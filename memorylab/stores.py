"""Cutoff-bound public corpus copies; never whole-controller SQLite backups."""
import copy
import hashlib
import os
import tempfile
from pathlib import Path

from .cards import CanonicalCards,EvidenceStore
from .contracts import (canonical,exact_object,identifier,namespace,reference,require,sha256)
from .kernel_cli import read_json
from .ledger import Ledger
from .registry import Registry
from .roles import SECRET
from .runtime import UsageStore


def store_config(path=None):
    c=copy.deepcopy(path) if type(path) is dict else read_json(path or Path(__file__).resolve().parent.parent/"configs/store_v0.json")
    exact_object(c,{"schema_version","status","hypothesis","max_sources","max_cards","max_usage","max_archived_indices",
        "max_specs","max_snapshot_bytes","purposes","derived_policy","index_credit_transfer","formal_admission","model_calls"},"invalid_store_config")
    require(c["schema_version"]=="corpus-store-v0" and c["purposes"]==["initial_checkpoint","shared_acquisition","research_shadow"] and
        c["derived_policy"]=="historical_reference_only_rebuild_active_l2" and c["index_credit_transfer"]=="none_no_causal_support_for_leaf_assignment" and
        c["formal_admission"] is False and type(c["model_calls"]) is int and c["model_calls"]==0,"unsupported_store_policy")
    for k in ("max_sources","max_cards","max_usage","max_archived_indices","max_specs","max_snapshot_bytes"):
        require(type(c[k]) is int and c[k]>0,"invalid_store_limit")
    return c


def portable(payload):
    """Comparable contents, not a permission grant or substitute for exact refs."""
    sources={canonical(row["ref"]):{k:row["source"][k] for k in
        ("event_type","episode","timestamp","visibility","text")} for row in payload["sources"]}
    cards={canonical(row["ref"]):row["card"] for row in payload["corpus"]["cards"]}
    indices={canonical(row["ref"]):row["index"] for row in payload["indices"]}
    def pointer(ref):
        if ref is None:return None
        key=canonical(ref)
        if ref["kind"]=="source":return {"source":sha256(sources[key])}
        if ref["kind"]=="card":return {"card":cards[key]["id"],"version":cards[key]["version"]}
        # An imported historical shell retains this identity. Old text stays
        # archived and cannot become the new mechanism's derived input.
        index=indices[key]
        original=index.get("original_ref",ref)
        return {"index":original,"supports":[pointer(r) for r in index["support_refs"]]}
    card_rows=[]
    for row in payload["corpus"]["cards"]:
        card=row["card"]
        card_rows.append({**{k:card[k] for k in ("id","version","type_hint","brief","detail","timestamp","outcome","status","extractor")},
            "evidence_refs":[pointer(r) for r in card["evidence_refs"]],"supersedes":[pointer(r) for r in card["supersedes"]]})
    usage=[{**{k:u[k] for k in ("episode","event_type","outcome")},"memory_ref":pointer(u["memory_ref"]),
        "evidence_ref":pointer(u["evidence_ref"])} for u in payload["usage"]]
    return sha256({"sources":list(sources.values()),"cards":card_rows,"usage":usage,"extractor":payload["corpus"]["extractor"]})


class CorpusStore:
    def __init__(self,cards,config=None):
        self.cards,self.ledger=cards,cards.ledger
        self.config=store_config(config)

    def _payload(self,ns,cutoff):
        corpus=self.cards.corpus(ns,cutoff)
        sources=[{"ref":r,"source":s} for r,s in self.cards.evidence.visible("extractor",ns,cutoff)]
        usage=UsageStore(self.cards).visible(ns,cutoff)
        require(len(sources)<=self.config["max_sources"] and len(corpus["cards"])<=self.config["max_cards"] and
            len(usage)<=self.config["max_usage"],"corpus_snapshot_count_limit")
        events=self.ledger.events(ns,cutoff);by_sequence={e["sequence"]:e for e in events}
        decisions=[e["data"]["receipt"] for e in events if e["data"].get("type")=="decision"]
        # A snapshot cannot cut between an input/usage event and its committing
        # decision, even if a later transaction made that receipt resolvable.
        for row in sources:
            seq=row["source"]["sequence"];request_id=by_sequence[seq]["data"]["source_request_id"]
            require(any(d["request_id"]==request_id for d in decisions),"uncommitted_snapshot_cutoff")
        card_refs={canonical(row["ref"]) for row in corpus["cards"]};indices={}
        for value in usage:
            expected={k:v for k,v in value.items() if k!="sequence"}
            matches=[d for d in decisions if d["result"].get("status")=="usage_recorded" and
                d["result"].get("sequence")==value["sequence"]]
            require(len(matches)==1 and self.ledger.receipt(matches[0]["request_id"],expected)==matches[0],"uncommitted_snapshot_usage")
            event=by_sequence[value["sequence"]]
            require(event["data"]=={"type":"usage","usage":expected} and event["request_id"]=="usage-"+sha256({"request_id":matches[0]["request_id"]}),"snapshot_usage_binding_mismatch")
            ref=value["memory_ref"]
            if ref is not None:
                reference(ref,{"card","index"});require(ref["namespace"]==ns,"cross_namespace_snapshot_usage")
                if ref["kind"]=="card":require(canonical(ref) in card_refs,"future_snapshot_usage_card")
                else:
                    index=self.ledger.get(ref)
                    require(type(index.get("support_refs")) is list and all(canonical(r) in card_refs for r in index["support_refs"]),"invalid_snapshot_index_support")
                    indices[canonical(ref)]={"ref":ref,"index":index}
            if value["evidence_ref"] is not None:self.cards.evidence.read(value["evidence_ref"],"extractor",ns,cutoff)
        require(len(indices)<=self.config["max_archived_indices"],"snapshot_index_limit")
        payload={"kernel_sha256":sha256(self.cards.kernel),"corpus":corpus,"sources":sources,"usage":usage,
            "indices":list(indices.values()),"store_config":self.config}
        encoded=canonical(payload)
        require(len(encoded)<=self.config["max_snapshot_bytes"],"corpus_snapshot_byte_limit")
        require(not SECRET.search(encoded),"corpus_snapshot_secret_material")
        return payload

    def capture(self,request_id,ns,cutoff):
        identifier(request_id)
        request={"namespace":ns,"cutoff":cutoff,"store_config_sha256":sha256(self.config)}
        with self.ledger.mutation():
            prior=self.ledger.receipt(request_id,request)
            if prior:return prior["result"]["snapshot"]
            payload=self._payload(ns,cutoff)
            ref=self.ledger.put({"kind":"corpus_snapshot","id":"corpus-"+sha256({"request_id":request_id}),"version":1,
                "request_id":request_id,"payload":payload,"portable_sha256":portable(payload),"formal_admission":False},"control")
            self.ledger.commit_receipt(request_id,request,{"status":"corpus_captured","snapshot":ref,"model_calls":0},"control")
            return ref

    def resolve(self,ref):
        reference(ref,{"corpus_snapshot"});require(ref["namespace"]=="control","invalid_corpus_snapshot_namespace")
        value=self.ledger.get(ref);p=value["payload"];c=p["corpus"]
        request={"namespace":c["namespace"],"cutoff":c["cutoff"],"store_config_sha256":sha256(p["store_config"])}
        receipt=self.ledger.receipt(value["request_id"],request)
        require(receipt is not None and receipt["result"].get("snapshot")==ref,"uncommitted_corpus_snapshot")
        require(p["store_config"]==self.config and self._payload(c["namespace"],c["cutoff"])==p and
            portable(p)==value["portable_sha256"],"corpus_snapshot_drift")
        return value

    @staticmethod
    def _purpose(source_ns,dest_ns,purpose,same_store):
        namespace(dest_ns)
        require(dest_ns!="control","invalid_import_namespace")
        if purpose=="shared_acquisition":
            require(source_ns.startswith("task/acq-") and dest_ns.startswith("task/") and source_ns!=dest_ns,"invalid_acquisition_import")
        elif purpose=="research_shadow":
            require(source_ns in ("research/evaluator","research/proposer") and dest_ns==source_ns and not same_store,"invalid_research_shadow_import")
        elif purpose=="initial_checkpoint":
            require(source_ns.startswith("task/") and dest_ns.startswith("task/") or source_ns==dest_ns and
                source_ns.startswith("research/") and not same_store,"invalid_initial_import")
        else:require(False,"unregistered_import_purpose")

    def import_snapshot(self,request_id,origin,ref,dest_ns,purpose,fault=None):
        identifier(request_id);value=origin.resolve(ref);p=value["payload"];source_ns=p["corpus"]["namespace"]
        self._purpose(source_ns,dest_ns,purpose,os.path.samefile(origin.ledger.path,self.ledger.path))
        require(p["kernel_sha256"]==sha256(self.cards.kernel) and p["store_config"]==self.config,"corpus_import_contract_mismatch")
        request={"snapshot":ref,"source_store":str(origin.ledger.path),"namespace":dest_ns,"purpose":purpose,"config":sha256(self.config)}
        def checkpoint(name):
            if fault:fault(name)
        with self.ledger.mutation():
            prior=self.ledger.receipt(request_id,request)
            if prior:return prior
            require(not self.ledger.events(dest_ns) and not any(self.ledger.objects(kind,dest_ns) for kind in ("source","card","index","projection")),"import_namespace_not_empty")
            mapping={};rows=[]
            for index,row in enumerate(p["sources"]):
                s=row["source"]
                new=self.cards.evidence.register("copy-source-"+sha256({"request":request_id,"index":index}),dest_ns,
                    s["event_type"],s["episode"],s["timestamp"],s["text"])
                mapping[canonical(row["ref"])]=new;rows.append({"original":row["ref"],"copied":new})
            checkpoint("sources")
            for index,row in enumerate(p["corpus"]["cards"]):
                card=row["card"]
                raw={**{k:card[k] for k in ("id","type_hint","brief","detail","outcome","status")},"expected_version":card["version"]-1,
                    "evidence_refs":[mapping[canonical(r)] for r in card["evidence_refs"]],"supersedes":[mapping[canonical(r)] for r in card["supersedes"]]}
                result=self.cards.extract("copy-card-"+sha256({"request":request_id,"index":index}),{"cards":[raw]},dest_ns,self.ledger.head(),card["timestamp"])["result"]
                require(result["status"]=="extracted",result["code"])
                new=result["cards"][0];mapping[canonical(row["ref"])]=new;rows.append({"original":row["ref"],"copied":new})
            checkpoint("cards")
            for row in p["indices"]:
                index=row["index"];original=index.get("original_ref",row["ref"])
                new=self.ledger.put({"kind":"index","id":index["id"],"version":index["version"],"namespace":dest_ns,
                    "historical_reference_only":True,"original_ref":original,"support_refs":[mapping[canonical(r)] for r in index["support_refs"]]},dest_ns)
                mapping[canonical(row["ref"])]=new;rows.append({"original":row["ref"],"copied":new})
            checkpoint("indices")
            usage=UsageStore(self.cards)
            for index,u in enumerate(p["usage"]):
                usage.record("copy-usage-"+sha256({"request":request_id,"index":index}),dest_ns,u["episode"],u["event_type"],
                    mapping[canonical(u["memory_ref"])] if u["memory_ref"] else None,u["outcome"],
                    mapping[canonical(u["evidence_ref"])] if u["evidence_ref"] else None)
            checkpoint("usage")
            cutoff=self.ledger.head();copied=self._payload(dest_ns,cutoff)
            require(portable(copied)==value["portable_sha256"],"corpus_import_content_mismatch")
            archive=self.ledger.put({"kind":"corpus_origin","id":"origin-"+sha256(request),"version":1,
                "snapshot":value,"source_store":str(origin.ledger.path),"mapping":rows},"control")
            result={"status":"corpus_imported","namespace":dest_ns,"cutoff":cutoff,"corpus_sha256":copied["corpus"]["sha256"],
                "portable_sha256":value["portable_sha256"],"origin":archive,"model_calls":0,"formal_admission":False}
            receipt=self.ledger.commit_receipt(request_id,request,result,"control");checkpoint("receipt")
            return receipt

    def fork(self,request_id,ref,destination,ns,registry,spec_refs,purpose="research_shadow",fault=None):
        """Publish a separate scoped DB without changing its source store."""
        identifier(request_id);destination=Path(destination)
        require(not destination.is_symlink() and destination.parent.is_dir(),"invalid_fork_destination")
        require(not destination.exists() or not os.path.samefile(destination,self.ledger.path),"fork_requires_separate_store")
        require(registry.ledger is self.ledger and type(spec_refs) is list,"fork_registry_mismatch")
        value=self.resolve(ref);self._purpose(value["payload"]["corpus"]["namespace"],ns,purpose,False)
        specs={};visiting=set()
        def collect(r):
            key=canonical(r)
            if key in specs:return
            require(key not in visiting and len(specs)+len(visiting)<self.config["max_specs"],"fork_spec_lineage_limit")
            visiting.add(key);spec=registry.resolve(r)
            if spec["parent"] is not None:collect(spec["parent"])
            visiting.remove(key);specs[key]=(r,spec)
        for r in spec_refs:collect(r)
        recipe={"snapshot":ref,"source_store":str(self.ledger.path),"namespace":ns,"purpose":purpose,
            "spec_refs":spec_refs,"kernel_sha256":sha256(self.cards.kernel),"store_config":self.config,"request_id":request_id}
        binding={"kind":"store_binding","id":"fork-binding","version":1,"recipe":recipe}
        binding_ref={"kind":"store_binding","id":"fork-binding","version":1,"namespace":"control","sha256":sha256(binding)}
        import_id="fork-import-"+sha256(recipe)
        if destination.exists():
            require(destination.is_file(),"invalid_fork_destination")
            with Ledger(destination) as ledger:
                require(ledger.get(binding_ref)==binding,"fork_identity_drift")
                receipt=ledger.receipt(import_id,{"snapshot":ref,"source_store":str(self.ledger.path),"namespace":ns,
                    "purpose":purpose,"config":sha256(self.config)})
                require(receipt is not None,"incomplete_store_fork")
                copied=CorpusStore(CanonicalCards(EvidenceStore(ledger,self.cards.kernel)),self.config)
                require(portable(copied._payload(ns,receipt["result"]["cutoff"]))==value["portable_sha256"],"fork_corpus_drift")
                ledger.audit()
                for r,spec in specs.values():require(ledger.get(r)==spec,"fork_spec_drift")
            return {"path":str(destination),"binding":binding_ref,"import":receipt,"cache_hit":True,"model_calls":0}
        fd,temp=tempfile.mkstemp(prefix=".store-pending-",dir=destination.parent);os.close(fd);staging=Path(temp)
        try:
            with Ledger(staging) as ledger:
                copied_registry=Registry(ledger,self.cards.kernel)
                cards=CanonicalCards(EvidenceStore(ledger,self.cards.kernel));store=CorpusStore(cards,self.config)
                with ledger.transaction():
                    ledger.put(binding,"control")
                    for r,spec in specs.values():require(ledger.put(spec,"control")==r,"fork_spec_drift")
                    receipt=store.import_snapshot(import_id,self,ref,ns,purpose,fault)
                ledger.audit()
            with staging.open("rb") as stream:os.fsync(stream.fileno())
            if fault:fault("before_publish")
            # link refuses an already-existing destination, unlike replace.
            os.link(staging,destination);staging.unlink()
            directory_fd=os.open(destination.parent,os.O_RDONLY)
            try:os.fsync(directory_fd)
            finally:os.close(directory_fd)
            return {"path":str(destination),"binding":binding_ref,"import":receipt,"cache_hit":False,"model_calls":0,
                "database_sha256":hashlib.sha256(destination.read_bytes()).hexdigest()}
        finally:
            for path in (staging,Path(str(staging)+"-wal"),Path(str(staging)+"-shm")):path.unlink(missing_ok=True)

"""Controller-only, receipt-bound inputs. No actor invocation or formal proof."""
import copy
import hashlib
import json
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .cards import CanonicalCards,EvidenceStore
from .contracts import canonical,exact_object,reference,require,sha256
from .derived import DerivedStage
from .freeze import implementation_files
from .kernel_cli import read_json
from .ledger import Ledger
from .registry import Registry
from .roles import RoleInputs,role_config,validate_role
from .runtime import CLIENT as MEMORY_CLIENT,RuntimeBuilder,RuntimeSession
from .scenario import CLIENT as SCENARIO_CLIENT,materialize,read_scenario
from .scheduling import compile_schedule,validate_graph
from .stage_environment import stage_environment_identity
from .stores import CorpusStore
from .workspace import workspace_snapshot


def input_config(path=None):
    c=read_json(path or Path(__file__).resolve().parent.parent/"configs/stage_input_v0.json")
    exact_object(c,{"schema_version","hypothesis","max_input_bytes","ledger_decode_cache_bytes","source_episode","graph_authority","cache_policy",
        "controller_graph_policy","environment_policy","controller_boundary_policy","shadow_policy","action_policy","formal_admission","model_calls",
        "validation_scope_policy","validation_memo_max_bytes"},"invalid_stage_input_config")
    require(c["schema_version"]=="stage-input-v0" and c["source_episode"]=="case_slot_with_registered_producer_phase" and
        c["graph_authority"]=="recompile_and_compare_exactly" and c["cache_policy"]=="same_bound_input_and_cutoff" and
        c["controller_graph_policy"]=="one_execution_graph_per_store" and
        c["environment_policy"]=="revalidate_python_os_native_sandbox_and_filtered_worker_environment_per_operation" and
        c["controller_boundary_policy"]=="bound_extra_origin_roots_for_task_role_and_production" and
        c["shadow_policy"]=="preproposal_corpus_separate_store" and
        c["action_policy"]=="derive_disabled_noeligible_or_execute_from_actual_inputs" and c["formal_admission"] is False and
        type(c["model_calls"]) is int and c["model_calls"]==0 and
        c["validation_scope_policy"]=="one_completed_validation_pass_transaction_reset_on_database_changes","unsupported_stage_input_policy")
    require(all(type(c[k]) is int and c[k]>0 for k in ("max_input_bytes","ledger_decode_cache_bytes","validation_memo_max_bytes")),"invalid_stage_input_limit")
    return c


class StageInputs:
    def __init__(self,cards,registry,journal,initial_snapshot,output,research_snapshots=None,role=None,scenario=None,denied_roots=()):
        self.cards,self.registry,self.journal,self.ledger=cards,registry,journal,cards.ledger
        require(registry.ledger is self.ledger and journal.ledger is self.ledger,"stage_binding_store_mismatch")
        self.graph=copy.deepcopy(validate_graph(journal.graph));self.plan=self.graph["sha256"]
        self.contract=copy.deepcopy(scenario if scenario is not None else read_scenario())
        g=self.graph
        rebuilt=compile_schedule(registry,g["memory_refs"],g["scenario_refs"],g["seeds"],g["initial_corpus_sha256"],
            g["mode"],g["config"],g["execution"],self.contract,g["meta_memory_ref"],g["shadow_memory_ref"],g["research_corpora"])
        require(rebuilt==g,"stage_bindings_not_compiled")
        self.store=CorpusStore(cards);self.initial=initial_snapshot;self.research=copy.deepcopy(research_snapshots or {})
        base=self.store.resolve(initial_snapshot)["payload"]["corpus"]
        require(base["namespace"].startswith("task/") and base["sha256"]==g["initial_corpus_sha256"],"initial_stage_corpus_mismatch")
        require(set(self.research)==({"evaluator","proposer"} if g["meta_memory_ref"] else set()),"research_stage_snapshots_mismatch")
        for role_name,ref in self.research.items():
            c=self.store.resolve(ref)["payload"]["corpus"]
            require(c["namespace"]=="research/"+role_name and c["sha256"]==g["research_corpora"][role_name],"research_stage_corpus_mismatch")
        self.role=copy.deepcopy(validate_role(role) if role is not None else role_config());self.config=input_config()
        output=Path(output);require(not output.is_symlink(),"invalid_stage_store_root");output.mkdir(parents=True,exist_ok=True)
        self.output=output.resolve();self.nodes={n["id"]:n for n in g["slots"]}
        require(type(denied_roots) in (list,tuple) and len(denied_roots)<=16 and
            all(isinstance(p,(str,Path)) and Path(p).is_absolute() for p in denied_roots),"invalid_stage_controller_roots")
        self.denied_roots=tuple(sorted({str(Path(p).resolve()) for p in denied_roots}))
        implementation=sha256({name:hashlib.sha256(data).hexdigest() for name,data in implementation_files().items()})
        self.binding={"kind":"stage_binding","id":"binding-"+self.plan,"version":1,"plan":self.plan,
            "initial_snapshot":self.initial,"research_snapshots":self.research,"store_root":str(self.output),
            "role_config":self.role,"scenario_contract":self.contract,"input_config":self.config,
            "implementation_sha256":implementation,"environment":stage_environment_identity(),"denied_roots":list(self.denied_roots),"category":"infrastructure_acceptance"}
        self.bind_request="bind-"+self.plan
        with self.ledger.mutation():
            require(all(v["plan"]==self.plan for _,v in self.ledger.objects("stage_binding","control")),"stage_controller_graph_isolation")
            self.binding_ref=self.ledger.put(self.binding,"control")
            request={"binding":self.binding}
            if not self.ledger.receipt(self.bind_request,request):
                self.ledger.commit_receipt(self.bind_request,request,{"status":"stage_inputs_bound","binding":self.binding_ref},"control")
        self.validation_session=None
        self._reset()

    def preflight(self):
        """Recheck even for control/cache/no-op paths before any new mutation."""
        require(self.ledger.get(self.binding_ref)==self.binding,"stage_binding_drift")
        receipt=self.ledger.receipt(self.bind_request,{"binding":self.binding})
        require(receipt is not None and receipt["result"].get("binding")==self.binding_ref,"uncommitted_stage_binding")
        require(self.config==self.binding["input_config"] and self.role==self.binding["role_config"] and
            self.contract==self.binding["scenario_contract"] and self.graph==self.journal.graph,"stage_binding_drift")
        require(list(self.denied_roots)==self.binding["denied_roots"] and
            all(str(Path(p).resolve())==p for p in self.denied_roots),"stage_controller_roots_drift")
        require(stage_environment_identity()==self.binding["environment"],"stage_environment_drift")
        implementation=sha256({name:hashlib.sha256(data).hexdigest() for name,data in implementation_files().items()})
        require(implementation==self.binding["implementation_sha256"],"stage_implementation_drift")

    def _reset(self):
        self.finished={};self.starts={}
        for event in self.ledger.events("control"):
            receipt=event["data"].get("receipt",{});r=receipt.get("result",{})
            if r.get("status")=="stage_finished" and r.get("plan")==self.plan:
                require(r["slot"] not in self.finished,"duplicate_stage_completion")
                self.finished[r["slot"]]=(event["sequence"],receipt)
            if r.get("status")=="stage_started":
                self.starts.setdefault(canonical(r["attempt"]),[]).append(event)
        self.memo={};self.checking=set();self.dependencies={};self.snapshots={};self.memo_bytes=0

    def refresh(self):
        """A cache generation belongs to one unchanged controller transaction."""
        if self.validation_session is None:
            self._reset();return
        require(self.ledger.db.in_transaction,"stage_validation_transaction_lost")
        changes=self.ledger.db.total_changes
        if changes!=self.validation_session["changes"]:
            self._reset();self.validation_session["changes"]=changes

    @contextmanager
    def validation_scope(self):
        require(self.ledger.db.in_transaction,"stage_validation_transaction_required")
        require(self.validation_session is None,"nested_stage_validation_scope")
        self._reset();self.validation_session={"changes":self.ledger.db.total_changes}
        try:yield self
        finally:
            self.validation_session=None
            self.finished={};self.starts={};self.memo={};self.checking=set();self.dependencies={};self.snapshots={};self.memo_bytes=0

    def _remember(self,cache,key,value):
        size=len(canonical(list(value) if type(value) is tuple else value))
        if size<=self.config["validation_memo_max_bytes"]-self.memo_bytes:
            cache[key]=value;self.memo_bytes+=size

    def _input(self,ref):
        reference(ref,{"stage_input"});require(ref["namespace"]=="control","invalid_stage_input_namespace")
        key=canonical(ref)
        if key in self.memo:return self.memo[key]
        require(key not in self.checking,"cyclic_stage_input_dependency");self.checking.add(key)
        try:
            value=self.ledger.get(ref);slot=value["slot"]
            receipt=self.ledger.receipt(value["request_id"],{"binding":self.binding_ref,"slot":slot})
            require(receipt is not None and receipt["result"].get("input")==ref,"uncommitted_stage_input")
            require(value["binding"]==self.binding_ref and value["payload"]==self._compose(slot),"stage_input_snapshot_drift")
            require(value["input_sha256"]==sha256(value["payload"]),"stage_input_hash_drift")
            self._remember(self.memo,key,value);return value
        finally:self.checking.remove(key)

    def _dependency(self,slot):
        if slot in self.dependencies:return self.dependencies[slot]
        require(slot in self.finished,"stage_dependencies_unavailable")
        sequence,receipt=self.finished[slot];r=receipt["result"];node=self.nodes[slot]
        require(self.ledger.receipt(receipt["request_id"])==receipt,"uncommitted_stage_completion")
        if r["outcome"] in ("disabled","blocked"):
            require(receipt["request_sha256"]==sha256({"plan":self.plan,"slot":slot,"action":"close_unrun"}),"unbound_stage_skip")
            if r["outcome"]=="disabled":require(node["condition"]=="disabled","unbound_stage_skip")
            else:
                require(any(d in self.finished and not self.journal._accepts_dependency(node,self.finished[d][1]["result"])
                    for d in node["depends_on"]),"unbound_stage_skip")
            artifacts={};begin_sequence=None
        else:
            reference(r["attempt"],{"stage_attempt"});attempt=self.ledger.get(r["attempt"])
            require(attempt["plan"]==self.plan and attempt["slot"]==slot and attempt["input_sha256"]==r["input_sha256"] and
                "bound_input" in attempt,"unbound_stage_attempt")
            bound=self._input(attempt["bound_input"])
            require(bound["slot"]==slot and bound["input_sha256"]==attempt["input_sha256"],"stage_attempt_input_mismatch")
            starts=[e for e in self.starts.get(canonical(r["attempt"]),[]) if e["sequence"]<=sequence]
            require(len(starts)==1,"uncommitted_stage_attempt")
            begin_sequence=starts[0]["sequence"]
            start_request={k:attempt[k] for k in ("plan","slot","input_sha256","mode","bound_input")}
            require(starts[0]["data"]["receipt"]["request_sha256"]==sha256(start_request),"stage_begin_receipt_mismatch")
            expected_calls=0 if bound["payload"]["action"]=="bound_noop" else node["model_invocations_max"]
            expected_tokens=0 if bound["payload"]["action"]=="bound_noop" else node["budget"]["input_tokens"]+node["budget"]["output_tokens"]
            require(attempt["reserved_calls"]==expected_calls and attempt["reserved_tokens"]==expected_tokens,"stage_reservation_input_mismatch")
            artifact=self.ledger.get(r["artifact"])
            require(artifact["attempt"]==r["attempt"] and artifact["category"]=="infrastructure_acceptance","stage_artifact_attempt_mismatch")
            request={"attempt":r["attempt"],"outcome":r["requested_outcome"],"simulated_usage":r["simulated_usage"],
                "raw":artifact["raw"],"artifacts":artifact["artifacts"]}
            require(receipt["request_sha256"]==sha256(request),"stage_artifact_receipt_mismatch")
            artifacts=artifact["artifacts"]
        result={"slot":slot,"sequence":sequence,"begin_sequence":begin_sequence,"receipt_sha256":sha256(receipt),"result":r,"artifacts":artifacts}
        self._remember(self.dependencies,slot,result);return result

    def _stage_snapshot(self,slot):
        if slot in self.snapshots:return self.snapshots[slot]
        d=self._dependency(slot);ref=d["artifacts"].get("snapshot")
        require(self.nodes[slot]["role"] in ("extractor","corpus","rebuild"),"invalid_stage_snapshot_producer")
        require(ref is not None,"missing_stage_snapshot")
        value=self.store.resolve(ref);c=value["payload"]["corpus"]
        require(c["namespace"]==self.nodes[slot]["namespace"] and c["cutoff"]<=d["sequence"],"stage_snapshot_scope_mismatch")
        capture=[e["sequence"] for e in self.ledger.events("control",d["sequence"]) if e["request_id"]==value["request_id"]]
        require(len(capture)==1,"future_stage_snapshot")
        current=self.store._payload(c["namespace"],d["sequence"])
        require(current["corpus"]["cards"]==c["cards"] and all(current[k]==value["payload"][k] for k in ("sources","usage","indices")),
            "incomplete_stage_snapshot")
        self._remember(self.snapshots,slot,(ref,c));return ref,c

    def _base(self,node):
        b=node["bindings"];ns=node["namespace"]
        producer=b.get("corpus_receipt") or b.get("prior_corpus_receipt") or b.get("extraction_receipt") or b.get("runtime_receipt")
        if producer:
            ref,c=self._stage_snapshot(producer)
            if c["namespace"]==ns:return ref,c
            require(self.graph["mode"]=="component_comparison" and self.nodes[producer]["role"]=="corpus" and
                self.nodes[producer]["namespace"].startswith("task/acq-") and ns.startswith("task/"),"unregistered_stage_corpus_transfer")
            purpose="shared_acquisition"
        elif ns.startswith("research/"):
            ref=self.research[ns.split("/")[1]];return ref,self.store.resolve(ref)["payload"]["corpus"]
        else:
            ref=self.initial;c=self.store.resolve(ref)["payload"]["corpus"];purpose="initial_checkpoint"
        request="install-"+sha256({"binding":self.binding_ref,"namespace":ns,"snapshot":ref,"purpose":purpose})
        copied=self.store.import_snapshot(request,self.store,ref,ns,purpose)["result"]
        snapshot=self.store.capture("installed-"+sha256({"install":request}),ns,copied["cutoff"])
        return snapshot,self.store.resolve(snapshot)["payload"]["corpus"]

    @contextmanager
    def _scope(self,node):
        if node["store_scope"]=="main":
            yield self.cards,self.registry,self.store,None,None;return
        role=node["namespace"].split("/")[1];scope=node["store_scope"]
        corpus_slot=role+"-history-corpus";snapshot,c=self._stage_snapshot(corpus_slot)
        destination=self.output/(scope+".sqlite")
        memory=node["bindings"]["memory"]
        parent=self.graph["scenario_refs"][0] if role=="evaluator" else self.graph["memory_refs"][0]
        # All nodes in this scope must share a recipe, including its chosen M.
        fork=self.store.fork("scope-"+sha256({"plan":self.plan,"scope":scope}),snapshot,destination,node["namespace"],
            self.registry,[memory,parent])
        with Ledger(destination) as ledger:
            cards=CanonicalCards(EvidenceStore(ledger,self.cards.kernel));registry=Registry(ledger,self.cards.kernel);store=CorpusStore(cards)
            imported=fork["import"]["result"]
            ref=store.capture("scope-corpus-"+sha256({"scope":scope,"plan":self.plan}),node["namespace"],imported["cutoff"])
            mapping={canonical(row["original"]):row["copied"] for row in ledger.get(imported["origin"])["mapping"]}
            yield cards,registry,store,(ref,store.resolve(ref)["payload"]["corpus"]),{"path":str(destination),"binding":fork["binding"],"mapping":mapping}

    def _sources(self,producer,ns,cutoff,cards,scope):
        d=self._dependency(producer);refs=d["artifacts"].get("observable_sources")
        require(type(refs) is list and len({canonical(r) for r in refs})==len(refs),"missing_or_duplicate_stage_sources")
        result=[]
        node=self.nodes[producer]
        episode=node["bindings"]["solver_receipt"] if node["role"]=="grade" else node["bindings"]["proposal_receipt"] if node["role"]=="proposal_validate" else producer
        for ref in refs:
            source=self.cards.evidence.read(ref,"extractor",ns,d["sequence"])
            require(source["episode"]==episode,"source_producer_stage_mismatch")
            require(d["begin_sequence"] is not None and source["sequence"]>d["begin_sequence"],"source_predates_producer_attempt")
            commits=[e["sequence"] for e in self.ledger.events(ns,d["sequence"]) if
                e["data"].get("receipt",{}).get("result",{}).get("source")==ref]
            require(len(commits)==1,"future_or_uncommitted_stage_source")
            copied=scope["mapping"].get(canonical(ref)) if scope else ref
            require(copied is not None,"source_missing_from_shadow_snapshot")
            cards.evidence.read(copied,"extractor",ns,cutoff);result.append(copied)
        return result

    def _projection(self,producer,cards,registry,corpus,memory):
        d=self._dependency(producer);art=d["artifacts"]
        require(art.get("snapshot")==corpus[0] and art.get("cutoff")==corpus[1]["cutoff"],"runtime_snapshot_mismatch")
        derived=art.get("derived_refs")
        require(type(derived) is dict,"missing_runtime_derived_bindings")
        bound=self._input(self.ledger.get(d["result"]["attempt"])["bound_input"])
        require(derived==bound["payload"]["resources"]["derived_refs"],"runtime_derived_producer_mismatch")
        expected=RuntimeBuilder(cards,registry).build(corpus[1]["namespace"],corpus[1]["cutoff"],memory,derived)
        require(art.get("projection")==expected["projection_ref"],"runtime_projection_mismatch")
        return art["projection"]

    def _compose(self,slot):
        require(slot in self.nodes,"unplanned_stage");node=self.nodes[slot];b=node["bindings"];role=node["role"];ns=node["namespace"]
        if node["condition"]=="disabled":
            return {"plan":self.plan,"slot":slot,"role":role,"namespace":ns,"store_scope":node["store_scope"],
                "action":"disabled","cutoff":0,"dependencies":[],"resources":{},"packet":None,"budget":node["budget"]}
        deps=[self._dependency(d) for d in node["depends_on"]]
        require(all(self.journal._accepts_dependency(node,d["result"]) for d in deps),"stage_dependencies_unavailable")
        proofs=[{k:d[k] for k in ("slot","sequence","receipt_sha256")} for d in deps]
        if ns=="control":
            return {"plan":self.plan,"slot":slot,"role":role,"namespace":ns,"store_scope":node["store_scope"],"action":"control",
                "cutoff":max((d["sequence"] for d in deps),default=0),"dependencies":proofs,
                "resources":{"bindings":b,"artifacts":[{"slot":d["slot"],"artifact":d["result"].get("artifact")} for d in deps]},
                "packet":None,"budget":node["budget"]}
        with self._scope(node) as (cards,registry,store,shadow_corpus,scope):
            inputs=RoleInputs(cards,registry,self.role);resources={};packet=None;action="execute"
            if role=="research_view":
                corpus=self._base(node);cutoff=corpus[1]["cutoff"]
                resources={"initial_snapshot":corpus[0],"report_artifact":deps[0]["result"].get("artifact"),"parent":b["parent"]};action="control"
            elif role=="oracle":corpus=None;cutoff=0
            else:
                corpus=shadow_corpus or self._base(node);cutoff=corpus[1]["cutoff"]
                resources={"snapshot":corpus[0],"memory":b.get("memory"),"corpus_sha256":corpus[1]["sha256"]}
            if role=="extractor":
                cutoff=max([cutoff]+[d["sequence"] for d in deps])
                require(cards.corpus(ns,cutoff)["cards"]==corpus[1]["cards"],"unexpected_stage_canonical_change")
                producers=[b["public_source_receipt"]]+[d["slot"] for d in deps if self.nodes[d["slot"]]["role"] in ("grade","proposal_validate")]
                refs=[]
                for producer in producers:refs.extend(self._sources(producer,ns,cutoff,cards,scope))
                packet,_=inputs.extractor(ns,cutoff,refs);resources["source_refs"]=refs
            elif role in ("consolidation","reflector"):
                packet,_=inputs.derived(role,ns,cutoff,b["memory"])
                if not packet["payload"]["context"]["derived_input"]["payload"]["scheduled"]:action="bound_noop"
            elif role=="corpus":action="control"
            elif role=="rebuild":
                derived={}
                for producer in b["derived_receipts"]:
                    d=self._dependency(producer)
                    if d["result"]["outcome"]!="disabled":
                        batch=d["artifacts"].get("batch");require(batch is not None,"missing_derived_stage_batch")
                        DerivedStage(cards,registry).resolve(batch,self.nodes[producer]["role"],ns,cutoff,b["memory"])
                        derived[self.nodes[producer]["role"]]=batch
                resources["derived_refs"]=derived;action="control"
            elif role in ("solver","acquisition","oracle"):
                with tempfile.TemporaryDirectory(prefix="stage-task-template-") as temporary:
                    root=Path(temporary);materialize(root,b["episode"])
                    (root/"open_memory.py").write_text(MEMORY_CLIENT);(root/"scenario.py").write_text(SCENARIO_CLIENT)
                    briefs={"items":[]}
                    if b["memory"]:
                        producer=b.get("runtime_receipt") or next(d["slot"] for d in deps if self.nodes[d["slot"]]["role"]=="rebuild")
                        projection=self._projection(producer,cards,registry,corpus,b["memory"])
                        session=RuntimeSession(cards.ledger,projection,(root/"SPEC.md").read_text());briefs=json.loads(session.briefs())
                        resources["projection"]=projection
                    packet=inputs.task(role,ns,cutoff,root,b["episode"],briefs)
                    (root/"role.schema.json").write_bytes(canonical(packet["payload"]["output_schema"])+b"\n")
                    resources.update(episode=b["episode"],task_files={name:data.decode() for name,data in workspace_snapshot(root).items()})
            elif role in ("evaluator","proposer","shadow"):
                research_role=b.get("research_role",role);producer=b["research_view_receipt"]
                refs=self._sources(producer,ns,cutoff,cards,scope)
                parent=self.graph["scenario_refs"][0] if research_role=="evaluator" else self.graph["memory_refs"][0]
                projection=self._projection(b["runtime_receipt"],cards,registry,corpus,b["memory"])
                context=canonical([cards.evidence.read(r,research_role,ns,cutoff) for r in refs]).decode()
                briefs=json.loads(RuntimeSession(cards.ledger,projection,context).briefs())
                packet=inputs.research(research_role,ns,cutoff,parent,refs,briefs)
                resources.update(parent=parent,source_refs=refs,projection=projection)
            if scope:resources["store_binding"]={"path":scope["path"],"binding":scope["binding"]}
            return {"plan":self.plan,"slot":slot,"role":role,"namespace":ns,"store_scope":node["store_scope"],"action":action,
                "cutoff":cutoff,"dependencies":proofs,"resources":resources,"packet":packet,"budget":node["budget"]}

    def prepare(self,slot,fault=None):
        request={"binding":self.binding_ref,"slot":slot};request_id="input-"+sha256(request)
        with self.ledger.mutation(),self.ledger.cached_reads(self.config["ledger_decode_cache_bytes"]):
            self.preflight()
            self.refresh()
            prior=self.ledger.receipt(request_id,request)
            if prior:self._input(prior["result"]["input"]);return prior["result"]["input"]
            payload=self._compose(slot);require(len(canonical(payload))<=self.config["max_input_bytes"],"stage_input_overflow")
            ref=self.ledger.put({"kind":"stage_input","id":"input-"+sha256(request),"version":1,"request_id":request_id,
                "slot":slot,"binding":self.binding_ref,"payload":payload,"input_sha256":sha256(payload)},"control")
            if fault:fault("input")
            self.ledger.commit_receipt(request_id,request,{"status":"stage_input_ready","input":ref,"model_calls":0},"control")
            if fault:fault("receipt")
            return ref

    def resolve(self,ref):
        with self.ledger.mutation(),self.ledger.cached_reads(self.config["ledger_decode_cache_bytes"]):
            self.preflight();self.refresh();return copy.deepcopy(self._input(ref))

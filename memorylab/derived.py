"""Bounded role-specific L2 inputs and immutable generated-response admission."""
import copy
from pathlib import Path

from .contracts import (ContractError, bounded_text, canonical, exact_object,
                        identifier, reference, references, require, sha256)
from .kernel_cli import parse_json, read_json
from .runtime import read_runtime,validate_runtime


def read_derived(path=None):
    config=read_json(path or Path(__file__).resolve().parent.parent/"configs/derived_v0.json")
    return validate_derived(config)


def validate_derived(config):
    exact_object(config,{"schema_version","status","hypothesis","model","source_input","max_input_bytes","max_output_bytes","max_indices","root_type_hint",
        "index_brief_bytes","index_detail_bytes","annotation_bytes","consolidation_prompt","reflection_prompt","live_execution_authorized"},"invalid_derived_contract")
    require(config["schema_version"]=="derived-contract-v0" and config["live_execution_authorized"] is False,"unsupported_derived_contract")
    require(config["root_type_hint"]=="root","unregistered_derived_root")
    require(config["source_input"]=="progressive_handles","unsupported_derived_source_input")
    for key in ("max_input_bytes","max_output_bytes","max_indices","index_brief_bytes","index_detail_bytes","annotation_bytes"):
        require(type(config[key]) is int and config[key]>0,"invalid_derived_limit")
    for key in ("consolidation_prompt","reflection_prompt"):bounded_text(config[key],16000,"invalid_derived_prompt")
    require(config["model"] is None or type(config["model"]) is str and bool(config["model"].strip()),"invalid_derived_model")
    return config


def ancestor(parent,child):
    return parent=="root" or child==parent or child.startswith(parent+"/")


class DerivedStage:
    def __init__(self,cards,registry,runtime=None,config=None):
        self.cards,self.registry,self.ledger=cards,registry,cards.ledger
        self.runtime=validate_runtime(runtime if runtime is not None else read_runtime())
        self.config=validate_derived(config if config is not None else read_derived())

    def prepare(self,role,ns,cutoff,spec_ref):
        require(role in ("consolidation","reflector"),"invalid_derived_role")
        reference(spec_ref,{"memory"});spec=self.registry.resolve(spec_ref)
        corpus=self.cards.corpus(ns,cutoff);active=self.cards.active(ns,cutoff)
        eligible=[];rows=[];sources={}
        for ref,card in active:
            current_sources=[]
            for source_ref in card["evidence_refs"]:
                source=self.cards.evidence.read(source_ref,"reflector",ns,cutoff)
                source_key=sha256(source_ref)
                sources[source_key]={"ref":source_ref,"event_type":source["event_type"],"episode":source["episode"],
                    "timestamp":source["timestamp"],"text_sha256":source["sha256"],
                    "text_bytes":len(source["text"].encode("utf-8")),"text_chars":len(source["text"])}
                current_sources.append(sources[source_key])
            trigger=spec["config"]["reflection.trigger"]
            if trigger=="failure" and card["outcome"]=="failure" or trigger=="feedback" and any(s["event_type"]=="explicit_feedback" for s in current_sources):
                eligible.append(ref)
            rows.append({"ref":ref,"type_hint":card["type_hint"],"brief":card["brief"],"detail":card["detail"],
                "timestamp":card["timestamp"],"outcome":card["outcome"],"evidence_refs":card["evidence_refs"]})
        scheduled=spec["config"]["consolidation.type"]=="single_index" and len(active)>=self.runtime["index_min_support"] if role=="consolidation" else bool(eligible)
        prompt=self.config["consolidation_prompt" if role=="consolidation" else "reflection_prompt"]
        payload={"role":role,"namespace":ns,"cutoff":cutoff,"spec":spec_ref,"spec_config":spec["config"],
            "kernel_sha256":self.registry.fingerprint,"corpus_sha256":corpus["sha256"],"runtime_sha256":sha256(self.runtime),
            "generator_sha256":sha256(self.config),"prompt":prompt,"model":self.config["model"],"scheduled":scheduled,
            "cards":rows if scheduled else [],"eligible_refs":eligible if role=="reflector" and scheduled else [],
            "sources":list(sources.values()) if scheduled else []}
        require(len(canonical(payload))<=self.config["max_input_bytes"],"derived_input_overflow")
        return {"payload":payload,"sha256":sha256(payload)}

    def _validate_output(self,input_,response):
        payload=input_["payload"];role=payload["role"];key="indices" if role=="consolidation" else "annotations"
        exact_object(response,{key},"invalid_derived_output_fields")
        values=response[key];limit=self.config["max_indices"] if role=="consolidation" else self.runtime["reflection_max_annotations"]
        require(type(values) is list and len(values)<=limit,"too_many_derived_items")
        require(payload["scheduled"] or not values,"derived_stage_disabled")
        active={canonical(row["ref"]):row for row in payload["cards"]};eligible={canonical(r) for r in payload["eligible_refs"]}
        seen=set();result=[]
        for item in values:
            if role=="reflector":
                exact_object(item,{"support","text"},"invalid_reflection_annotation")
                reference(item["support"],{"card"});bounded_text(item["text"],self.config["annotation_bytes"],"annotation_overflow")
                require(canonical(item["support"]) in active and canonical(item["support"]) in eligible,"ineligible_reflection_support")
                identity=canonical(item["support"])
            else:
                exact_object(item,{"type_hint","brief","detail","support_refs"},"invalid_generated_index")
                references(item["support_refs"],{"card"},self.runtime["index_max_support"],self.runtime["index_min_support"])
                bounded_text(item["brief"],self.config["index_brief_bytes"],"index_brief_overflow")
                bounded_text(item["detail"],self.config["index_detail_bytes"],"index_detail_overflow")
                require(type(item["type_hint"]) is str and (item["type_hint"]==self.config["root_type_hint"] or
                    item["type_hint"] in self.cards.kernel["taxonomy"]),"unregistered_index_type")
                supported=[]
                for ref in item["support_refs"]:
                    require(ref["namespace"]==payload["namespace"],"cross_namespace_index_support")
                    require(canonical(ref) in active,"inactive_index_support")
                    supported.append(active[canonical(ref)])
                require(all(ancestor(item["type_hint"],r["type_hint"]) for r in supported),"invalid_index_ancestor")
                if payload["spec_config"]["organizer.type"]=="fixed_tree":
                    require(len({r["type_hint"].split("/")[0] for r in supported})==1,"cross_group_tree_index")
                identity=canonical(sorted(item["support_refs"],key=lambda r:canonical(r)))
            require(identity not in seen,"duplicate_derived_support_set");seen.add(identity);result.append(copy.deepcopy(item))
        return result

    def accept(self,request_id,input_,raw_response,invocation):
        identifier(request_id)
        exact_object(input_,{"payload","sha256"},"invalid_derived_input")
        require(sha256(input_["payload"])==input_["sha256"],"derived_input_drift")
        bounded_text(raw_response,self.config["max_output_bytes"],"derived_output_overflow")
        payload=input_["payload"]
        require(self.prepare(payload["role"],payload["namespace"],payload["cutoff"],payload["spec"])==input_,"derived_input_snapshot_mismatch")
        request={"input":input_,"raw_response":raw_response,"invocation":invocation}
        with self.ledger.mutation():
            cached=self.ledger.receipt(request_id,request)
            if cached:return cached
            raw=self.ledger.put({"kind":"derived_raw","id":"derived-raw-"+sha256(request),"version":1,
                "input":input_,"raw_response":raw_response,"invocation":invocation},"control")
            try:
                exact_object(invocation,{"mode","input_sha256","model","model_calls","input_tokens","output_tokens"},"invalid_derived_invocation")
                require(invocation["mode"]=="scripted_acceptance","unvalidated_live_derived_invocation")
                require(invocation["input_sha256"]==input_["sha256"],"derived_invocation_input_mismatch")
                require(invocation["model"]=="explicit-offline-fixture" and type(invocation["model_calls"]) is int and invocation["model_calls"]==0 and
                    invocation["input_tokens"] is None and invocation["output_tokens"] is None,"invalid_scripted_derived_usage")
                response=parse_json(raw_response);items=self._validate_output(input_,response)
            except (ContractError,ValueError,UnicodeError) as exc:
                result={"status":"rejected","code":getattr(exc,"code","invalid_derived_json"),"raw":raw,"batch":None,"model_calls":0,
                    "category":"infrastructure_acceptance","semantic_validation":"not_established"}
            else:
                batch=self.ledger.put({"kind":"derived_batch","id":"derived-batch-"+sha256({"request_id":request_id,"request":request}),"version":1,
                    "namespace":payload["namespace"],"input":input_,"items":items,"raw":raw,"invocation":invocation,"request_id":request_id},payload["namespace"])
                result={"status":"accepted" if items else "noop","code":"structurally_valid","raw":raw,"batch":batch,"model_calls":0,
                    "category":"infrastructure_acceptance","semantic_validation":"not_established"}
            return self.ledger.commit_receipt(request_id,request,result,"control")

    def _accept_capture(self,request_id,input_,capture_ref,capture):
        """Join RoleAdmission's atomic unit; never accept self-filled live usage."""
        self.ledger._require_transaction()
        reference(capture_ref,{"role_capture"})
        require(capture_ref["namespace"]=="control" and self.ledger.get(capture_ref)==capture,"invalid_derived_capture")
        p=capture["packet"]["payload"];payload=input_["payload"]
        require(p["role"]==payload["role"] and p["context"]["derived_input"]==input_ and
            capture["transport"]["transport_success"] is True and capture["validation_code"]=="raw_transport_valid" and
            capture["category"] in ("infrastructure_acceptance","qualification_canary"),"derived_capture_input_mismatch")
        require(self.prepare(payload["role"],payload["namespace"],payload["cutoff"],payload["spec"])==input_,"derived_input_snapshot_mismatch")
        if capture["category"]=="qualification_canary":
            require(self.config["model"] is not None and self.config["model"]==p["requested_model"],"fixed_derived_model_mismatch")
        response=capture["transport"]["final"];raw_response=canonical(response).decode()
        bounded_text(raw_response,self.config["max_output_bytes"],"derived_output_overflow")
        items=self._validate_output(input_,response)
        invocation={"mode":"captured_diagnostic","capture":capture_ref}
        request={"input":input_,"raw_response":raw_response,"invocation":invocation}
        prior=self.ledger.receipt(request_id,request)
        if prior:return prior
        raw=self.ledger.put({"kind":"derived_raw","id":"derived-raw-"+sha256(request),"version":1,
            "input":input_,"raw_response":raw_response,"invocation":invocation},"control")
        batch=self.ledger.put({"kind":"derived_batch","id":"derived-batch-"+sha256({"request_id":request_id,"request":request}),"version":1,
            "namespace":payload["namespace"],"input":input_,"items":items,"raw":raw,"invocation":invocation,"request_id":request_id},payload["namespace"])
        return self.ledger.commit_receipt(request_id,request,{"status":"accepted" if items else "noop","code":"structurally_valid",
            "raw":raw,"batch":batch,"category":capture["category"],"semantic_validation":"not_established",
            "formal_admission":False},"control")

    def resolve(self,ref,role,ns,cutoff,spec_ref):
        reference(ref,{"derived_batch"});require(ref["namespace"]==ns,"cross_namespace_derived_batch")
        batch=self.ledger.get(ref);receipt=self.ledger.receipt(batch["request_id"])
        require(receipt is not None and receipt["result"]["batch"]==ref,"uncommitted_derived_batch")
        if batch["invocation"]["mode"]=="captured_collaboration":
            from .collaboration import resolve_capture
            capture=resolve_capture(self.ledger,batch["invocation"]["capture"])
            require(capture["packet"]["role_input"]["payload"]["context"]["derived_input"]==batch["input"] and
                canonical(capture["final"]).decode()==self.ledger.get(batch["raw"])["raw_response"],"derived_collaboration_input_mismatch")
        if batch["invocation"]["mode"]=="captured_diagnostic":
            from .admission import resolve_capture
            capture=resolve_capture(self.ledger,batch["invocation"]["capture"])
            require(capture["packet"]["payload"]["context"]["derived_input"]==batch["input"] and
                canonical(capture["transport"]["final"]).decode()==self.ledger.get(batch["raw"])["raw_response"],"derived_capture_input_mismatch")
        input_=self.prepare(role,ns,cutoff,spec_ref)
        require(batch["input"]==input_,"stale_derived_batch")
        return batch

    def accept_collaboration(self, request_id, input_, capture_ref):
        """Admit an immutable observed subagent envelope, never scripted usage."""
        from .collaboration import resolve_capture
        self.ledger._require_transaction()
        capture = resolve_capture(self.ledger, capture_ref)
        packet = capture["packet"]["role_input"]["payload"]
        payload = input_["payload"]
        require(packet["role"] == payload["role"] and packet["namespace"] == payload["namespace"] and
                packet["cutoff"] == payload["cutoff"] and packet["context"]["derived_input"] == input_,
                "derived_collaboration_input_mismatch")
        require(self.prepare(payload["role"], payload["namespace"], payload["cutoff"], payload["spec"]) == input_,
                "derived_input_snapshot_mismatch")
        items = self._validate_output(input_, capture["final"])
        invocation = {"mode": "captured_collaboration", "capture": capture_ref}
        raw_response = canonical(capture["final"]).decode()
        bounded_text(raw_response, self.config["max_output_bytes"], "derived_output_overflow")
        request = {"input": input_, "raw_response": raw_response, "invocation": invocation}
        old = self.ledger.receipt(request_id, request)
        if old: return old
        raw = self.ledger.put({"kind": "derived_raw", "id": "derived-raw-" + sha256(request), "version": 1,
                              "input": input_, "raw_response": raw_response, "invocation": invocation}, "control")
        batch = self.ledger.put({"kind": "derived_batch", "id": "derived-batch-" + sha256({"request_id": request_id, "request": request}),
                                "version": 1, "namespace": payload["namespace"], "input": input_, "items": items,
                                "raw": raw, "invocation": invocation, "request_id": request_id}, payload["namespace"])
        return self.ledger.commit_receipt(request_id, request, {"status": "accepted" if items else "noop",
                    "code": "structurally_valid", "raw": raw, "batch": batch, "category": "collaboration_observed",
                    "provider_tokens": None, "provider_cost": None, "semantic_validation": "not_established",
                    "formal_admission": False}, "control")

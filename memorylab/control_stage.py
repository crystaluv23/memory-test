"""Actual receipt-derived control stages; no model or quality shortcut."""
from pathlib import Path

from .common import exclusive_run
from .contracts import canonical,exact_object,require,sha256,timestamp
from .kernel_cli import read_json
from .runtime import RuntimeBuilder
from .task_stage import ZERO


def control_config():
    c=read_json(Path(__file__).resolve().parent.parent/"configs/control_stage_v0.json")
    exact_object(c,{"schema_version","hypothesis","mode","max_report_bytes","max_research_view_bytes","research_visibility",
        "blocked_grade","shadow_quality","cross_store_commit","lock_policy","formal_admission","model_calls"},"invalid_control_stage_config")
    require(c["schema_version"]=="control-stage-v0" and c["mode"]=="fixture_only" and
        c["research_visibility"]=="aggregate_task_outcomes_no_private_grade_or_raw_traces" and
        c["blocked_grade"]=="unavailable_no_execution" and c["shadow_quality"]=="unavailable_no_promotion" and
        c["cross_store_commit"]=="immutable_child_receipt_then_parent_publish" and c["formal_admission"] is False and
        c["lock_policy"]=="distinct_control_task_and_role_output_locks" and
        type(c["model_calls"]) is int and c["model_calls"]==0,"unsupported_control_stage_policy")
    for k in ("max_report_bytes","max_research_view_bytes"):
        require(type(c[k]) is int and c[k]>0,"invalid_control_stage_limit")
    return c


class ControlStages:
    roles={"grade","corpus","rebuild","report","research_view","proposal_validate","shadow_grade"}

    def __init__(self,inputs,tasks,actors,output):
        require(tasks.inputs is inputs and actors.inputs is inputs,"control_stage_resolver_mismatch")
        self.inputs,self.tasks,self.actors=inputs,tasks,actors
        self.ledger,self.journal=inputs.ledger,inputs.journal;self.config=control_config()
        self.output=Path(output);require(not self.output.is_symlink(),"invalid_control_stage_output")
        self.output.mkdir(parents=True,exist_ok=True);self.output=self.output.resolve()
        require(len({self.output,tasks.output,actors.output})==3,"control_stage_lock_alias")

    def _start_id(self,slot):return "control-start-"+sha256({"plan":self.inputs.plan,"slot":slot})

    def _execution(self,slot):
        start=self.ledger.receipt(self._start_id(slot));require(start is not None,"missing_control_stage_execution")
        ref=start["result"]["execution"];e=self.ledger.get(ref)
        p=self.inputs.resolve(e["input"])["payload"]
        request={"input":e["input"],"config":self.config,"output":str(self.output)}
        require(p["slot"]==slot and p["role"] in self.roles-{"grade"} and e["request"]==request and
            start["request_sha256"]==sha256(request),"control_stage_execution_drift")
        return ref,e,p

    def run(self,slot,at,fault=None):
        timestamp(at)
        with exclusive_run(self.output):
            input_ref=self.inputs.prepare(slot);p=self.inputs.resolve(input_ref)["payload"]
            require(p["role"] in self.roles,"not_control_stage")
            if p["role"]=="grade":return self.tasks.grade(slot,at,fault)
            request={"input":input_ref,"config":self.config,"output":str(self.output)}
            start=self.ledger.receipt(self._start_id(slot),request)
            if start:
                e=self.ledger.get(start["result"]["execution"])
                if self.ledger.receipt(e["end_id"]):return {**self.resolve(slot),"cache_hit":True}
            else:
                with self.ledger.mutation():
                    begun=self.journal.begin_bound("control-begin-"+sha256(input_ref),input_ref,self.inputs)["result"]
                    require(begun["status"]=="stage_started","control_stage_already_terminal")
                    e={"kind":"control_stage_execution","id":"control-"+sha256(input_ref),"version":1,
                        "input":input_ref,"request":request,"attempt":begun["attempt"],"timestamp":at,
                        "end_id":"control-end-"+sha256(input_ref)}
                    ref=self.ledger.put(e,"control")
                    self.ledger.commit_receipt(self._start_id(slot),request,{"execution":ref},"control")
            ref,e,p=self._execution(slot);child=None
            if p["store_scope"]!="main":
                require(p["role"]=="rebuild","unsupported_shadow_control")
                with self.inputs._scope(self.inputs.nodes[slot]) as (cards,registry,store,corpus,scope):
                    require(p["resources"]["store_binding"]=={k:scope[k] for k in ("path","binding")},"control_scope_drift")
                    req={"execution":ref,"input":input_ref,"store_binding":p["resources"]["store_binding"]}
                    id_="control-child-"+sha256(req)
                    with cards.ledger.mutation():
                        child=cards.ledger.receipt(id_,req)
                        if child is None:child=cards.ledger.commit_receipt(id_,req,self._rebuild(p,cards,registry),"control")
                        self._verify_rebuild(p,cards,registry,child["result"])
                if fault:fault("child_committed")
            with self.ledger.mutation():
                self.inputs.resolve(input_ref)
                artifacts=child["result"] if child else self._materialize(p,e["timestamp"])
                if fault:fault("materialized")
                publication=self.ledger.put({"kind":"control_stage_publication","id":"publication-"+sha256(ref),"version":1,
                    "execution":ref,"input":input_ref,"artifacts":artifacts,"child_receipt":child,"formal_admission":False},"control")
                if fault:fault("publication")
                finish=self.journal.complete("control-finish-"+sha256(ref),e["attempt"],"completed",ZERO,[],
                    {**artifacts,"control_publication":publication})
                if fault:fault("stage")
                result={"status":"control_stage_committed","publication":publication,"stage":finish,"actual_model_calls":0,
                    "formal_admission":False,"promotion_eligible":False,"gain_evidence":False,"cache_hit":False,"independent_sample":False}
                self.ledger.commit_receipt(e["end_id"],{"execution":ref,"publication":publication},result,"control")
                if fault:fault("receipt")
                return result

    def resolve(self,slot):
        with self.ledger.mutation(),self.ledger.cached_reads(self.inputs.config["ledger_decode_cache_bytes"]):
            return self._resolve(slot)

    def _resolve(self,slot):
        ref,e,p=self._execution(slot);end=self.ledger.receipt(e["end_id"])
        require(end is not None,"unpublished_control_stage")
        publication_ref=end["result"]["publication"];pub=self.ledger.get(publication_ref)
        require(end["request_sha256"]==sha256({"execution":ref,"publication":publication_ref}) and
            pub["execution"]==ref and pub["input"]==e["input"] and pub["formal_admission"] is False,"control_publication_drift")
        art=pub["artifacts"]
        if p["role"]=="rebuild":
            with self.inputs._scope(self.inputs.nodes[slot]) as (cards,registry,store,corpus,scope):
                if scope:
                    req={"execution":ref,"input":e["input"],"store_binding":p["resources"]["store_binding"]}
                    child=pub["child_receipt"]
                    require(child is not None and child==cards.ledger.receipt("control-child-"+sha256(req),req) and child["result"]==art,
                        "control_child_receipt_drift")
                else:require(pub["child_receipt"] is None,"control_scope_drift")
                self._verify_rebuild(p,cards,registry,art)
        elif p["role"]=="corpus":
            require(art=={"snapshot":p["resources"]["snapshot"]},"control_corpus_drift")
            self.inputs.store.resolve(art["snapshot"])
        elif p["role"]=="report":require(self.ledger.get(art["report"])["value"]==self.task_report(),"control_report_drift")
        elif p["role"] in ("proposal_validate","shadow_grade"):
            require(self.ledger.get(art["validation"])["value"]==self._proposal(p),"control_validation_drift")
        elif p["role"]=="research_view":
            require(self.ledger.get(art["view"])["value"]==self._view(p),"control_research_view_drift")
        d=self.inputs._dependency(slot)
        require(d["artifacts"]=={**art,"control_publication":publication_ref} and d["result"]==end["result"]["stage"]["result"],
            "control_stage_finish_drift")
        return end["result"]

    @staticmethod
    def _rebuild(p,cards,registry):
        r=p["resources"];built=RuntimeBuilder(cards,registry).build(p["namespace"],p["cutoff"],r["memory"],r["derived_refs"])
        return {"snapshot":r["snapshot"],"cutoff":p["cutoff"],"projection":built["projection_ref"],"derived_refs":r["derived_refs"]}

    def _verify_rebuild(self,p,cards,registry,art):
        # Build is idempotent from an immutable corpus, not a fresh actor call.
        require(art==self._rebuild(p,cards,registry),"control_rebuild_drift")

    def task_report(self):
        """Every planned task stays visible, even before execution."""
        with self.ledger.mutation(),self.ledger.cached_reads(self.inputs.config["ledger_decode_cache_bytes"]):
            return self._task_report()

    def _task_report(self):
        self.inputs.preflight();self.inputs.refresh();finished=self.journal._finished();attempts=self.journal._attempts();rows=[]
        for n in self.inputs.graph["slots"]:
            if n["role"] not in ("solver","acquisition","oracle"):continue
            slot=n["id"];grade_slot=slot+"-grade";grade=None;grade_ref=None
            task_status=finished.get(slot,{}).get("outcome","started" if slot in attempts else "pending")
            grade_status=finished.get(grade_slot,{}).get("outcome","pending")
            if grade_slot in finished and grade_status=="completed":
                validated=self.tasks.resolve_grade(grade_slot);grade=validated["value"];grade_ref=validated["ref"]
            ep=n["bindings"]["episode"];parts=slot.split("-");scenario=int(parts[0][1:]);repeat=int(parts[1][1:])
            chain=self.inputs.graph["chains"][scenario*len(self.inputs.graph["seeds"])+repeat]
            rows.append({"task":slot,"namespace":n["namespace"],"agent_role":n["role"],"episode_role":ep["role"],
                "scenario":chain["scenario"],"repeat":repeat,"seed":chain["seed"],"memory":n["bindings"]["memory"],
                "task_status":task_status,"grade_status":grade_status,"grade":grade_ref,
                "verification":grade["verification"] if grade else "unavailable",
                "full_success":bool(grade and grade["verification"]=="verified" and grade["full_success"] and task_status=="completed"),
                "behavior_success":bool(grade and grade["verification"]=="verified" and grade["behavior_success"] and task_status=="completed")})
        result={"plan":self.inputs.plan,"mode":self.inputs.graph["mode"],"category":"historical_failure_derived_synthetic",
            "evidence_category":"infrastructure_acceptance","planned":len(rows),"rows":rows,
            "full_success":sum(r["full_success"] for r in rows),"behavior_success":sum(r["behavior_success"] for r in rows),
            "unavailable":sum(r["verification"]!="verified" for r in rows),"actual_model_calls":0,
            "formal_admission":False,"gain_evidence":False,"promotion_eligible":False}
        require(len(canonical(result))<=self.config["max_report_bytes"],"control_report_overflow")
        return result

    def _view(self,p):
        producer=self.inputs.nodes[p["slot"]]["bindings"]["report_receipt"]
        report_result=self.resolve(producer);pub=self.ledger.get(report_result["publication"])
        report=self.ledger.get(pub["artifacts"]["report"])["value"];groups={}
        for row in report["rows"]:
            key=(row["scenario"]["id"],row["memory"]["id"] if row["memory"] else row["agent_role"],row["episode_role"])
            v=groups.setdefault(key,{"scenario":key[0],"mechanism":key[1],"episode_role":key[2],"planned":0,
                "full_success":0,"behavior_success":0,"unavailable":0,"not_executed":0})
            v["planned"]+=1;v["full_success"]+=row["full_success"];v["behavior_success"]+=row["behavior_success"]
            v["unavailable"]+=row["verification"]!="verified";v["not_executed"]+=row["task_status"] in ("pending","blocked")
        return {"mode":report["mode"],"evidence_category":"infrastructure_acceptance","quality":"not_established",
            "outcomes":[groups[k] for k in sorted(groups)],
            "mechanisms":[self.inputs.registry.resolve(r) for r in self.inputs.graph["memory_refs"]],
            "scenarios":[self.inputs.registry.resolve(r) for r in self.inputs.graph["scenario_refs"]],"promotion_eligible":False}

    def _proposal(self,p):
        producer=self.inputs.nodes[p["slot"]]["bindings"]["proposal_receipt"];dep=self.inputs._dependency(producer)
        if dep["result"]["outcome"]=="blocked":return {"status":"unavailable","code":"proposal_not_executed","quality":"unavailable","promotion_eligible":False}
        require(dep["artifacts"].get("role_publication") is not None,"missing_actual_research_publication")
        result=self.actors.resolve(producer);r=result["role_result"]
        with self.inputs._scope(self.inputs.nodes[producer]) as (cards,registry,store,corpus,scope):
            material=r["materialization"];capture=cards.ledger.get(r["capture"])
            if material:
                require(cards.ledger.receipt(material["request_id"])==material,"control_proposal_receipt_drift")
                value=material["result"];status=value["status"];code=value["code"]
                require(status in ("accepted","noop","rejected"),"invalid_control_proposal_status")
                if value["candidate"] is not None:registry.resolve(value["candidate"])
            else:
                status="rejected" if capture["transport"].get("transport_success") else "transport_failed";code=r["code"]
        return {"status":status,"code":code,"quality":"unavailable","promotion_eligible":False}

    def _materialize(self,p,at):
        role=p["role"];r=p["resources"];key=sha256({"plan":self.inputs.plan,"slot":p["slot"]})
        if role=="corpus":return {"snapshot":r["snapshot"]}
        if role=="rebuild":return self._rebuild(p,self.inputs.cards,self.inputs.registry)
        value=self.task_report() if role=="report" else self._view(p) if role=="research_view" else self._proposal(p)
        ref=self.ledger.put({"kind":"control_result","id":"control-result-"+key,"version":1,"value":value,"formal_admission":False},"control")
        art={"report" if role=="report" else "view" if role=="research_view" else "validation":ref}
        if role in ("research_view","proposal_validate"):
            text=canonical(value).decode()
            require(len(text.encode())<=min(self.config["max_research_view_bytes"],self.inputs.cards.kernel["limits"]["source_text_bytes"]),
                "control_public_view_overflow")
            producer=self.inputs.nodes[p["slot"]]["bindings"].get("proposal_receipt")
            ns=self.inputs.nodes[producer]["namespace"] if producer else p["namespace"]
            source=self.inputs.cards.evidence.register("control-public-"+key,ns,"explicit_feedback" if producer else "agent_output",
                producer or p["slot"],at,text)
            art["observable_sources"]=[source]
        return art

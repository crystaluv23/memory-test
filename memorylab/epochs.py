"""Controlled alternating epoch acceptance. Fixture transitions never claim gain."""
import copy

from .contracts import (canonical, exact_object, identifier, reference, require, sha256)
from .kernel_cli import read_json
from .scenario import compile_chain
from .statistics import paired_decision


def epoch_config(path=None):
    from pathlib import Path
    value=read_json(path or Path(__file__).resolve().parent.parent/"configs/epoch_v0.json")
    exact_object(value,{"schema_version","mode","status","hypothesis","active_max","elite_max","proposals_max","confirmation_min","confirmation_max",
        "oracle_min","frontier_min","frontier_max_exclusive","mastery_min","mastery_rounds","plateau_attempts","plateau_min_gain","success_gain_min",
        "noninferiority_margin","cost_reduction_min","alpha","multiple_comparison","severe_regression_tolerance",
        "decision_claims","shadow_comparisons_max","confirmation_comparisons_max","error_scope","confirmation_policy","cost_claim_scope"},"invalid_epoch_config")
    require(value["schema_version"]=="epoch-contract-v0" and value["mode"]=="scripted_acceptance","formal_epoch_not_accepted")
    for key in ("active_max","elite_max","proposals_max","confirmation_min","confirmation_max","mastery_rounds","plateau_attempts","shadow_comparisons_max","confirmation_comparisons_max"):
        require(type(value[key]) is int and value[key]>0,"invalid_epoch_limit")
    require(value["active_max"]<=8 and value["elite_max"]<=3 and value["proposals_max"]<=3,"epoch_expansion_limit")
    require(value["confirmation_min"]<=value["confirmation_max"]<=14,"invalid_confirmation_limit")
    for key in ("oracle_min","frontier_min","frontier_max_exclusive","mastery_min","plateau_min_gain","success_gain_min","noninferiority_margin","cost_reduction_min","alpha"):
        require(type(value[key]) is float and 0<value[key]<1,"invalid_epoch_threshold")
    require(value["frontier_min"]<value["frontier_max_exclusive"]==value["mastery_min"],"ambiguous_frontier_boundary")
    require(value["multiple_comparison"]=="bonferroni_predeclared_comparisons_and_claims" and type(value["severe_regression_tolerance"]) is int and value["severe_regression_tolerance"]==0,"unsupported_decision_rule")
    require(value["decision_claims"]==["better","worse","noninferior"] and value["shadow_comparisons_max"]<=3 and
        value["confirmation_comparisons_max"]==value["proposals_max"]+2*value["shadow_comparisons_max"],"unsupported_decision_family")
    require(value["error_scope"]=="one_memory_epoch_primary_and_shadow_all_reported_quality_claims" and
        value["confirmation_policy"]=="frozen_plan_exact_units_single_look" and
        value["cost_claim_scope"]=="observed_frozen_paired_cohort_total_cost_not_future_expected_cost","unsupported_confirmation_policy")
    return value


def pareto_refs(metrics):
    return [m["ref"] for m in metrics if not any(o["success"]>=m["success"] and o["cost"]<=m["cost"] and
        (o["success"]>m["success"] or o["cost"]<m["cost"]) for o in metrics)]


class ControlledEpochs:
    def __init__(self,ledger,registry,cards):
        self.ledger,self.registry,self.cards=ledger,registry,cards
        self.config=epoch_config()
        with ledger.mutation():
            ledger.put({"kind":"epoch_contract","id":"epoch-contract-v0","version":1,"config":self.config},"control")

    def initialize(self,request_id,experiment_id,memories,focus):
        identifier(request_id);identifier(experiment_id)
        require(type(memories) is list and 1<=len(memories)<=self.config["active_max"],"invalid_active_population")
        require(len({canonical(r) for r in memories})==len(memories),"duplicate_active_memory")
        for ref in memories:require(self.registry.resolve(ref)["kind"]=="memory","invalid_active_memory")
        require(self.registry.resolve(focus)["kind"]=="scenario","invalid_focus")
        request={"experiment":experiment_id,"active":memories,"focus":focus,"config":self.config}
        with self.ledger.mutation():
            prior=self.ledger.receipt(request_id,request)
            if prior:return prior
            id_="epoch-"+experiment_id
            require(not any(v["id"]==id_ for _,v in self.ledger.objects("epoch","control")),"experiment_already_initialized")
            signature=compile_chain(self.registry,focus,0)["semantic_signature"]
            state={"kind":"epoch","id":id_,"version":1,"parent":None,"mode":"scripted_acceptance","phase":"memory","status":"running",
                "active":memories,"archive":[],"focus":focus,"challenges":[{"ref":focus,"status":"frontier","signature":signature,"reward":None}],
                "elite":memories[:self.config["elite_max"]],"metrics":[],"proposals":0,"pending":[],"attempts":[],"mastery":[],"mastery_seen":[],
                "memory_epoch":1,"comparison_plans":[],"evaluation_units":[],
                "task_champion":memories[0],"meta_champion":memories[0],"switch_reason":None,"history":[]}
            ref=self.ledger.put(state,"control")
            return self.ledger.commit_receipt(request_id,request,{"status":"epoch_initialized","state":ref,"simulated":True},"control")

    def latest(self,id_):
        versions=[(r,v) for r,v in self.ledger.objects("epoch","control") if v["id"]==id_]
        require(bool(versions),"missing_epoch")
        previous=None
        for i,(ref,value) in enumerate(versions):
            require(value["version"]==i+1 and value["parent"]==previous,"broken_epoch_lineage")
            previous=ref
        return versions[-1]

    def fixture(self,request_id,operation,data):
        """Controller-only fixture authority. No model or formal results accepted."""
        request={"operation":operation,"data":data,"category":"scripted_fixture"}
        canonical(request)
        with self.ledger.mutation():
            prior=self.ledger.receipt(request_id,request)
            if prior:return prior["result"]["proof"]
            ref=self.ledger.put({"kind":"acceptance_proof","id":"proof-"+sha256(request),"version":1,**request},"control")
            self.ledger.commit_receipt(request_id,request,{"status":"fixture_registered","proof":ref},"control")
            return ref

    def proof(self,ref,operation):
        reference(ref,{"acceptance_proof"});require(ref["namespace"]=="control","invalid_proof_namespace")
        value=self.ledger.get(ref)
        require(value["category"]=="scripted_fixture" and value["operation"]==operation,"invalid_fixture_proof")
        return value["data"]

    def parent(self,state):
        metrics=[m for m in state["metrics"] if m["ref"] in state["active"]]
        eligible=pareto_refs(metrics) if metrics else state["active"]
        counts={canonical(ref):sum(a.get("parent")==ref for a in state["attempts"]) for ref in eligible}
        return min(eligible,key=lambda r:(counts[canonical(r)],r["id"]))

    def transition(self,request_id,state_ref,action,args):
        identifier(request_id);reference(state_ref,{"epoch"});canonical(args)
        request={"state":state_ref,"action":action,"args":args,"config_sha256":sha256(self.config)}
        with self.ledger.mutation():
            prior=self.ledger.receipt(request_id,request)
            if prior:return prior
            current,state=self.latest(state_ref["id"])
            require(current==state_ref,"stale_epoch_snapshot")
            new=copy.deepcopy(state)
            outcome=self._apply(new,action,args,request_id)
            new["version"]+=1;new["parent"]=state_ref
            require(len(new["active"])<=self.config["active_max"],"active_population_overflow")
            require(sum(t["status"]=="frontier" for t in new["challenges"])<=1,"multiple_frontiers")
            new["history"].append({"action":action,"result":outcome,"simulated":True})
            # Public, bounded summaries go to each researcher's own namespace;
            # private fixture/grade input arrays and hidden traces are excluded.
            from datetime import datetime,timezone
            at=datetime.now(timezone.utc).isoformat()
            summary={"mode":"scripted_acceptance","action":action,"status":outcome.get("status"),"phase":new["phase"],
                "focus_id":new["focus"]["id"],"task_champion_id":new["task_champion"]["id"],"meta_champion_id":new["meta_champion"]["id"],
                "active_count":len(new["active"]),"known_gain_evidence":False}
            for role in ("evaluator","proposer"):
                self.cards.evidence.register("research-"+sha256({"request_id":request_id,"role":role}),"research/"+role,
                    "visible_outcome",new["id"],at,canonical(summary).decode())
            ref=self.ledger.put(new,"control")
            return self.ledger.commit_receipt(request_id,request,{"status":"epoch_transition","state":ref,"result":outcome,
                "simulated":True,"gain_evidence":False,"kernel_frozen":False},"control")

    def _apply(self,state,action,args,request_id):
        if action=="plan_confirmation":return self._plan(state,args,request_id)
        if action=="propose":return self._propose(state,args,request_id)
        if action=="memory_confirmation":return self._memory(state,args)
        if action=="mastery_confirmation":return self._mastery(state,args)
        if action=="challenge_confirmation":return self._challenge(state,args)
        if action=="shadow_confirmation":return self._shadow(state,args)
        if action=="exhaustion":
            exact_object(args,{"reason"},"invalid_exhaustion")
            require(args["reason"]=="proposal_budget_exhausted" and state["proposals"]>=self.config["proposals_max"],"unproven_search_exhaustion")
            state["status"]="proposal_budget_exhausted"
            return {"status":state["status"],"pending_retained":len(state["pending"])}
        require(False,"unknown_epoch_action")

    def _propose(self,state,args,request_id):
        exact_object(args,{"proposal","cutoff"},"invalid_epoch_proposal")
        proposal=args["proposal"]
        require(type(proposal) is dict and "kind" in proposal,"invalid_epoch_proposal")
        expected="memory" if state["phase"]=="memory" else "scenario"
        require(proposal["kind"]==expected,"opposite_side_frozen")
        require(state["proposals"]<self.config["proposals_max"],"proposal_budget_exhausted")
        required=self.parent(state) if expected=="memory" else state["focus"]
        require(proposal.get("parent")==required,"ineligible_epoch_parent")
        state["proposals"]+=1
        result=self.registry.propose("proposal-"+sha256({"request_id":request_id}),proposal,"proposer" if expected=="memory" else "evaluator",
            args["cutoff"],self.cards.proposal_evidence)["result"]
        if result["status"]=="accepted":
            if result["candidate"] in state["active"] or result["candidate"] in state["pending"]:
                return {**result,"status":"duplicate","code":"already_materialized_or_active"}
            state["pending"].append(result["candidate"])
        return result

    def _plan(self,state,args,request_id):
        exact_object(args,{"operation","candidate","baseline","history_sha256","units"},"invalid_confirmation_plan")
        operation=args["operation"]
        require(operation in ("memory_confirmation","shadow_confirmation"),"invalid_confirmation_plan_operation")
        candidate=args["candidate"]
        if operation=="memory_confirmation":
            require(state["phase"]=="memory","opposite_side_frozen")
            require(candidate in state["pending"] and candidate["kind"]=="memory","unproposed_candidate")
            baseline=self.registry.resolve(candidate)["parent"]
            require(args["history_sha256"] is None,"invalid_memory_plan_history")
            roles={"task"};maximum=self.config["proposals_max"]
        else:
            require(candidate==state["task_champion"] and candidate!=state["meta_champion"],"shadow_not_new_task_champion")
            baseline=state["meta_champion"]
            history=args["history_sha256"]
            require(type(history) is str and len(history)==64 and all(c in "0123456789abcdef" for c in history),"invalid_shadow_history")
            roles={"evaluator","proposer"};maximum=self.config["shadow_comparisons_max"]
        require(args["baseline"]==baseline,"confirmation_baseline_drift")
        exact_object(args["units"],roles,"invalid_confirmation_plan_units")
        units=[]
        for values in args["units"].values():
            require(type(values) is list and self.config["confirmation_min"]<=len(values)<=self.config["confirmation_max"],"invalid_confirmation_plan_units")
            for unit in values:identifier(unit)
            units.extend(values)
        require(len(set(units))==len(units) and not set(units)&set(state["evaluation_units"]),"reused_confirmation_units")
        count=sum(self.ledger.get(p["ref"])["operation"]==operation and
            self.ledger.get(p["ref"])["memory_epoch"]==state["memory_epoch"] for p in state["comparison_plans"])
        require(count<maximum,"confirmation_plan_budget_exhausted")
        require(not any(self.ledger.get(p["ref"])["candidate"]==candidate and self.ledger.get(p["ref"])["operation"]==operation and
            p["status"]=="planned" for p in state["comparison_plans"]),"confirmation_plan_already_pending")
        # This state snapshot and request are exactly reconstructable from the plan.
        current,_=self.latest(state["id"])
        plan={"kind":"comparison_plan","id":"comparison-"+sha256({"request_id":request_id}),"version":1,
            "request_id":request_id,"state":current,"memory_epoch":state["memory_epoch"],"focus":state["focus"],
            "config_sha256":sha256(self.config),"mode":"scripted_acceptance",**copy.deepcopy(args)}
        ref=self.ledger.put(plan,"control")
        state["comparison_plans"].append({"ref":ref,"status":"planned"})
        state["evaluation_units"].extend(units)
        return {"status":"confirmation_planned","plan":ref,"reserved_units":len(units)}

    def _planned_proof(self,state,args,operation,data,groups):
        reference(args["plan"],{"comparison_plan"})
        require(args["plan"]["namespace"]=="control","invalid_confirmation_plan_namespace")
        plan=self.ledger.get(args["plan"])
        entries=[p for p in state["comparison_plans"] if p["ref"]==args["plan"]]
        require(len(entries)==1 and entries[0]["status"]=="planned","confirmation_plan_consumed_or_unknown")
        require(plan["mode"]=="scripted_acceptance" and plan["config_sha256"]==sha256(self.config) and
            plan["memory_epoch"]==state["memory_epoch"] and plan["focus"]==state["focus"] and
            plan["operation"]==operation and plan["candidate"]==args["candidate"],"confirmation_plan_scope_drift")
        baseline=self.registry.resolve(args["candidate"])["parent"] if operation=="memory_confirmation" else state["meta_champion"]
        require(plan["baseline"]==baseline,"confirmation_baseline_drift")
        require(data["plan"]==args["plan"],"confirmation_proof_plan_drift")
        if operation=="shadow_confirmation":
            require(plan["history_sha256"]==args["history_sha256"]==data["history_sha256"],"shadow_history_drift")
        for role,key in groups.items():
            rows=data[key]
            require(type(rows) is list and all(type(r) is dict and "unit" in r for r in rows),"invalid_paired_data")
            require([r["unit"] for r in rows]==plan["units"][role],"confirmation_units_drift")
        frozen_args={k:plan[k] for k in ("operation","candidate","baseline","history_sha256","units")}
        frozen_request={"state":plan["state"],"action":"plan_confirmation","args":frozen_args,"config_sha256":plan["config_sha256"]}
        receipt=self.ledger.receipt(plan["request_id"],frozen_request)
        require(receipt is not None and receipt["result"]["result"].get("plan")==args["plan"],"unpublished_confirmation_plan")
        events=self.ledger.events("control")
        planned=[e for e in events if e["request_id"]==plan["request_id"] and e["data"]=={"type":"decision","receipt":receipt}]
        proofs=[e for e in events if e["data"].get("type")=="decision" and
            e["data"]["receipt"]["result"].get("proof")==args["proof"]]
        require(len(planned)==len(proofs)==1 and proofs[0]["sequence"]>planned[0]["sequence"],"confirmation_proof_not_after_plan")
        proof_value=self.ledger.get(args["proof"])
        expected={k:proof_value[k] for k in ("operation","data","category")}
        require(self.ledger.receipt(proofs[0]["request_id"],expected)==proofs[0]["data"]["receipt"],"confirmation_proof_receipt_drift")
        return entries[0]

    def _memory(self,state,args):
        exact_object(args,{"candidate","proof","plan"},"invalid_memory_confirmation")
        require(state["phase"]=="memory","opposite_side_frozen")
        candidate=args["candidate"];require(candidate in state["pending"] and candidate["kind"]=="memory","unproposed_candidate")
        data=self.proof(args["proof"],"memory_confirmation")
        exact_object(data,{"pairs","regression_safe","budget_qualified","completed","infrastructure_failure","plan"},"invalid_memory_proof")
        for key in ("regression_safe","budget_qualified","completed","infrastructure_failure"):
            require(type(data[key]) is bool,"invalid_memory_proof")
        entry=self._planned_proof(state,args,"memory_confirmation",data,{"task":"pairs"})
        decision=paired_decision(data["pairs"],self.config)
        entry["status"]="consumed"
        valid=data["completed"] and data["budget_qualified"] and not data["infrastructure_failure"] and decision["verdict"]!="insufficient"
        passed=valid and data["regression_safe"] and decision["verdict"] in ("better","cheaper_noninferior")
        parent=self.registry.resolve(candidate)["parent"]
        units=[r["unit"] for r in data["pairs"]]
        require(not any(set(units)&set(a.get("units",[])) for a in state["attempts"]),"reused_confirmation_units")
        state["pending"].remove(candidate)
        attempt={"candidate":candidate,"parent":parent,"units":units,"valid":valid,"gain":decision["effect"],"passed":passed,"decision":decision,"proof":args["proof"],"plan":args["plan"]}
        state["attempts"].append(attempt)
        if passed:
            state["active"].append(candidate);state["task_champion"]=candidate;state["mastery"]=[]
            for ref,success_key,cost_key in ((parent,"baseline_success","baseline_cost"),(candidate,"candidate_success","candidate_cost")):
                state["metrics"]=[m for m in state["metrics"] if m["ref"]!=ref]
                state["metrics"].append({"ref":ref,"success":sum(r[success_key] for r in data["pairs"])/len(units),
                    "cost":sum(r[cost_key] for r in data["pairs"])/len(units)})
            ranked=pareto_refs([m for m in state["metrics"] if m["ref"] in state["active"]])
            state["elite"]=sorted(ranked,key=lambda r:r["id"])[:self.config["elite_max"]]
            self._evict(state)
        else:state["archive"].append({"ref":candidate,"reason":"confirmation_rejected"})
        valid_attempts=[a for a in state["attempts"] if a["valid"]]
        tail=valid_attempts[-self.config["plateau_attempts"]:]
        if len(tail)==self.config["plateau_attempts"] and all(a["gain"]<self.config["plateau_min_gain"] for a in tail):
            self._switch(state,"plateau")
        return {"status":"simulated_promoted" if passed else "confirmation_rejected","decision":decision,"phase":state["phase"]}

    def _evict(self,state):
        if len(state["active"])<=self.config["active_max"]:return
        niche=lambda r:tuple(self.registry.resolve(r)["config"][k] for k in ("organizer.type","retrieval.type"))
        active=state["active"];metrics=[m for m in state["metrics"] if m["ref"] in active]
        pareto=pareto_refs(metrics)
        dominated=[m["ref"] for m in metrics if m["ref"] not in pareto and m["ref"]!=state["task_champion"]]
        duplicates=[r for i,r in enumerate(active) if r!=state["task_champion"] and any(self.registry.resolve(r)["config"]==self.registry.resolve(o)["config"] for o in active[i+1:])]
        ordinary=[r for r in active if r!=state["task_champion"] and sum(niche(r)==niche(o) for o in active)>1]
        eligible=dominated or duplicates or ordinary
        require(bool(eligible),"no_legal_population_eviction")
        chosen=next(r for r in active if r in eligible);active.remove(chosen)
        state["archive"].append({"ref":chosen,"reason":"bounded_population_eviction"})
        state["elite"]=[r for r in state["elite"] if r in active]

    def _mastery(self,state,args):
        exact_object(args,{"proof"},"invalid_mastery_confirmation")
        require(state["phase"]=="memory","opposite_side_frozen")
        data=self.proof(args["proof"],"mastery_confirmation")
        exact_object(data,{"cohort","units","successes","budget_qualified"},"invalid_mastery_proof")
        require(data["cohort"]==state["elite"] and data["budget_qualified"] is True,"mastery_cohort_or_budget_drift")
        units=data["units"];require(type(units) is list and self.config["confirmation_min"]<=len(units)<=self.config["confirmation_max"],"invalid_mastery_units")
        for unit in units:identifier(unit)
        require(len(set(units))==len(units) and not set(units)&set(state["evaluation_units"]),"reused_mastery_units")
        state["mastery_seen"].extend(units);state["evaluation_units"].extend(units)
        exact_object(data["successes"],{r["id"] for r in state["elite"]},"mastery_cohort_drift")
        rates=[]
        for values in data["successes"].values():
            require(type(values) is list and len(values)==len(units) and all(type(v) is bool for v in values),"invalid_mastery_results")
            rates.append(sum(values)/len(values))
        require(bool(rates),"empty_mastery_cohort")
        passed=sum(rates)/len(rates)>=self.config["mastery_min"]
        if passed:state["mastery"].append({"units":units,"proof":args["proof"],"cohort":data["cohort"]})
        else:state["mastery"]=[]
        if len(state["mastery"])>=self.config["mastery_rounds"]:self._switch(state,"mastery")
        return {"status":"mastered" if state["phase"]=="evaluator" else "mastery_confirmation_pending","passed":passed}

    def _switch(self,state,reason):
        for candidate in state["pending"]:state["archive"].append({"ref":candidate,"reason":"untested_epoch_closed"})
        state["pending"]=[]
        for challenge in state["challenges"]:
            if challenge["ref"]==state["focus"]:challenge["status"]="mastered" if reason=="mastery" else "archive"
        for entry in state["comparison_plans"]:
            if entry["status"]=="planned" and self.ledger.get(entry["ref"])["operation"]=="memory_confirmation":entry["status"]="expired"
        state["phase"]="evaluator";state["proposals"]=0;state["switch_reason"]=reason;state["status"]="running"
        state["frozen_active_sha256"]=sha256(state["active"])

    def _challenge(self,state,args):
        exact_object(args,{"candidate","proof"},"invalid_challenge_confirmation")
        require(state["phase"]=="evaluator" and sha256(state["active"])==state["frozen_active_sha256"],"evaluator_population_drift")
        candidate=args["candidate"];require(candidate in state["pending"] and candidate["kind"]=="scenario","unproposed_challenge")
        data=self.proof(args["proof"],"challenge_confirmation")
        exact_object(data,{"validity","oracle_success","elite_rates","disagreement","coverage","learning_progress","cost","budget_qualified"},"invalid_challenge_reward")
        require(type(data["validity"]) is bool and type(data["budget_qualified"]) is bool,"invalid_challenge_reward")
        for key in ("oracle_success","disagreement","learning_progress","cost"):
            require(type(data[key]) in (int,float) and data[key]>=0,"invalid_challenge_reward")
        require(data["oracle_success"]<=1 and type(data["coverage"]) is list,"invalid_challenge_reward")
        rates=data["elite_rates"];exact_object(rates,{r["id"] for r in state["elite"]},"challenge_cohort_drift")
        require(bool(rates) and all(type(v) in (int,float) and 0<=v<=1 for v in rates.values()),"invalid_elite_rate")
        signature=compile_chain(self.registry,candidate,0)["semantic_signature"]
        duplicate=any(t["signature"]==signature for t in state["challenges"])
        average=sum(rates.values())/len(rates)
        reward={**data,"elite_success":average,"novelty":not duplicate}
        state["pending"].remove(candidate)
        valid=data["validity"] and data["budget_qualified"] and data["oracle_success"]>=self.config["oracle_min"] and not duplicate
        frontier=valid and self.config["frontier_min"]<=average<self.config["frontier_max_exclusive"]
        status="frontier" if frontier else "regression" if valid and average>=self.config["mastery_min"] else "rejected"
        state["challenges"].append({"ref":candidate,"status":status,"signature":signature,"reward":reward})
        if frontier:
            for pending in state["pending"]:
                state["challenges"].append({"ref":pending,"status":"rejected","signature":compile_chain(self.registry,pending,0)["semantic_signature"],"reward":{"reason":"untested_epoch_closed"}})
            state["pending"]=[]
            for t in state["challenges"]:
                if t["status"]=="mastered":t["status"]="regression"
            for entry in state["comparison_plans"]:
                if entry["status"]=="planned":entry["status"]="expired"
            state["memory_epoch"]+=1
            state["focus"]=candidate;state["phase"]="memory";state["proposals"]=0;state["attempts"]=[];state["mastery"]=[];state["mastery_seen"]=[];state["metrics"]=[]
        code="accepted_frontier" if frontier else "duplicate_challenge" if duplicate else "too_easy_regression" if status=="regression" else "stepping_stone_required" if valid else "invalid_or_unsolvable"
        return {"status":status,"code":code,"reward":reward}

    def _shadow(self,state,args):
        exact_object(args,{"candidate","proof","history_sha256","plan"},"invalid_shadow_confirmation")
        require(args["candidate"]==state["task_champion"],"shadow_not_task_champion")
        data=self.proof(args["proof"],"shadow_confirmation")
        exact_object(data,{"history_sha256","evaluator_pairs","proposer_pairs","budget_qualified","plan"},"invalid_shadow_proof")
        require(type(args["history_sha256"]) is str and len(args["history_sha256"])==64 and data["history_sha256"]==args["history_sha256"],"shadow_history_drift")
        entry=self._planned_proof(state,args,"shadow_confirmation",data,{"evaluator":"evaluator_pairs","proposer":"proposer_pairs"})
        require(type(data["budget_qualified"]) is bool,"invalid_shadow_proof")
        evaluator=paired_decision(data["evaluator_pairs"],self.config);proposer=paired_decision(data["proposer_pairs"],self.config)
        passed=data["budget_qualified"] is True and all(d["verdict"] in ("better","cheaper_noninferior") for d in (evaluator,proposer))
        entry["status"]="consumed"
        if passed:state["meta_champion"]=args["candidate"]
        return {"status":"simulated_meta_promoted" if passed else "shadow_rejected","evaluator":evaluator,"proposer":proposer}

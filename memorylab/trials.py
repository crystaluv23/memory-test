"""Budget reservation and atomic trial completion. Private scoring never becomes L0."""
import copy

from .contracts import (canonical, exact_object, identifier, reference, require,
                        sha256, timestamp, namespace)


class TrialJournal:
    def __init__(self, ledger, cards, runtime_builder, config):
        from .freeze import validate_execution
        self.ledger, self.cards, self.builder, self.config = ledger, cards, runtime_builder, validate_execution(config)

    def _completions(self, plan_id=None):
        result = []
        for e in self.ledger.events("control"):
            data = e["data"]
            if data.get("type") == "decision":
                value = data["receipt"]["result"]
                if value.get("status") == "trial_completed" and (plan_id is None or value["plan_id"] == plan_id):
                    result.append(value)
        return result

    def cached(self, fingerprint):
        matches = [r for r in self._completions() if r["fingerprint"] == fingerprint]
        require(len(matches) <= 1, "duplicate_committed_trial")
        return copy.deepcopy(matches[0]) if matches else None

    def balance(self, plan_id):
        attempts = [(ref,value) for ref,value in self.ledger.objects("attempt","control") if value["plan_id"] == plan_id]
        complete = {r["attempt"]["id"]:r for r in self._completions(plan_id)}
        calls = tokens = 0
        unknown = False
        for ref,value in attempts:
            finished = complete.get(ref["id"])
            if not finished:
                calls += value["reserved_calls"]; tokens += value["reserved_tokens"]
            else:
                usage = finished["usage"]
                calls += value["reserved_calls"] if usage["model_calls"] is None else usage["model_calls"]
                if usage["tokens"] is None:
                    tokens += value["reserved_tokens"]; unknown = True
                else:
                    tokens += usage["tokens"]
        return {"charged_calls": calls,"charged_tokens":tokens,"cost_unknown":unknown,
            "calls_remaining":self.config["round_calls"]-calls,"tokens_remaining":self.config["round_tokens"]-tokens}

    def begin(self, request_id, plan_id, job, fingerprint, reserved_calls=0, reserved_tokens=0):
        identifier(request_id); identifier(plan_id)
        require(type(job) is dict and {"id","namespace","memory","mode"} <= set(job), "invalid_trial_job")
        identifier(job["id"])
        namespace(job["namespace"])
        require(job["namespace"].startswith("task/"),"invalid_trial_namespace")
        reference(job["memory"], {"memory"})
        require(job["mode"] in ("scripted_acceptance","live_canary","formal_comparison"), "invalid_trial_mode")
        require(type(fingerprint) is str and len(fingerprint)==64 and all(c in "0123456789abcdef" for c in fingerprint), "invalid_trial_fingerprint")
        for value in (reserved_calls,reserved_tokens):
            require(type(value) is int and value >= 0,"invalid_budget_reservation")
        require(job["mode"] != "scripted_acceptance" or (reserved_calls==0 and reserved_tokens==0), "scripted_cannot_claim_model_cost")
        require(job["mode"] == "scripted_acceptance", "live_trial_not_accepted")
        request = {"plan_id":plan_id,"job":job,"fingerprint":fingerprint,"reserved_calls":reserved_calls,"reserved_tokens":reserved_tokens}
        with self.ledger.mutation():
            prior=self.ledger.receipt(request_id,request)
            if prior:return prior
            cached=self.cached(fingerprint)
            if cached:
                return self.ledger.commit_receipt(request_id,request,{"status":"cache_hit","committed":cached,"new_model_calls":0},"control")
            require(not any(v["fingerprint"]==fingerprint for _,v in self.ledger.objects("attempt","control")),"started_trial_requires_recovery")
            require(not any(v["plan_id"]==plan_id and v["job"]["id"]==job["id"] for _,v in self.ledger.objects("attempt","control")),"unplanned_job_retry")
            balance=self.balance(plan_id)
            require(reserved_calls<=balance["calls_remaining"] and reserved_tokens<=balance["tokens_remaining"],"round_budget_exhausted")
            value={"kind":"attempt","id":"attempt-"+fingerprint,"version":1,"plan_id":plan_id,"job":job,
                "fingerprint":fingerprint,"reserved_calls":reserved_calls,"reserved_tokens":reserved_tokens}
            ref=self.ledger.put(value,"control")
            return self.ledger.commit_receipt(request_id,request,{"status":"trial_started","attempt":ref},"control")

    def complete(self, request_id, attempt_ref, raw_events, grade, usage, at, extraction_template=None, fault=None):
        """fault is controller-only acceptance injection; never from model output."""
        identifier(request_id); reference(attempt_ref,{"attempt"}); timestamp(at)
        exact_object(usage,{"model_calls","tokens","simulated_invocations"},"invalid_trial_usage")
        for v in usage.values():require(v is None or (type(v) is int and v>=0),"invalid_trial_usage")
        require(type(raw_events) is list and type(grade) is dict,"invalid_trial_artifacts")
        require(type(grade.get("full_success")) is bool and type(grade.get("behavior_success")) is bool,"invalid_trial_grade")
        attempt=self.ledger.get(attempt_ref);job=attempt["job"];ns=job["namespace"]
        template=extraction_template if extraction_template is not None else {"cards":[]}
        canonical(template)
        request={"attempt":attempt_ref,"raw_events":raw_events,"grade":grade,"usage":usage,"timestamp":at,"extraction_template":template}
        def checkpoint(name):
            if fault:fault(name)
        with self.ledger.mutation():
            prior=self.ledger.receipt(request_id,request)
            if prior:return prior
            require(self.cached(attempt["fingerprint"]) is None,"duplicate_committed_trial")
            require(job["mode"] != "scripted_acceptance" or (usage["model_calls"]==0 and usage["tokens"]==0),"scripted_usage_mismatch")
            raw={"kind":"raw","id":"raw-"+attempt["fingerprint"],"version":1,"events":raw_events,
                 "mode":job["mode"],"category":"infrastructure_acceptance"}
            raw_ref=self.ledger.put(raw,"control");checkpoint("raw")
            grade_ref=self.ledger.put({"kind":"grade","id":"grade-"+attempt["fingerprint"],"version":1,
                                      "value":grade,"mode":job["mode"]},"control");checkpoint("grade")
            sources=[]
            # Only exact observable envelopes are admitted to task memory; private
            # grader groups/inputs/expected outputs stay in the control grade object.
            for i,event in enumerate(raw_events):
                exact_object(event,{"event_type","episode","timestamp","text"},"invalid_observable_event")
                sources.append(self.cards.evidence.register("rawsrc-"+sha256({"trial":attempt["fingerprint"],"index":i}),ns,
                    event["event_type"],event["episode"],event["timestamp"],event["text"]))
            public={k:grade[k] for k in ("full_success","behavior_success")}
            public["verification"]="unavailable" if grade.get("status") in ("interrupted","missing","timeout") else "verified"
            sources.append(self.cards.evidence.register("outcome-"+attempt["fingerprint"],ns,"visible_outcome",job["id"],at,canonical(public).decode()))
            checkpoint("sources")
            from .cards import RAW_CARD_KEYS
            exact_object(template,{"cards"},"invalid_extraction_template")
            require(type(template["cards"]) is list,"invalid_extraction_template")
            proposal={"cards":[]}
            for raw_card in template["cards"]:
                exact_object(raw_card,(RAW_CARD_KEYS-{"evidence_refs"})|{"source_indices"},"invalid_extraction_template_card")
                indices=raw_card["source_indices"]
                require(type(indices) is list and bool(indices) and all(type(i) is int and 0<=i<len(sources) for i in indices),"invalid_source_indices")
                proposal["cards"].append({**{k:v for k,v in raw_card.items() if k!="source_indices"},"evidence_refs":[sources[i] for i in indices]})
            extraction=self.cards.extract("extract-"+attempt["fingerprint"],proposal,ns,self.ledger.head(),at)
            checkpoint("memory")
            projection=self.builder.build(ns,self.ledger.head(),job["memory"])
            checkpoint("projection")
            known=usage["model_calls"] is not None and usage["tokens"] is not None
            within=known and usage["model_calls"]<=attempt["reserved_calls"] and usage["tokens"]<=attempt["reserved_tokens"]
            valid_memory=extraction["result"]["status"]=="extracted"
            result={"status":"trial_completed","plan_id":attempt["plan_id"],"fingerprint":attempt["fingerprint"],
                "attempt":attempt_ref,"job":job,"raw":raw_ref,"grade":grade_ref,"sources":sources,
                "extraction":extraction,"projection":projection["projection_ref"],"usage":usage,
                "budget_qualified":bool(within),"category":"infrastructure_acceptance",
                "task_success":bool(grade["full_success"] and valid_memory),"gain_evidence":False,
                "promotion_eligible":False}
            payoff=self.ledger.put({"kind":"payoff","id":"payoff-"+attempt["fingerprint"],"version":1,
                "job":job,"grade":grade_ref,"trial_success":result["task_success"],"budget_qualified":bool(within),
                "evidence_category":"infrastructure_acceptance"},"control")
            result["payoff"]=payoff;checkpoint("payoff")
            receipt=self.ledger.commit_receipt(request_id,request,result,"control");checkpoint("receipt")
            return receipt

    def recover_interrupted(self, request_id, attempt_ref, at):
        attempt=self.ledger.get(attempt_ref)
        grade={"full_success":False,"behavior_success":False,"status":"interrupted","independent_grade":None}
        usage={"model_calls":0 if attempt["job"]["mode"]=="scripted_acceptance" else None,
               "tokens":0 if attempt["job"]["mode"]=="scripted_acceptance" else None,"simulated_invocations":1}
        return self.complete(request_id,attempt_ref,[],grade,usage,at)

    def report(self, jobs, plan_id):
        completed={r["job"]["id"]:r for r in self._completions(plan_id)}
        rows=[]
        for job in jobs:
            result=completed.get(job["id"])
            rows.append({"job":job["id"],"status":"pending" if result is None else self.ledger.get(result["grade"])["value"].get("status","completed"),
                "success":bool(result and result["task_success"]),"fingerprint":result["fingerprint"] if result else None})
        return {"category":"infrastructure_acceptance","planned":len(jobs),"completed":len(completed),
            "successful":sum(r["success"] for r in rows),"rows":rows,"budget":self.balance(plan_id),
            "gain_evidence":False,"kernel_frozen":False}

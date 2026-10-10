"""Complete prospective stage graphs and an explicitly simulated budget journal.

No Codex invocation or live-result admission occurs in this module.
"""
import copy
from pathlib import Path

from .contracts import (canonical, exact_object, identifier, namespace, positive_int,
                        reference, require, sha256)
from .freeze import execution_config, validate_execution
from .kernel_cli import read_json
from .scenario import compile_chain, read_scenario

ROLES = {"solver", "acquisition", "extractor", "consolidation", "reflector",
         "oracle", "evaluator", "proposer", "shadow"}
CONTROLS = {"grade", "corpus", "rebuild", "report", "research_view", "shadow_grade", "proposal_validate"}
TERMINAL_DEPENDENCY_ROLES = {"grade", "report", "shadow_grade", "proposal_validate"}
MODES = {"component_comparison", "independent_learning"}
TERMINAL = {"completed", "failed", "blocked", "interrupted", "timeout", "disabled"}


def digest(value):
    require(type(value) is str and len(value) == 64 and
            all(c in "0123456789abcdef" for c in value), "invalid_schedule_digest")
    return value


def schedule_config(path=None):
    return validate_schedule(read_json(path or Path(__file__).resolve().parent.parent/"configs/schedule_v0.json"))


def validate_schedule(value):
    exact_object(value, {"schema_version", "status", "hypothesis", "default_mode", "max_scenarios",
        "max_repeats", "max_slots", "call_unit", "execution_admitted", "automatic_retry", "target_memory_policy", "shadow_isolation",
        "batch_policy", "control_seconds", "roles"}, "invalid_schedule_config")
    require(value["schema_version"] == "schedule-contract-v0" and value["default_mode"] in MODES,
            "unsupported_schedule_config")
    require(value["call_unit"] == "fresh_codex_session_invocation" and
        value["execution_admitted"] is False and value["automatic_retry"] is False and
        value["target_memory_policy"] == "same_frozen_learning_cutoff" and
        value["shadow_isolation"] == "separate_store_same_role_namespace" and
        value["batch_policy"] == "sequential_worst_case_seconds_no_round_reset", "unsafe_schedule_policy")
    for k in ("max_scenarios", "max_repeats", "max_slots", "control_seconds"):
        positive_int(value[k], "invalid_schedule_limit")
    exact_object(value["roles"], ROLES, "invalid_role_registry")
    for limits in value["roles"].values():
        exact_object(limits, {"input_tokens", "output_tokens", "seconds", "commands"}, "invalid_role_budget")
        for v in limits.values(): positive_int(v, "invalid_role_budget")
    return value


def compile_schedule(registry, memory_refs, scenario_refs, seeds, corpus_sha256,
                     mode=None, config=None, execution=None, scenario_contract=None,
                     meta_memory_ref=None, shadow_memory_ref=None, research_corpora=None):
    """Declare all slots before execution, including conditional L2 and controls."""
    config = validate_schedule(config) if config is not None else schedule_config()
    execution = validate_execution(execution) if execution is not None else execution_config()
    mode = mode or config["default_mode"]
    require(mode in MODES, "invalid_learning_mode")
    digest(corpus_sha256)
    require(type(memory_refs) is list and 1 <= len(memory_refs) <= 8, "invalid_active_population")
    require(type(scenario_refs) is list and 1 <= len(scenario_refs) <= config["max_scenarios"], "invalid_scenario_population")
    for refs, kind in ((memory_refs, "memory"), (scenario_refs, "scenario")):
        require(len({canonical(r) for r in refs}) == len(refs), "duplicate_schedule_arm")
        for ref in refs:
            reference(ref, {kind}); require(registry.resolve(ref)["kind"] == kind, "invalid_schedule_spec")
    require(type(seeds) is list and 1 <= len(seeds) <= config["max_repeats"] and
        all(type(s) is int and s >= 0 for s in seeds) and len(set(seeds)) == len(seeds), "invalid_schedule_seeds")
    require(shadow_memory_ref is None or meta_memory_ref is not None, "shadow_requires_meta_memory")
    for ref in (meta_memory_ref, shadow_memory_ref):
        if ref is not None:
            reference(ref, {"memory"}); registry.resolve(ref)
    if shadow_memory_ref is not None:
        require(shadow_memory_ref != meta_memory_ref, "shadow_candidate_is_current")
    if meta_memory_ref is not None:
        exact_object(research_corpora, {"evaluator", "proposer"}, "missing_research_corpus_snapshots")
        for h in research_corpora.values(): digest(h)
    else: require(research_corpora is None, "unused_research_corpus_snapshots")
    contract = scenario_contract or read_scenario()
    slots, chains, target_grades, known_ids = [], [], [], set()

    def add(id_, role, ns, deps, bindings, condition="always", store_scope="main"):
        identifier(id_); namespace(ns)
        require(id_ not in known_ids, "duplicate_schedule_slot")
        require(len(slots) < config["max_slots"], "schedule_slot_limit")
        require(all(d in known_ids for d in deps), "schedule_dependency_order")
        model = role in ROLES and condition != "disabled"
        budget = copy.deepcopy(config["roles"][role]) if model else {
            "input_tokens": 0, "output_tokens": 0, "seconds": config["control_seconds"], "commands": 0}
        require(budget["input_tokens"] <= execution["episode_input_tokens"] and
            budget["output_tokens"] <= execution["episode_output_tokens"] and
            budget["seconds"] <= execution["episode_seconds"] and
            budget["commands"] <= execution["episode_commands"], "role_budget_exceeds_execution_guard")
        slots.append({"id": id_, "role": role, "namespace": ns, "depends_on": list(dict.fromkeys(deps)),
            "dependency_policy": "terminal" if role in TERMINAL_DEPENDENCY_ROLES else "observable_terminal" if role=="extractor" else "successful",
            "store_scope": store_scope,
            "bindings": copy.deepcopy(bindings), "condition": condition, "fresh_session": model,
            "model_invocations_max": int(model), "budget": budget})
        known_ids.add(id_)
        return id_

    def learned(prefix, ns, episodes, memory):
        previous = []
        for ep in episodes:
            # Rebuild even before the first learning call: independent arms can
            # start with a legally frozen initial corpus, acquisition is memory-off.
            view = maintenance(prefix+"-"+ep["id"], ns, previous, memory) if memory else None
            solver = add(prefix+"-"+ep["id"]+"-solve", "solver" if memory else "acquisition", ns,
                [view] if view else previous, {"episode": ep, "memory": memory,
                "corpus_receipt": previous[-1] if previous else None, "initial_corpus_sha256": corpus_sha256,
                "visibility": "current_task_and_own_memory" if memory else "current_task_only_memory_off"})
            grade = add(solver+"-grade", "grade", "control", [solver],
                {"solver_receipt": solver, "visibility": "controller_private_verifier"})
            extract = add(solver+"-extract", "extractor", ns, [solver, grade],
                {"public_source_receipt": solver, "prior_corpus_receipt": previous[-1] if previous else None,
                 "visibility": "own_observable_events_only_no_private_grade", "fixed_extractor_sha256": sha256(registry.kernel["extractor"])})
            previous = [extract]
        return add(prefix+"-corpus", "corpus", ns, previous,
            {"initial_corpus_sha256": corpus_sha256, "extraction_receipt": previous[-1] if previous else None})

    def maintenance(prefix, ns, deps, memory, store_scope="main", initial_hash=None):
        spec = registry.resolve(memory)["config"]
        common = {"memory": memory, "corpus_receipt": deps[-1] if deps else None,
                  "initial_corpus_sha256": initial_hash or corpus_sha256}
        index = add(prefix+"-index", "consolidation", ns, deps,
            dict(common, visibility="own_active_cards_and_resolvable_sources"),
            "eligible_active_cards" if spec["consolidation.type"] == "single_index" else "disabled", store_scope)
        reflection = add(prefix+"-reflect", "reflector", ns, deps,
            dict(common, visibility="own_eligible_sources_only"),
            "eligible_"+spec["reflection.trigger"] if spec["reflection.trigger"] != "off" else "disabled", store_scope)
        return add(prefix+"-rebuild", "rebuild", ns, deps+[index, reflection],
            dict(common, derived_receipts=[index, reflection]), store_scope=store_scope)

    for scenario_index, scenario_ref in enumerate(scenario_refs):
        for repeat, seed in enumerate(seeds):
            chain = compile_chain(registry, scenario_ref, seed, contract); chains.append(chain)
            prefix = "s"+str(scenario_index)+"-r"+str(repeat)
            learning = [ep for ep in chain["episodes"] if ep["role"] in ("learning", "interference")]
            targets = [ep for ep in chain["episodes"] if ep["role"] not in ("learning", "interference")]
            shared = learned(prefix+"-acq", "task/acq-"+prefix, learning, None) if mode == "component_comparison" else None
            for arm, memory in enumerate(memory_refs):
                arm_prefix = prefix+"-a"+str(arm); ns = "task/"+arm_prefix
                corpus = shared or learned(arm_prefix, ns, learning, memory)
                # Cross-namespace input is declared only for the common acquired
                # corpus; the executor must copy/validate its immutable snapshot.
                view = maintenance(arm_prefix+"-target", ns, [corpus], memory)
                for ep in targets:
                    solve = add(arm_prefix+"-"+ep["id"]+"-solve", "solver", ns, [view],
                        {"episode": ep, "memory": memory, "corpus_receipt": corpus,
                         "runtime_receipt": view, "visibility": "current_task_and_frozen_own_memory"})
                    grade = add(solve+"-grade", "grade", "control", [solve],
                        {"solver_receipt": solve, "visibility": "controller_private_verifier"})
                    target_grades.append(grade)
            for ep in targets:
                oracle = add(prefix+"-"+ep["id"]+"-oracle", "oracle", "task/oracle-"+prefix, [],
                    {"episode": ep, "memory": None, "visibility": "declared_oracle_information_no_gold_no_arms"})
                target_grades.append(add(oracle+"-grade", "grade", "control", [oracle],
                    {"solver_receipt": oracle, "visibility": "controller_private_verifier"}))
    report = add("task-report", "report", "control", target_grades,
        {"mode": mode, "visibility": "controller_private_full_denominator"})
    if meta_memory_ref is not None:
        for role in ("evaluator", "proposer"):
            ns = "research/"+role
            history = add(role+"-view", "research_view", ns, [report],
                {"report_receipt": report, "visibility": "bounded_role_history_no_hidden_answers",
                 "role": role, "parent": scenario_refs[0] if role == "evaluator" else memory_refs[0]})
            extraction = add(role+"-history-extract", "extractor", ns, [history],
                {"public_source_receipt": history, "initial_corpus_sha256": research_corpora[role],
                 "visibility": "own_bounded_history_only_no_private_report",
                 "fixed_extractor_sha256": sha256(registry.kernel["extractor"])})
            research_corpus = add(role+"-history-corpus", "corpus", ns, [extraction],
                {"initial_corpus_sha256": research_corpora[role], "extraction_receipt": extraction})
            meta_runtime = maintenance(role+"-meta", ns, [research_corpus], meta_memory_ref, initial_hash=research_corpora[role])
            proposal = add(role+"-proposal", role, ns, [history, meta_runtime],
                {"research_view_receipt": history, "memory": meta_memory_ref,
                 "runtime_receipt": meta_runtime, "visibility": "own_research_view_and_meta_memory"})
            validation = add(role+"-proposal-validate", "proposal_validate", "control", [proposal],
                {"proposal_receipt": proposal, "role": role, "parent": scenario_refs[0] if role == "evaluator" else memory_refs[0],
                 "visibility": "controller_typed_validation_no_promotion"})
            add(role+"-proposal-extract", "extractor", ns, [proposal, validation],
                {"public_source_receipt": proposal, "prior_corpus_receipt": research_corpus,
                 "visibility": "own_proposal_and_public_validation_only_for_next_round",
                 "fixed_extractor_sha256": sha256(registry.kernel["extractor"])})
            if shadow_memory_ref is not None:
                for label, memory in (("current", meta_memory_ref), ("candidate", shadow_memory_ref)):
                    # Separate stores retain the registered role namespace.
                    scope = role+"-shadow-"+label
                    view = maintenance(scope, ns, [research_corpus], memory, scope, research_corpora[role])
                    call = add(scope+"-call", "shadow", ns, [history, view],
                        {"research_role": role, "research_view_receipt": history, "memory": memory,
                         "runtime_receipt": view, "visibility": "same_history_own_memory_only"}, store_scope=scope)
                    add(call+"-grade", "shadow_grade", "control", [call],
                        {"proposal_receipt": call, "objective": role, "visibility": "independent_controller_verifier"})
    preview = budget_preview(slots, execution)
    payload = {"schema_version": "stage-graph-v0", "category": "prospective_plan_not_execution",
        "mode": mode, "kernel_sha256": registry.fingerprint, "config": config, "execution": execution,
        "scenario_contract_sha256": sha256(contract), "initial_corpus_sha256": corpus_sha256,
        "memory_refs": memory_refs, "scenario_refs": scenario_refs, "seeds": seeds,
        "meta_memory_ref": meta_memory_ref, "shadow_memory_ref": shadow_memory_ref,
        "research_corpora": research_corpora,
        "research_stages": "scheduled" if meta_memory_ref else "not_requested_task_comparison_only",
        "shadow_stages": "scheduled" if shadow_memory_ref else "not_nominated_no_meta_upgrade",
        "chains": chains, "slots": slots, "budget_preview": preview,
        "live_model_calls": 0, "execution_admitted": False, "kernel_frozen": False}
    return {**payload, "sha256": sha256(payload)}


def budget_preview(slots, execution):
    by_role, batches, batch, seconds = {}, [], [], 0
    for n in slots:
        b = n["budget"]; maximum = b["input_tokens"]+b["output_tokens"]
        row = by_role.setdefault(n["role"], {"slots": 0, "session_invocations_max": 0, "tokens_max": 0, "seconds_max": 0})
        row["slots"] += 1; row["session_invocations_max"] += n["model_invocations_max"]
        row["tokens_max"] += maximum; row["seconds_max"] += b["seconds"]
        require(b["seconds"] <= execution["batch_seconds"], "slot_exceeds_batch_time")
        if batch and seconds+b["seconds"] > execution["batch_seconds"]:
            batches.append({"slots": batch, "seconds_max": seconds}); batch, seconds = [], 0
        batch.append(n["id"]); seconds += b["seconds"]
    if batch: batches.append({"slots": batch, "seconds_max": seconds})
    calls = sum(r["session_invocations_max"] for r in by_role.values())
    tokens = sum(r["tokens_max"] for r in by_role.values())
    blockers = []
    if calls > execution["round_calls"]: blockers.append("planned_session_invocations_exceed_round_guard")
    if tokens > execution["round_tokens"]: blockers.append("planned_tokens_exceed_round_guard")
    return {"basis": "prospective_unqualified_maxima", "by_role": by_role, "slots": len(slots),
        "session_invocations_max": calls, "tokens_max": tokens,
        "provider_requests_max": None, "provider_budget_qualified": False,
        "batches": batches, "fits_round_guard": not blockers, "blockers": blockers,
        "live_model_calls": 0}


def validate_graph(graph):
    exact_object(graph, {"schema_version", "category", "mode", "kernel_sha256", "config", "execution",
        "scenario_contract_sha256", "initial_corpus_sha256", "memory_refs", "scenario_refs", "seeds", "meta_memory_ref",
        "shadow_memory_ref", "research_corpora", "research_stages", "shadow_stages", "chains", "slots", "budget_preview",
        "live_model_calls", "execution_admitted", "kernel_frozen", "sha256"}, "invalid_stage_graph")
    require(sha256({k: v for k, v in graph.items() if k != "sha256"}) == graph["sha256"], "stage_graph_drift")
    require(graph.get("schema_version") == "stage-graph-v0" and graph.get("execution_admitted") is False and
        graph.get("category") == "prospective_plan_not_execution" and graph.get("kernel_frozen") is False and
        graph.get("live_model_calls") == 0, "unaccepted_stage_graph")
    require(graph.get("mode") in MODES, "invalid_learning_mode")
    config = validate_schedule(graph["config"])
    execution = validate_execution(graph["execution"])
    require(type(graph["slots"]) is list and 0 < len(graph["slots"]) <= config["max_slots"], "invalid_stage_slots")
    seen = set()
    for n in graph["slots"]:
        exact_object(n, {"id", "role", "namespace", "store_scope", "depends_on", "dependency_policy", "bindings", "condition", "fresh_session", "model_invocations_max", "budget"}, "invalid_stage_slot")
        identifier(n["id"]); namespace(n["namespace"])
        identifier(n["store_scope"])
        require(n["role"] in ROLES|CONTROLS and n["condition"] in {
            "always", "disabled", "eligible_active_cards", "eligible_failure", "eligible_feedback"}, "unregistered_stage")
        require(n["role"] in ROLES or n["condition"] == "always", "unregistered_stage_condition")
        require(n["dependency_policy"] == ("terminal" if n["role"] in TERMINAL_DEPENDENCY_ROLES else
            "observable_terminal" if n["role"]=="extractor" else "successful"), "stage_dependency_policy_drift")
        if n["role"] in {"grade", "report", "shadow_grade", "proposal_validate"}:
            require(n["namespace"] == "control", "stage_role_namespace_mismatch")
        if n["role"] in {"solver", "acquisition", "oracle"}:
            require(n["namespace"].startswith("task/"), "stage_role_namespace_mismatch")
        if n["role"] in {"evaluator", "proposer"}:
            require(n["namespace"] == "research/"+n["role"], "stage_role_namespace_mismatch")
        require(n["id"] not in seen and type(n["depends_on"]) is list and
            len(set(n["depends_on"])) == len(n["depends_on"]) and all(d in seen for d in n["depends_on"]), "schedule_dependency_order")
        require(type(n["bindings"]) is dict and type(n["condition"]) is str, "invalid_stage_binding")
        model = n["role"] in ROLES and n["condition"] != "disabled"
        expected = config["roles"][n["role"]] if model else {"input_tokens": 0, "output_tokens": 0, "seconds": config["control_seconds"], "commands": 0}
        require(n["budget"] == expected and type(n["model_invocations_max"]) is int and
            n["model_invocations_max"] == int(model) and n["fresh_session"] is model, "stage_budget_drift")
        require(all(n["budget"][k] <= execution[guard] for k, guard in (
            ("input_tokens", "episode_input_tokens"), ("output_tokens", "episode_output_tokens"),
            ("seconds", "episode_seconds"), ("commands", "episode_commands"))), "role_budget_exceeds_execution_guard")
        seen.add(n["id"])
    require(graph["budget_preview"] == budget_preview(graph["slots"], graph["execution"]), "stage_preview_drift")
    return graph


class StageJournal:
    """Failure/recovery ledger, unable to assert a live invocation or promotion."""
    def __init__(self, ledger, graph):
        self.ledger, self.graph = ledger, copy.deepcopy(validate_graph(graph))
        self.plan = graph["sha256"]; self.slots = {n["id"]: n for n in graph["slots"]}
        with ledger.mutation():
            ledger.put({"kind": "stage_plan", "id": "stages-"+self.plan, "version": 1, "graph": self.graph}, "control")

    def _finished(self):
        result = {}
        for event in self.ledger.events("control"):
            if event["data"].get("type") != "decision": continue
            r = event["data"]["receipt"]["result"]
            if r.get("status") == "stage_finished" and r.get("plan") == self.plan:
                require(r["slot"] not in result, "duplicate_stage_completion")
                result[r["slot"]] = r
        return result

    def _attempts(self):
        return {v["slot"]: (r, v) for r, v in self.ledger.objects("stage_attempt", "control") if v["plan"] == self.plan}

    def balance(self):
        calls, tokens, unknown = 0, 0, False
        done = self._finished()
        for slot, (_, attempt) in self._attempts().items():
            usage = done.get(slot, {}).get("simulated_usage")
            if usage is None:
                calls += attempt["reserved_calls"]; tokens += attempt["reserved_tokens"]
            else:
                calls += attempt["reserved_calls"] if usage["session_invocations"] is None else usage["session_invocations"]
                if usage["input_tokens"] is None or usage["output_tokens"] is None:
                    tokens += attempt["reserved_tokens"]; unknown = True
                else: tokens += usage["input_tokens"]+usage["output_tokens"]
                unknown |= usage["session_invocations"] is None
        c = self.graph["execution"]
        return {"basis": "scripted_budget_simulation_not_model_usage", "charged_calls": calls,
            "charged_tokens": tokens, "calls_remaining": c["round_calls"]-calls,
            "tokens_remaining": c["round_tokens"]-tokens, "cost_unknown": unknown, "live_model_calls": 0}

    @staticmethod
    def _accepts_dependency(node, result):
        allowed = TERMINAL if node["dependency_policy"]=="terminal" else TERMINAL-{"blocked"} if node["dependency_policy"]=="observable_terminal" else {"completed","disabled"}
        return result["outcome"] in allowed

    def begin(self, request_id, slot, input_sha256, mode="scripted_acceptance"):
        return self._begin(request_id,slot,input_sha256,mode)

    def begin_bound(self,request_id,input_ref,resolver):
        require(resolver.journal is self,"stage_input_journal_mismatch")
        value=resolver.resolve(input_ref);payload=value["payload"]
        require(payload["plan"]==self.plan,"stage_plan_mismatch")
        if payload["action"]=="disabled":return self.close_unrun(request_id,value["slot"])
        return self._begin(request_id,value["slot"],value["input_sha256"],"scripted_acceptance",input_ref,
            payload["action"]=="bound_noop")

    def _begin(self,request_id,slot,input_sha256,mode,bound_input=None,no_model=False):
        identifier(request_id); digest(input_sha256)
        require(mode == "scripted_acceptance", "live_stage_not_accepted")
        require(slot in self.slots, "unplanned_stage")
        n = self.slots[slot]
        require(n["condition"] != "disabled", "disabled_stage_cannot_start")
        request = {"plan": self.plan, "slot": slot, "input_sha256": input_sha256, "mode": mode}
        if bound_input is not None:request["bound_input"]=bound_input
        with self.ledger.mutation():
            prior = self.ledger.receipt(request_id, request)
            if prior: return prior
            finished = self._finished()
            if slot in finished:
                require(finished[slot]["input_sha256"] == input_sha256, "stage_input_drift")
                return self.ledger.commit_receipt(request_id, request, {"status": "stage_cache_hit",
                    "committed": finished[slot], "new_model_calls": 0, "independent_sample": False}, "control")
            require(slot not in self._attempts(), "started_stage_requires_recovery")
            require(all(d in finished and self._accepts_dependency(n,finished[d]) for d in n["depends_on"]), "stage_dependencies_unavailable")
            calls = 0 if no_model else n["model_invocations_max"]
            tokens = 0 if no_model else n["budget"]["input_tokens"]+n["budget"]["output_tokens"]
            balance = self.balance()
            require(calls <= balance["calls_remaining"] and tokens <= balance["tokens_remaining"], "round_budget_exhausted")
            ref = self.ledger.put({"kind": "stage_attempt", "id": "stage-"+sha256(request), "version": 1,
                **request, "reserved_calls": calls, "reserved_tokens": tokens}, "control")
            return self.ledger.commit_receipt(request_id, request, {"status": "stage_started", "attempt": ref,
                "category": "infrastructure_acceptance", "live_model_calls": 0}, "control")

    def complete(self, request_id, attempt_ref, outcome, simulated_usage, raw, artifacts, fault=None):
        identifier(request_id); reference(attempt_ref, {"stage_attempt"})
        a = self.ledger.get(attempt_ref)
        require(a["plan"] == self.plan, "stage_plan_mismatch")
        require(outcome in TERMINAL-{"disabled", "blocked"}, "invalid_stage_outcome")
        exact_object(simulated_usage, {"session_invocations", "input_tokens", "output_tokens"}, "invalid_stage_usage")
        require(all(v is None or (type(v) is int and v >= 0) for v in simulated_usage.values()), "invalid_stage_usage")
        require(type(raw) is list and type(artifacts) is dict, "invalid_stage_artifacts")
        n = self.slots[a["slot"]]
        require(n["model_invocations_max"] != 0 or all(v == 0 for v in simulated_usage.values()), "control_stage_claims_model_usage")
        request = {"attempt": attempt_ref, "outcome": outcome, "simulated_usage": simulated_usage, "raw": raw, "artifacts": artifacts}
        with self.ledger.mutation():
            prior = self.ledger.receipt(request_id, request)
            if prior: return prior
            require(a["slot"] not in self._finished(), "duplicate_stage_completion")
            artifact = self.ledger.put({"kind": "stage_artifact", "id": "result-"+a["id"], "version": 1,
                "attempt": attempt_ref, "raw": raw, "artifacts": artifacts, "category": "infrastructure_acceptance"}, "control")
            if fault: fault("artifacts")
            known = all(v is not None for v in simulated_usage.values())
            within = (known and simulated_usage["session_invocations"] <= a["reserved_calls"] and
                simulated_usage["input_tokens"] <= n["budget"]["input_tokens"] and simulated_usage["output_tokens"] <= n["budget"]["output_tokens"])
            # Unqualified cost cannot become a successful execution, while
            # grade/observable failure extraction remain permitted by policy.
            effective_outcome = "failed" if outcome == "completed" and not within else outcome
            result = {"status": "stage_finished", "plan": self.plan, "slot": a["slot"], "outcome": outcome,
                "input_sha256": a["input_sha256"], "attempt": attempt_ref, "artifact": artifact,
                "simulated_usage": simulated_usage, "budget_qualified": bool(within), "category": "infrastructure_acceptance",
                "live_model_calls": 0, "promotion_eligible": False, "gain_evidence": False}
            result.update(outcome=effective_outcome, requested_outcome=outcome)
            receipt = self.ledger.commit_receipt(request_id, request, result, "control")
            if fault: fault("receipt")
            return receipt

    def recover(self, request_id, attempt_ref):
        a = self.ledger.get(attempt_ref); n = self.slots[a["slot"]]
        usage = {"session_invocations": None, "input_tokens": None, "output_tokens": None} if n["model_invocations_max"] else {
            "session_invocations": 0, "input_tokens": 0, "output_tokens": 0}
        return self.complete(request_id, attempt_ref, "interrupted", usage, [], {"reason": "interrupted_no_automatic_retry"})

    def close_unrun(self, request_id, slot):
        identifier(request_id); require(slot in self.slots, "unplanned_stage")
        n = self.slots[slot]; request = {"plan": self.plan, "slot": slot, "action": "close_unrun"}
        with self.ledger.mutation():
            prior = self.ledger.receipt(request_id, request)
            if prior: return prior
            finished = self._finished()
            require(slot not in finished and slot not in self._attempts(), "stage_already_started_or_finished")
            if n["condition"] == "disabled":
                outcome, reason = "disabled", "mechanism_setting_disabled"
            else:
                failed = [d for d in n["depends_on"] if d in finished and not self._accepts_dependency(n,finished[d])]
                require(bool(failed), "stage_cannot_be_arbitrarily_skipped")
                outcome, reason = "blocked", "failed_dependency"
            result = {"status": "stage_finished", "plan": self.plan, "slot": slot, "outcome": outcome,
                "reason": reason, "input_sha256": None, "simulated_usage": {"session_invocations": 0, "input_tokens": 0, "output_tokens": 0},
                "budget_qualified": True, "category": "infrastructure_acceptance", "live_model_calls": 0,
                "promotion_eligible": False, "gain_evidence": False}
            return self.ledger.commit_receipt(request_id, request, result, "control")

    def report(self):
        finished, attempts = self._finished(), self._attempts()
        rows = [{"id": n["id"], "role": n["role"], "status": finished[n["id"]]["outcome"] if n["id"] in finished else
                 "started" if n["id"] in attempts else "pending", "receipt": finished.get(n["id"])} for n in self.graph["slots"]]
        return {"plan": self.plan, "planned": len(rows), "terminal": len(finished), "rows": rows,
            "budget": self.balance(), "prospective_budget": self.graph["budget_preview"],
            "category": "infrastructure_acceptance", "live_model_calls": 0, "promotion_eligible": False,
            "gain_evidence": False, "kernel_frozen": False}

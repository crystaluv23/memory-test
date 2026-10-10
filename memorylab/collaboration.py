"""Durable controller bridge for real, message-only Codex subagents.

This module never creates synthetic model events or invokes a provider. The
calling Codex controller owns spawn/wait/followup and records their actual IDs
and returned text. Unknown provider usage and instruction-only isolation remain
unqualified. All task execution and semantic commits use existing production
services; structural admission is never a champion decision.
"""
import argparse
import copy
import hashlib
import json
import shlex
import shutil
import sys
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .cards import CanonicalCards, EvidenceStore
from .common import exclusive_run, save, tree_hash
from .contracts import (ROOT, bounded_text, canonical, exact_object, identifier,
                        ContractError, read_kernel, require, sha256, timestamp)
from .kernel_cli import parse_json, read_json
from .ledger import Ledger
from .registry import Registry
from .derived import DerivedStage
from .roles import EvidenceReader, RoleInputs
from .runtime import RuntimeBuilder, RuntimeSession, UsageStore
from .scenario import ScenarioPolicy, compile_chain, materialize, verify
from .workspace import read_workspace_text


def now():
    return datetime.now(timezone.utc).isoformat()


def resolve_capture(ledger, ref):
    """Validate the real collaboration envelope without inventing RPC usage."""
    c = ledger.get(ref)
    require(c["kind"] == "collaboration_capture" and ref["namespace"] == "control", "invalid_collaboration_capture")
    a = ledger.get(c["actor"]); slot = a["id"]
    binding = ledger.receipt("bind-" + slot)
    require(binding is not None and binding["result"]["binding"] == c["binding"], "collaboration_capture_binding_mismatch")
    intent = ledger.receipt(c["intent_id"])
    require(intent is not None and intent["result"]["raw_sha256"] == sha256(c["raw"]) and
            intent["result"]["raw"] == c["raw"], "collaboration_capture_raw_mismatch")
    message = parse_json(c["raw"])
    require(message == {"op": "finish", "result": c["final"]}, "collaboration_capture_final_mismatch")
    require(c["packet"] == a["packet"], "collaboration_capture_packet_mismatch")
    return c


def implementation():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder, pattern in (("memorylab", "*.py"), ("configs", "*.json"), ("docs", "*.md"))
            for p in sorted((ROOT / folder).glob(pattern))}


def environment():
    return {"python": sys.version, "executable": str(Path(sys.executable).resolve()),
            "executable_sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
            "sqlite": sqlite3.sqlite_version, "platform": sys.platform}


def workspace_snapshot(path):
    try:
        return {"hashes": tree_hash(path), "boundary_error": None}
    except ContractError as exc:
        # No parent collection from a rejected filesystem. A failed actor's raw
        # submitted code is retained independently, without reading link targets.
        return {"hashes": None, "boundary_error": exc.code}


INSTRUCTION = """You are an independent experimental role, not a coding assistant for the controller.
Do not call any tools, read files, browse, use global memories, spawn agents or message anyone.
Use only the supplied JSON packet and controller replies. This is a real task:
produce your own solution or role result, never pretend a fixture is inference.
Return exactly one JSON object per turn. To request a service return
{\"op\":\"scenario\",\"request\":{\"op\":\"inspect\" or \"check\"}},
{\"op\":\"write_engine\",\"text\":\"complete engine.py contents\"},
{\"op\":\"open_memory\",\"id\":\"a disclosed memory id\"}, or
{\"op\":\"read_evidence\",\"id\":\"a declared read handle\",\"start\":0,\"count\":2048}.
The controller will execute a permitted service and send its actual reply in a
followup turn of this same session. No controller paths are accessible.
Finish with {\"op\":\"finish\",\"result\":<object matching packet output_schema>}.
For solver, modify engine.py via write_engine, inspect/check the current public
environment as needed, then finish with summary, completion_claim, memory_uses.
Read memory detail before relying on a brief. Check the current rule and reject
inapplicable or stale lessons. A partial result is allowed. For source roles,
use read_evidence and cite exact declared refs; pointers alone are not evidence.
Research roles propose only a legal single-field change or an honest noop.
Do not invent task results or claim your proposal improved anything.
"""


def prepare(path, config=None, initial_specs=None, schedule=None, bootstrap_summaries=None):
    path = Path(path).resolve()
    config = copy.deepcopy(config or read_json(ROOT / "configs/collaboration_v0.json"))
    defaults = read_json(ROOT / "configs/collaboration_v0.json")
    exact_object(config, set(defaults), "invalid_collaboration_config")
    require(config["schema_version"] == "collaboration-observed-v0" and config["fork_turns"] == "none"
            and config["formal_admission"] is False and config["automatic_retry"] is False,
            "unsafe_collaboration_config")
    require(config["transport"] in ("controller_collaboration_tools", "controller_collaboration_tools_with_local_role_ipc", "controller_codex_app_server") and
            config["learning_mode"] == "independent_learning" and config["iterations_max"] == 2 and
            config["actor_roles"] == ["solver", "extractor", "consolidation", "reflector", "evaluator", "proposer"] and
            config["provider_tokens"] is None and config["provider_cost"] is None and
            config["direct_actor_tools"] in ("forbidden_instruction_only_not_os_qualified", "one_controller_client_only_instruction_not_os_qualified"),
            "unsupported_collaboration_route")
    require(config["workspace_policy"] == "persistent_temporary_root_outside_controller_and_project_denials" and
            type(config["workspace_root_mode"]) is int and config["workspace_root_mode"] == 0o700,
            "unsupported_collaboration_workspace_policy")
    require(config["message_delivery_policy"] in ("reserved_exact_followup_actual_tool_return_ack_no_unknown_resend",
            "reserved_exact_ipc_reply_authenticated_stdout_ack_no_unknown_retry") and
            config["termination_policy"] in ("actual_interrupt_receipt_halt_run_preserve_missing_denominator", "native_solver_failure_continue_other_units_preserve_denominator"),
            "unsupported_collaboration_delivery_policy")
    require((config["transport"], config["direct_actor_tools"], config["message_delivery_policy"]) in (
        ("controller_collaboration_tools", "forbidden_instruction_only_not_os_qualified", "reserved_exact_followup_actual_tool_return_ack_no_unknown_resend"),
        ("controller_codex_app_server", "forbidden_instruction_only_not_os_qualified", "reserved_exact_followup_actual_tool_return_ack_no_unknown_resend"),
        ("controller_collaboration_tools_with_local_role_ipc", "one_controller_client_only_instruction_not_os_qualified", "reserved_exact_ipc_reply_authenticated_stdout_ack_no_unknown_retry")),
        "inconsistent_collaboration_route")
    require(config["public_service_grammar"] == "exact_json_examples_finish_result_native_final_v1" and
            config["observable_service_sources"] == "all_acknowledged_public_requests_and_actual_replies_v1",
            "unsupported_collaboration_public_io_policy")
    require(config["initial_packet_delivery"] == "authenticated_own_packet_service_v1",
            "unsupported_initial_packet_delivery")
    require(config["bootstrap_invocation"] == "frozen_complete_exec_command_quoted_heredoc_v1" and
            config["empty_client_input"] == "reject_before_state_and_network_v1",
            "unsupported_bootstrap_invocation")
    require(config["actor_start_policy"] == "native_ready_actual_bind_reserved_go_actual_empty_return_ack_v1" and
            config["memory_usage_policy"] == "actual_disclosure_claimed_use_separate_no_inferred_behavior_link_v1",
            "unsupported_collaboration_start_usage_policy")
    require(all(type(config[k]) is int and config[k] > 0 for k in
            ("agent_slots_max", "actor_turns_max", "actor_wall_seconds", "packet_bytes_max", "response_bytes_max", "engine_bytes_max")),
            "invalid_collaboration_limit")
    require(type(config["seed"]) is int and config["seed"] >= 0, "invalid_collaboration_seed")
    require(initial_specs is None or config["transport"] == "controller_codex_app_server", "native_bootstrap_only")
    if initial_specs is not None:
        exact_object(initial_specs, {"memory", "scenario"}, "invalid_native_bootstrap_specs")
    require(schedule is None or config["transport"] == "controller_codex_app_server" and
            schedule in (["challenge-1", "proposer-1", "candidate-1"], ["proposer-1", "candidate-1"]), "invalid_native_continuation_schedule")
    require(bootstrap_summaries is None or config["transport"] == "controller_codex_app_server" and
            schedule == ["proposer-1", "candidate-1"] and set(bootstrap_summaries) == {"challenge-1"}, "invalid_native_bootstrap_summaries")
    require(config["termination_policy"] != "native_solver_failure_continue_other_units_preserve_denominator" or
            config["transport"] == "controller_codex_app_server", "native_failure_policy_only")
    require(not path.exists(), "collaboration_run_exists")
    path.mkdir(parents=True)
    workspace_root = Path(tempfile.mkdtemp(prefix="memorylab-collaboration-subjects-")).resolve()
    workspace_root.chmod(config["workspace_root_mode"])
    client_root = Path(tempfile.mkdtemp(prefix="mlipc-", dir="/tmp")).resolve()
    client_root.chmod(0o700)
    require(not workspace_root.is_relative_to(path) and not workspace_root.is_relative_to(Path.home() / "Project"),
            "collaboration_workspace_inside_denied_root")
    hashes = implementation()
    for name in hashes:
        target = path / "source-snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    plan = {"schema_version": "collaboration-plan-v0", "config": config, "implementation": hashes,
            "kernel": read_kernel(), "environment": environment(), "workspace_root": str(workspace_root), "client_root": str(client_root),
            "created_at": now(), "category": "collaboration_observed",
            "formal_admission": False, "provider_tokens": None, "provider_cost": None,
            "schedule": schedule or ["baseline", "proposer-0", "candidate-0", "evaluator-0", "challenge-1", "proposer-1", "candidate-1"],
            **({"bootstrap_specs": initial_specs} if initial_specs is not None else {})}
    if bootstrap_summaries is not None: plan["bootstrap_summaries"] = bootstrap_summaries
    plan["sha256"] = sha256(plan)
    save(path / "plan.json", plan)
    with Loop(path) as loop:
        memory = loop.registry.register_root("memory", "initial-memory", initial_specs["memory"] if initial_specs else None)
        scenario = loop.registry.register_root("scenario", "initial-scenario", initial_specs["scenario"] if initial_specs else None)
        with loop.ledger.transaction():
            loop.ledger.commit_receipt("initial-specs", {"plan": plan["sha256"]},
                                       {"memory": memory, "scenario": scenario}, "control")
    return {"run": str(path), "plan": plan["sha256"], "real_agents_dispatched": 0}


class Loop:
    def __init__(self, path, readonly=False):
        self.path = Path(path).resolve()
        self.plan = read_json(self.path / "plan.json")
        value = dict(self.plan); digest = value.pop("sha256")
        require(sha256(value) == digest, "collaboration_plan_drift")
        require(self.plan["environment"] == environment(), "collaboration_environment_drift")
        for name, expected in self.plan["implementation"].items():
            require(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected,
                    "collaboration_implementation_drift", name)
            require(hashlib.sha256((self.path / "source-snapshot" / name).read_bytes()).hexdigest() == expected,
                    "collaboration_snapshot_drift", name)
        self.config = self.plan["config"]
        self.ledger = Ledger(self.path / "ledger.sqlite", readonly=readonly)
        self.registry = Registry(self.ledger, self.plan["kernel"])
        self.evidence = EvidenceStore(self.ledger, self.plan["kernel"])
        self.cards = CanonicalCards(self.evidence)
        self.inputs = RoleInputs(self.cards, self.registry)

    def __enter__(self): return self
    def __exit__(self, *_): self.ledger.close()

    def _commit(self, key, request, result):
        with self.ledger.mutation():
            old = self.ledger.receipt(key, request)
            return old or self.ledger.commit_receipt(key, request, result, "control")

    def _require_execution(self):
        require(not (self.path / "revocation.json").exists(), "collaboration_execution_revoked")

    def _unit_failure(self, slot):
        if self.config["termination_policy"] != "native_solver_failure_continue_other_units_preserve_denominator": return False
        receipt = self.ledger.receipt("terminate-" + slot)
        if receipt is None: return False
        t = self.ledger.get(receipt["result"]["termination"])
        proof = t["actual_interrupt_tool_result"]
        return (self._actor(slot)["internal"]["role"] == "solver" and t["reason"] == "actor_failed" and
                proof.get("transport_failure_only") is True and proof.get("exit", {}).get("exit_code") is not None and
                proof.get("failure", "").startswith(("native_connection_error_stop_no_retry", "rpc_role_timeout", "native_turn_failed")))

    def _halted(self):
        return any(not self._unit_failure(t["id"]) for _, t in self.ledger.objects("collaboration_termination", "control"))

    def initial(self): return self.ledger.receipt("initial-specs")["result"]

    def phase(self, name, memory, scenario, seed):
        self._require_execution()
        identifier(name)
        require(name in self.plan["schedule"], "unplanned_collaboration_phase")
        request = {"name": name, "memory": memory, "scenario": scenario, "seed": seed}
        old = self.ledger.receipt("phase-" + name, request)
        if old: return old["result"]
        chain = compile_chain(self.registry, scenario, seed)
        result = {**request, "chain": chain, "namespace": "task/" + name,
                  "verdict": "observational_not_promoted"}
        return self._commit("phase-" + name, request, result)["result"]

    def _phase(self, name):
        receipt = self.ledger.receipt("phase-" + name)
        require(receipt is not None, "unknown_collaboration_phase")
        return receipt["result"]

    def _actor(self, slot):
        identifier(slot)
        r = self.ledger.receipt("actor-" + slot)
        require(r is not None, "unknown_collaboration_actor")
        return r["result"]

    def _workspace_root(self):
        root = Path(self.plan["workspace_root"])
        require(root.is_absolute() and not root.is_symlink() and root.is_dir() and
                root.resolve() == root and not root.is_relative_to(self.path) and
                not root.is_relative_to(Path.home().resolve() / "Project"), "invalid_collaboration_workspace_root")
        return root

    def workspace(self, a):
        require(a["internal"]["role"] == "solver", "actor_has_no_workspace")
        root = self._workspace_root()
        require(a["internal"]["workspace"] == str(root / a["id"]), "collaboration_workspace_binding_drift")
        return root / a["id"]

    def _client_root(self):
        root = Path(self.plan["client_root"])
        require(root.is_absolute() and root.is_dir() and not root.is_symlink() and root.resolve() == root and
                not root.is_relative_to(self.path) and not root.is_relative_to(Path.home().resolve() / "Project"),
                "invalid_collaboration_client_root")
        return root

    def _cutoff(self, phase, index):
        p = self._phase(phase)
        ep = p["chain"]["episodes"][index]
        label = "target" if ep["role"] in ("target", "counterexample") else str(index)
        key = "input-cutoff-" + phase + "-" + label
        old = self.ledger.receipt(key)
        if old is None:
            old = self._commit(key, {"phase": phase, "label": label}, {"cutoff": self.ledger.head()})
        return label, old["result"]["cutoff"]

    def _messages(self, slot):
        return [e["data"]["receipt"]["result"] for e in self.ledger.events("control")
                if e["data"].get("type") == "decision" and
                e["data"]["receipt"]["result"].get("actor_slot") == slot and
                e["data"]["receipt"]["request_id"] == "reply-" + slot + "-" + str(e["data"]["receipt"]["result"].get("sequence"))]

    def _register_actor(self, slot, packet, internal):
        self._require_execution()
        identifier(slot)
        if self.config["transport"] == "controller_collaboration_tools_with_local_role_ipc":
            old = self.ledger.receipt("actor-" + slot)
            if old: return old["result"]
            from .collaboration_ipc import prepare_client
            public, private = prepare_client(self._client_root(), slot, self.plan["environment"]["executable"],
                                             self.config["response_bytes_max"])
            packet = copy.deepcopy(packet); internal = copy.deepcopy(internal)
            packet["service_client"] = public; internal["service_client"] = private
            packet["instruction"] = """You are an independent experimental role. Use only this JSON packet and replies.
You may use functions.exec solely to invoke exec_command with the exact registered
service_client.command and a single JSON request on stdin (a quoted heredoc is
permitted). Use the functions.exec first-line directive
// @exec: {"max_output_tokens":40000}
and exec_command max_output_tokens=40000, then text() its actual result.
Do not execute any other program, read
files, use global memories, browse, spawn agents or message anyone. The client
itself is the only allowed local file execution. Do not edit the client or its
state. No controller paths, hidden answers or other arms are authorized.
This is real inference. Produce your own solution or role result, not fixtures.
Send one of these JSON requests to the client: scenario request inspect/check,
write_engine with full text, open_memory with disclosed id, read_evidence with
declared id/start/count, or finish with result matching output_schema.
Exact request shapes (no additional keys):
{"op":"packet"}
{"op":"scenario","request":{"op":"inspect"}}
{"op":"scenario","request":{"op":"check"}}
{"op":"write_engine","text":"complete engine.py contents"}
{"op":"open_memory","id":"disclosed memory id"}
{"op":"read_evidence","id":"declared read handle","start":0,"count":2048}
{"op":"finish","result":<object matching output_schema>}
Solver must write engine.py via write_engine, check actual execution, then finish
with summary/completion_claim/memory_uses. Source roles must read evidence and
cite declared exact refs. Read memory detail before relying on a brief; check
current rules and stale/inapplicable lessons. Researchers make one legal field
change or honest noop and never claim improvement without independent evidence.
After the client accepts finish, return the same result object as your native
agent final, matching output_schema. The op/ result envelope is client-only.
Do not retry a disconnected/uncertain client send; return a partial failure.
"""
        bounded_text(canonical(packet).decode(), self.config["packet_bytes_max"], "collaboration_packet_limit")
        request = {"packet": packet, "internal": internal, "plan": self.plan["sha256"]}
        old = self.ledger.receipt("actor-" + slot, request)
        if old: return old["result"]
        require(len(self.ledger.objects("collaboration_actor", "control")) < self.config["agent_slots_max"],
                "collaboration_agent_limit")
        value = {"kind": "collaboration_actor", "id": slot, "version": 1,
                 "packet": packet, "packet_sha256": sha256(packet), "internal": internal,
                 "created_at": now(), "plan": self.plan["sha256"]}
        with self.ledger.transaction():
            ref = self.ledger.put(value, "control")
            return self.ledger.commit_receipt("actor-" + slot, request, {**value, "ref": ref}, "control")["result"]

    def task(self, phase, index):
        self._require_execution()
        p = self._phase(phase); episodes = p["chain"]["episodes"]
        require(type(index) is int and 0 <= index < len(episodes), "invalid_collaboration_episode")
        slot = phase + "-task-" + str(index)
        old = self.ledger.receipt("actor-" + slot)
        if old: return old["result"]
        for prior in range(index):
            previous = phase + "-task-" + str(prior)
            require(self.ledger.receipt("done-" + previous) is not None or self._unit_failure(previous), "previous_task_not_complete")
            if episodes[prior]["role"] in ("learning", "interference"):
                require(self.ledger.receipt("done-" + phase + "-extract-" + str(prior)) is not None or self._unit_failure(previous),
                        "previous_extraction_not_complete")
        ws = self._workspace_root() / slot
        require(not ws.exists(), "uncommitted_workspace_needs_attention")
        label, cutoff = self._cutoff(phase, index)
        derived_refs = {}
        stage = DerivedStage(self.cards, self.registry)
        for role in ("consolidation", "reflector"):
            if stage.prepare(role, p["namespace"], cutoff, p["memory"])["payload"]["scheduled"]:
                done = self.ledger.receipt("done-" + phase + "-" + role + "-" + label)
                require(done is not None, "scheduled_derived_not_complete")
                r = done["result"]["materialization"]["result"]
                require(r["status"] in ("accepted", "noop"), "derived_materialization_rejected")
                derived_refs[role] = r["batch"]
        projection = RuntimeBuilder(self.cards, self.registry).build(p["namespace"], cutoff, p["memory"], derived_refs=derived_refs)["projection_ref"]
        protected = materialize(ws, episodes[index])
        ws.chmod(0o700)
        session = RuntimeSession(self.ledger, projection, (ws / "SPEC.md").read_text())
        brief = json.loads(session.briefs())
        public = self.inputs.task("solver", p["namespace"], cutoff, ws, episodes[index], brief)
        packet = {"role_input": public, "files": {n: (ws / n).read_text() for n in
                  ("engine.py", "run.py", "SPEC.md", "sample_input.json")},
                  "output_schema": public["payload"]["output_schema"], "instruction": INSTRUCTION}
        return self._register_actor(slot, packet, {"role": "solver", "phase": phase, "episode": episodes[index],
                 "namespace": p["namespace"], "cutoff": cutoff, "projection": projection,
                 "workspace": str(ws), "protected": protected, "initial_engine": (ws / "engine.py").read_text()})

    def extractor(self, phase, index):
        self._require_execution()
        slot = phase + "-extract-" + str(index); task_slot = phase + "-task-" + str(index)
        old = self.ledger.receipt("actor-" + slot)
        if old: return old["result"]
        done = self.ledger.receipt("done-" + task_slot)
        require(done is not None, "task_not_complete")
        a = self._actor(task_slot); cutoff = self.ledger.head()
        require(a["internal"]["episode"]["role"] in ("learning", "interference"),
                "target_extraction_not_in_learning_plan")
        packet, reader = self.inputs.extractor(a["internal"]["namespace"], cutoff, done["result"]["sources"])
        return self._register_actor(slot, {"role_input": packet, "instruction": INSTRUCTION,
                  "output_schema": packet["payload"]["output_schema"]},
                 {"role": "extractor", "namespace": a["internal"]["namespace"], "cutoff": cutoff,
                  "source_refs": done["result"]["sources"], "task_slot": task_slot})

    def summary(self, phase):
        if phase in self.plan.get("bootstrap_summaries", {}): return copy.deepcopy(self.plan["bootstrap_summaries"][phase])
        p = self._phase(phase); rows = []
        for index, episode in enumerate(p["chain"]["episodes"]):
            slot = phase + "-task-" + str(index)
            done = self.ledger.receipt("done-" + slot)
            terminated = self.ledger.receipt("terminate-" + slot)
            rows.append({"episode_role": episode["role"],
                         "status": "completed" if done else "terminated" if terminated else "missing",
                         "full_success": done["result"]["grade"]["full_success"] if done else False if terminated else None})
        return {"phase": phase, "scenario_config": self.registry.resolve(p["scenario"])["config"],
                "memory_config": self.registry.resolve(p["memory"])["config"], "rows": rows,
                "planned": len(rows), "completed": sum(r["status"] == "completed" for r in rows),
                "terminated": sum(r["status"] == "terminated" for r in rows),
                "successes": sum(r["full_success"] is True for r in rows),
                "interpretation": "observed_only_no_causal_gain_or_promotion"}

    def research(self, slot, role, parent, phases):
        self._require_execution()
        old = self.ledger.receipt("actor-" + slot)
        if old: return old["result"]
        require(role in ("evaluator", "proposer"), "invalid_collaboration_research_role")
        summaries = [self.summary(p) for p in phases]
        require(all(s["planned"] == s["completed"] + (s["terminated"] if self.config["termination_policy"] == "native_solver_failure_continue_other_units_preserve_denominator" else 0) for s in summaries), "incomplete_research_input")
        ns = "research/" + role
        source = self.evidence.register("research-source-" + slot, ns, "visible_outcome", slot, now(),
                                       canonical(summaries).decode())
        cutoff = self.ledger.head()
        own_spec = self.initial()["memory"]
        projection = RuntimeBuilder(self.cards, self.registry).build(ns, cutoff, own_spec)["projection_ref"]
        runtime = RuntimeSession(self.ledger, projection, "research " + role)
        briefs = json.loads(runtime.briefs())
        public = self.inputs.research(role, ns, cutoff, parent, [source], briefs)
        packet = {"role_input": public, "instruction": INSTRUCTION,
                  "output_schema": public["payload"]["output_schema"]}
        return self._register_actor(slot, packet, {"role": role, "namespace": ns, "cutoff": cutoff,
                    "projection": projection, "source_refs": [source], "parent": parent})

    def research_extract(self, research_slot):
        self._require_execution()
        slot = research_slot + "-extract"
        old = self.ledger.receipt("actor-" + slot)
        if old: return old["result"]
        done = self.ledger.receipt("done-" + research_slot)
        require(done is not None, "research_not_complete")
        a = self._actor(research_slot); i = a["internal"]; cutoff = self.ledger.head()
        packet, _ = self.inputs.extractor(i["namespace"], cutoff, done["result"]["sources"])
        return self._register_actor(slot, {"role_input": packet, "instruction": INSTRUCTION,
                 "output_schema": packet["payload"]["output_schema"]},
                {"role": "extractor", "namespace": i["namespace"], "cutoff": cutoff,
                 "source_refs": done["result"]["sources"], "research_slot": research_slot})

    def derived(self, phase, role, cutoff, label):
        self._require_execution()
        p = self._phase(phase); slot = phase + "-" + role + "-" + label
        old = self.ledger.receipt("actor-" + slot)
        if old: return old["result"]
        packet, _ = self.inputs.derived(role, p["namespace"], cutoff, p["memory"])
        input_ = packet["payload"]["context"]["derived_input"]
        require(input_["payload"]["scheduled"], "unscheduled_collaboration_derived")
        return self._register_actor(slot, {"role_input": packet, "instruction": INSTRUCTION,
                 "output_schema": packet["payload"]["output_schema"]},
                {"role": role, "namespace": p["namespace"], "cutoff": cutoff,
                 "source_refs": [s["ref"] for s in input_["payload"]["sources"]], "derived_input": input_})

    def dispatch_intent(self, slot):
        self._require_execution()
        require(not self._halted(), "collaboration_run_terminated")
        a = self._actor(slot)
        message = self.prompt(slot)
        if self.config["transport"] == "controller_collaboration_tools_with_local_role_ipc":
            command = " ".join(shlex.quote(x) for x in a["packet"]["service_client"]["command"])
            invocation = {"cmd": command + " <<'MEMORYLAB_PACKET_REQUEST'\n{\"op\":\"packet\"}\nMEMORYLAB_PACKET_REQUEST",
                          "login": False, "max_output_tokens": 40000, "yield_time_ms": 10000}
            message = canonical({"instruction": "Independent experimental role. Do not call any tools yet. First return exactly ready_result as your native final. Wait for a follow-up with status CONTROLLER_BOUND and this expected_packet_sha256. Only after that, call functions.exec with // @exec: {\"max_output_tokens\":40000} then text(await tools.exec_command(bootstrap_exec_command)); use the exact supplied bootstrap_exec_command including its stdin heredoc. This obtains your public task and instructions. Do not read any other file, global memory, parent chat or other role; do not browse, spawn or message agents. Follow the returned packet; only the registered client may be executed. Do not retry uncertain sends.",
                "ready_result": {"status": "awaiting_controller_binding", "packet_sha256": a["packet_sha256"]},
                "bootstrap_exec_command": invocation, "service_client": a["packet"]["service_client"],
                "expected_packet_sha256": a["packet_sha256"]}).decode()
        return self._commit("dispatch-" + slot, {"actor": a["ref"], "fork_turns": "none"},
                            {"status": "dispatch_reserved", "packet_sha256": a["packet_sha256"],
                             "tool_request": {"task_name": "subject_" + self.plan["sha256"][:12] + "_" + slot.replace("-", "_"),
                                              "fork_turns": "none", "message": message}})

    def bind(self, slot, agent_id, tool_result):
        self._require_execution()
        a = self._actor(slot)
        require(self.ledger.receipt("dispatch-" + slot) is not None, "dispatch_not_reserved")
        bounded_text(agent_id, 200, "invalid_agent_handle")
        native = self.config["transport"] == "controller_codex_app_server"
        require(type(tool_result) is dict and (tool_result.get("thread", {}).get("id") if native else tool_result.get("agent_id", tool_result.get("task_name"))) == agent_id,
                "spawn_handle_mismatch")
        if native:
            thread = tool_result["thread"]
            require(thread.get("ephemeral") is True and not thread.get("turns") and
                    not thread.get("forkedFromId") and not thread.get("parentThreadId") and
                    tool_result.get("instructionSources") == [], "native_thread_not_fresh")
        for ref, other in self.ledger.objects("collaboration_binding", "control"):
            require(other["agent_id"] != agent_id or other["slot"] == slot, "agent_reused_across_slots")
        request = {"actor": a["ref"], "agent_id": agent_id, "tool_result": tool_result}
        old = self.ledger.receipt("bind-" + slot, request)
        if old: return old
        with self.ledger.transaction():
            ref = self.ledger.put({"kind": "collaboration_binding", "id": slot, "version": 1,
                                   "slot": slot, "bound_at": now(), **request}, "control")
            return self.ledger.commit_receipt("bind-" + slot, request, {"binding": ref}, "control")

    def start_intent(self, slot, native_ready):
        self._require_execution()
        require(self.config["transport"] == "controller_collaboration_tools_with_local_role_ipc", "start_route_not_selected")
        a = self._actor(slot); binding = self.ledger.receipt("bind-" + slot)
        require(binding is not None, "collaboration_sender_unbound")
        b = self.ledger.get(binding["result"]["binding"])
        exact_object(native_ready, {"agent_name", "agent_status"}, "invalid_native_ready")
        require(native_ready["agent_name"] == b["agent_id"] and type(native_ready["agent_status"]) is dict,
                "collaboration_ready_sender_mismatch")
        exact_object(native_ready["agent_status"], {"completed"}, "invalid_native_ready")
        require(parse_json(native_ready["agent_status"]["completed"]) ==
                {"status": "awaiting_controller_binding", "packet_sha256": a["packet_sha256"]},
                "collaboration_ready_packet_mismatch")
        request = {"actor": a["ref"], "binding": binding["result"]["binding"], "native_ready": native_ready}
        old = self.ledger.receipt("start-" + slot, request)
        if old:
            return {**old, "dispatch_status": "already_acknowledged_do_not_resend" if self.ledger.receipt("start-ack-" + slot) else "start_unknown_do_not_resend"}
        require((timestamp(now()) - timestamp(b["bound_at"])).total_seconds() <= self.config["actor_wall_seconds"],
                "collaboration_wall_deadline")
        require(not self._messages(slot) and not self.ledger.receipt("done-" + slot) and
                not self._halted(), "actor_already_started_or_terminal")
        message = canonical({"status": "CONTROLLER_BOUND", "packet_sha256": a["packet_sha256"]}).decode()
        r = self._commit("start-" + slot, request, {"status": "start_reserved",
                         "binding": binding["result"]["binding"], "native_ready": native_ready,
                         "tool_request": {"target": b["agent_id"], "message": message}})
        return {**r, "dispatch_status": "call_start_once"}

    def acknowledge_start(self, slot, agent_id, tool_result):
        a = self._actor(slot); start = self.ledger.receipt("start-" + slot)
        require(start is not None and start["result"]["tool_request"]["target"] == agent_id, "collaboration_start_target_mismatch")
        require(type(tool_result) is str and tool_result == "", "invalid_native_start_tool_return")
        record = {"actor": a["ref"], "agent_id": agent_id, "tool_result": tool_result,
                  "start_request_sha256": start["request_sha256"]}
        return self._commit("start-ack-" + slot, record, {"status": "actual_empty_native_tool_return_not_context_attestation", "record": record})

    def prompt(self, slot): return canonical(self._actor(slot)["packet"]).decode()

    def _public_reply(self, response):
        return response["wire"] if "wire" in response else canonical(response).decode()

    def followup_intent(self, slot, sequence):
        self._require_execution()
        a = self._actor(slot)
        require(self.config["transport"] in ("controller_collaboration_tools", "controller_codex_app_server"), "manual_followup_route_not_selected")
        old = self.ledger.receipt("send-" + slot + "-" + str(sequence))
        if old:
            return {**old, "dispatch_status": "already_acknowledged_do_not_resend" if self._delivery(slot, sequence) else "delivery_unknown_do_not_resend"}
        require(self.ledger.receipt("done-" + slot) is None and self.ledger.receipt("terminate-" + slot) is None,
                "actor_already_terminal")
        require(not self._halted(), "collaboration_run_terminated")
        binding = self.ledger.receipt("bind-" + slot)
        require(binding is not None, "collaboration_sender_unbound")
        messages = self._messages(slot)
        require(type(sequence) is int and 1 <= sequence == len(messages), "invalid_collaboration_delivery_sequence")
        b = self.ledger.get(binding["result"]["binding"])
        require((timestamp(now()) - timestamp(b["bound_at"])).total_seconds() <= self.config["actor_wall_seconds"],
                "collaboration_wall_deadline")
        require(sequence < self.config["actor_turns_max"], "collaboration_turn_limit")
        response = messages[-1]["response"]
        request = {"actor": a["ref"], "sequence": sequence, "binding": binding["result"]["binding"],
                   "reply": self.ledger.receipt("reply-" + slot + "-" + str(sequence))["request_sha256"]}
        receipt = self._commit("send-" + slot + "-" + str(sequence), request,
                    {"status": "followup_reserved_do_not_repeat_unknown", "tool_request": {
                        "target": b["agent_id"], "message": self._public_reply(response)}})
        return {**receipt, "dispatch_status": "call_followup_once"}

    def client_delivery_intent(self, slot, sequence):
        self._require_execution()
        a = self._actor(slot)
        require(self.config["transport"] == "controller_collaboration_tools_with_local_role_ipc", "ipc_route_not_selected")
        messages = self._messages(slot)
        require(type(sequence) is int and 1 <= sequence == len(messages), "invalid_collaboration_delivery_sequence")
        binding = self.ledger.receipt("bind-" + slot); require(binding is not None, "collaboration_sender_unbound")
        b = self.ledger.get(binding["result"]["binding"])
        request = {"actor": a["ref"], "sequence": sequence, "binding": binding["result"]["binding"],
                   "reply": self.ledger.receipt("reply-" + slot + "-" + str(sequence))["request_sha256"], "route": "local_role_ipc"}
        old = self.ledger.receipt("send-" + slot + "-" + str(sequence), request)
        if old: return {**old, "dispatch_status": "ipc_send_unknown_do_not_repeat"}
        receipt = self._commit("send-" + slot + "-" + str(sequence), request,
                    {"route": "local_role_ipc", "delivery_payload": {"target": b["agent_id"],
                     "message": self._public_reply(messages[-1]["response"])}})
        return {**receipt, "dispatch_status": "call_client_send_once"}

    def acknowledge_client(self, slot, sequence, agent_id, observed, request_frame_sha256, ack_frame_sha256):
        reserved = self.ledger.receipt("send-" + slot + "-" + str(sequence))
        require(reserved is not None and reserved["result"].get("route") == "local_role_ipc", "ipc_send_not_reserved")
        payload = reserved["result"]["delivery_payload"]
        reply = self.ledger.receipt("reply-" + slot + "-" + str(sequence))["result"]
        require(payload["target"] == agent_id and observed == {"sequence": sequence,
                "raw_sha256": hashlib.sha256(reply["raw"].encode()).hexdigest(),
                "wire_sha256": hashlib.sha256(payload["message"].encode()).hexdigest()}, "ipc_ack_mismatch")
        request = {"agent_id": agent_id, "sequence": sequence, "send_request_sha256": reserved["request_sha256"],
                   "observed_client_receipt": observed, "request_frame_sha256": request_frame_sha256,
                   "ack_frame_sha256": ack_frame_sha256}
        result = self._commit("delivered-" + slot + "-" + str(sequence), request,
                    {"status": "ipc_client_stdout_ack_not_model_context_or_provider_tool_return", "record": request})
        self._delivery(slot, sequence)
        return result

    def _delivery(self, slot, sequence):
        ack = self.ledger.receipt("delivered-" + slot + "-" + str(sequence))
        if ack is None: return None
        reserved = self.ledger.receipt("send-" + slot + "-" + str(sequence))
        binding = self.ledger.receipt("bind-" + slot)
        reply = self.ledger.receipt("reply-" + slot + "-" + str(sequence))
        require(reserved is not None and binding is not None and reply is not None, "collaboration_delivery_missing_dependency")
        agent_id = self.ledger.get(binding["result"]["binding"])["agent_id"]
        payload = reserved["result"].get("delivery_payload", reserved["result"].get("tool_request"))
        require(payload == {"target": agent_id, "message": self._public_reply(reply["result"]["response"])} and
                ack["result"]["record"]["send_request_sha256"] == reserved["request_sha256"] and
                ack["result"]["record"]["agent_id"] == agent_id and
                ack["request_sha256"] == sha256(ack["result"]["record"]), "collaboration_delivery_binding_mismatch")
        return ack

    def acknowledge_followup(self, slot, sequence, agent_id, tool_result):
        require(self.config["transport"] in ("controller_collaboration_tools", "controller_codex_app_server"), "manual_followup_route_not_selected")
        reserved = self.ledger.receipt("send-" + slot + "-" + str(sequence))
        require(reserved is not None, "collaboration_followup_not_reserved")
        require(reserved["result"]["tool_request"]["target"] == agent_id, "collaboration_sender_mismatch")
        require(type(tool_result) is dict and bool(tool_result) and not tool_result.get("error") and
                tool_result.get("agent_id", agent_id) == agent_id, "invalid_collaboration_followup_result")
        if self.config["transport"] == "controller_codex_app_server":
            require(type(tool_result.get("turn", {}).get("id")) is str and
                    tool_result.get("thread_id") == agent_id, "native_delivery_thread_mismatch")
        bounded_text(canonical(tool_result).decode(), self.config["response_bytes_max"], "collaboration_response_limit")
        request = {"agent_id": agent_id, "sequence": sequence, "tool_result": tool_result,
                   "send_request_sha256": reserved["request_sha256"]}
        result = self._commit("delivered-" + slot + "-" + str(sequence), request,
                             {"status": "native_turn_start_acknowledged_not_model_comprehension" if self.config["transport"] == "controller_codex_app_server" else "controller_tool_return_acknowledged_not_model_comprehension", "record": request})
        self._delivery(slot, sequence)
        return result

    def interrupt_intent(self, slot, reason):
        a = self._actor(slot); binding = self.ledger.receipt("bind-" + slot)
        require(binding is not None, "collaboration_sender_unbound")
        require(reason in ("wall_deadline", "turn_limit", "actor_failed", "actor_tool_violation"),
                "invalid_collaboration_termination_reason")
        b = self.ledger.get(binding["result"]["binding"])
        request = {"actor": a["ref"], "binding": binding["result"]["binding"], "reason": reason}
        old = self.ledger.receipt("interrupt-" + slot, request)
        if old:
            return {**old, "dispatch_status": "already_terminal" if self.ledger.receipt("terminate-" + slot) else "interrupt_unknown_do_not_repeat"}
        require(self.ledger.receipt("done-" + slot) is None, "actor_already_terminal")
        if reason == "wall_deadline":
            require((timestamp(now()) - timestamp(b["bound_at"])).total_seconds() > self.config["actor_wall_seconds"],
                    "collaboration_deadline_not_reached")
        if reason == "turn_limit":
            require(len(self._messages(slot)) >= self.config["actor_turns_max"], "collaboration_turn_limit_not_reached")
        receipt = self._commit("interrupt-" + slot, request,
                    {"status": "interrupt_reserved", "tool_request": {"target": b["agent_id"]}})
        return {**receipt, "dispatch_status": "call_interrupt_once"}

    def terminate(self, slot, agent_id, reason, tool_result):
        a = self._actor(slot); binding = self.ledger.receipt("bind-" + slot)
        require(binding is not None and self.ledger.get(binding["result"]["binding"])["agent_id"] == agent_id,
                "collaboration_sender_mismatch")
        reserved = self.ledger.receipt("interrupt-" + slot,
                    {"actor": a["ref"], "binding": binding["result"]["binding"], "reason": reason})
        require(reserved is not None, "collaboration_interrupt_not_reserved")
        require(type(tool_result) is dict and bool(tool_result) and not tool_result.get("error"),
                "invalid_collaboration_interrupt_result")
        bounded_text(canonical(tool_result).decode(), self.config["response_bytes_max"], "collaboration_response_limit")
        request = {"actor": a["ref"], "binding": binding["result"]["binding"], "agent_id": agent_id,
                   "reason": reason, "actual_interrupt_tool_result": tool_result}
        old = self.ledger.receipt("terminate-" + slot, request)
        if old: return old
        require(self.ledger.receipt("done-" + slot) is None, "actor_already_terminal")
        with self.ledger.transaction():
            ref = self.ledger.put({"kind": "collaboration_termination", "id": slot, "version": 1,
                    **request, "at": now(), "final": None, "provider_tokens": None, "provider_cost": None,
                    "workspace_snapshot": workspace_snapshot(self.workspace(a)) if a["internal"]["role"] == "solver" else None}, "control")
            return self.ledger.commit_receipt("terminate-" + slot, request,
                    {"termination": ref, "status": "terminated_run_halted", "full_success": False,
                     "formal_admission": False, "gain_evidence": False}, "control")

    def _selected_candidate(self, slot, fallback):
        done = self.ledger.receipt("done-" + slot)
        require(done is not None, "research_branch_not_complete")
        r = done["result"]["materialization"]["result"]
        return r["candidate"] if r["status"] == "accepted" else fallback

    def next(self):
        """Advance only from committed outcomes, and return the next real actor.

        This is an experimental branch schedule, not the formal epoch/champion
        state machine. No accepted proposal alone can produce a gain verdict.
        """
        self._require_execution()
        if self._halted():
            return {"status": "observed_run_terminated", "report": self.report(), "delivery_ready": False}
        initial = self.initial(); memory = initial["memory"]; scenario = initial["scenario"]
        names = self.plan["schedule"]
        for name in names:
            if name.startswith("proposer-") or name.startswith("evaluator-"):
                if not self.ledger.receipt("done-" + name):
                    role = "proposer" if name.startswith("proposer") else "evaluator"
                    phases = ["baseline"] if name == "proposer-0" else ["baseline", "candidate-0"] if name == "evaluator-0" else ["challenge-1"]
                    a = self.research(name, role, memory if role == "proposer" else scenario, phases)
                    return self._pending(a)
                if not self.ledger.receipt("done-" + name + "-extract"):
                    return self._pending(self.research_extract(name))
                if name.startswith("proposer-"): memory = self._selected_candidate(name, memory)
                else: scenario = self._selected_candidate(name, scenario)
            else:
                p = self.phase(name, memory, scenario, self.config["seed"])
                for index, ep in enumerate(p["chain"]["episodes"]):
                    slot = name + "-task-" + str(index)
                    if self._unit_failure(slot):
                        if ep["role"] in ("learning", "interference"):
                            self._commit("skip-extract-" + slot, {"terminated_task": slot}, {"status": "skipped_failed_dependency_no_model_or_card"})
                        continue
                    if not self.ledger.receipt("done-" + slot):
                        label, cutoff = self._cutoff(name, index)
                        for role in ("consolidation", "reflector"):
                            prepared = DerivedStage(self.cards, self.registry).prepare(role, p["namespace"], cutoff, p["memory"])
                            if prepared["payload"]["scheduled"] and not self.ledger.receipt("done-" + name + "-" + role + "-" + label):
                                return self._pending(self.derived(name, role, cutoff, label))
                    if not self.ledger.receipt("done-" + slot): return self._pending(self.task(name, index))
                    if ep["role"] in ("learning", "interference"):
                        slot = name + "-extract-" + str(index)
                        if not self.ledger.receipt("done-" + slot): return self._pending(self.extractor(name, index))
        return {"status": "observed_schedule_complete_not_qualified", "report": self.report(),
                "delivery_ready": False, "reason": "qualification_independent_repeats_regression_pressure_history_review_required"}

    def _pending(self, a):
        slot = a["id"]; binding = self.ledger.receipt("bind-" + slot)
        intent = self.ledger.receipt("dispatch-" + slot)
        messages = self._messages(slot)
        unsealed = self.ledger.receipt("intent-" + slot + "-" + str(len(messages) + 1))
        send = self.ledger.receipt("send-" + slot + "-" + str(len(messages))) if messages else None
        ack = self._delivery(slot, len(messages)) if messages else None
        expired = binding is not None and (timestamp(now()) - timestamp(self.ledger.get(binding["result"]["binding"])["bound_at"])).total_seconds() > self.config["actor_wall_seconds"]
        exhausted = len(messages) >= self.config["actor_turns_max"]
        interrupt = self.ledger.receipt("interrupt-" + slot)
        needs_start = binding is not None and self.config["transport"] == "controller_collaboration_tools_with_local_role_ipc" and not self.ledger.receipt("start-ack-" + slot)
        status = ("interrupt_unknown_do_not_repeat" if interrupt else "interrupt_required" if expired or exhausted else "needs_attention_unsealed_response" if unsealed else
                  "delivery_unknown_do_not_resend" if send and not ack else "ready_to_deliver_same_agent" if messages and not ack else
                  "start_unknown_do_not_resend" if needs_start and self.ledger.receipt("start-" + slot) else "start_required" if needs_start else
                  "started" if binding else "dispatch_unknown_do_not_respawn" if intent else "ready_to_dispatch")
        return {"slot": slot, "role": a["internal"]["role"], "packet_sha256": a["packet_sha256"],
                "prompt": self.prompt(slot),
                "status": status, "interrupt_reason": "wall_deadline" if expired else "turn_limit" if exhausted else None,
                "agent_id": self.ledger.get(binding["result"]["binding"])["agent_id"] if binding else None,
                "next_sequence": len(messages) + 1, "last_reply": messages[-1]["response"] if messages else None,
                "unsealed_response": unsealed["result"] if unsealed else None}

    def _runtime(self, a):
        i = a["internal"]
        task = a["packet"].get("files", {}).get("SPEC.md", "research " + i["role"])
        s = RuntimeSession(self.ledger, i["projection"], task)
        s.briefs()
        for msg in self._messages(a["id"]):
            if msg["operation"] == "open_memory" and "wire" in msg["response"] and self._delivery(a["id"], msg["sequence"]):
                wire = s.open(msg["request"])
                require(wire.decode() == msg["response"]["wire"], "collaboration_memory_replay_drift")
        return s

    def _reader(self, a):
        i = a["internal"]
        r = EvidenceReader(self.cards, i["role"], i["namespace"], i["cutoff"], i["source_refs"])
        for msg in self._messages(a["id"]):
            if msg["operation"] == "read_evidence" and "wire" in msg["response"] and self._delivery(a["id"], msg["sequence"]):
                wire = r.read(msg["request"])
                require(wire.decode() == msg["response"]["wire"], "collaboration_source_replay_drift")
        return r

    def _record_memory_usage(self, a, runtime, claimed=()):
        slot = a["id"]; ns = a["internal"]["namespace"]; usage = UsageStore(self.cards); opened = set()
        for n, event in enumerate(runtime.events):
            for j, ref in enumerate(event["memory_refs"] if event["success"] else []):
                usage.record("usage-" + slot + "-" + str(n) + "-" + str(j), ns, slot,
                             "open" if event["operation"] == "open" else "auto_load", ref)
                if event["operation"] == "open": opened.add(ref["id"])
        declared = list(dict.fromkeys(x for x in claimed if type(x) is str)) if type(claimed) in (list, tuple) else []
        for n, id_ in enumerate(declared):
            if id_ in runtime.disclosed:
                usage.record("claim-" + slot + "-" + str(n), ns, slot, "claimed_use", runtime.items[id_]["ref"])
        return {"claimed_without_open": [x for x in declared if x not in opened],
                "undisclosed_memory_claims": [x for x in declared if x not in runtime.disclosed],
                "behavior_link_status": "unavailable_no_independent_association"}

    def receive(self, slot, sequence, agent_id, raw):
        self._require_execution()
        a = self._actor(slot); binding = self.ledger.receipt("bind-" + slot)
        require(binding is not None and self.ledger.get(binding["result"]["binding"])["agent_id"] == agent_id,
                "collaboration_sender_mismatch")
        require(self.config["transport"] != "controller_collaboration_tools_with_local_role_ipc" or
                self.ledger.receipt("start-" + slot) is not None, "collaboration_start_not_reserved")
        require(type(sequence) is int and 1 <= sequence <= self.config["actor_turns_max"], "collaboration_turn_limit")
        bounded_text(raw, self.config["response_bytes_max"], "collaboration_response_limit")
        key = "reply-" + slot + "-" + str(sequence)
        request = {"actor": a["ref"], "sequence": sequence, "agent_id": agent_id, "raw": raw}
        old = self.ledger.receipt(key, request)
        if old: return old["result"]
        require(self.ledger.receipt("terminate-" + slot) is None and not self._halted(),
                "collaboration_run_terminated")
        require(self.ledger.receipt("done-" + slot) is None, "actor_already_terminal")
        require(sequence == len(self._messages(slot)) + 1, "collaboration_sequence_gap")
        require(sequence == 1 or self._delivery(slot, sequence - 1) is not None, "collaboration_previous_reply_not_delivered")
        b = self.ledger.get(binding["result"]["binding"])
        # The absolute deadline starts at dispatch binding, not each followup.
        started = self.ledger.receipt("deadline-" + slot)
        if started is None:
            started = self._commit("deadline-" + slot, {"binding": binding["result"]["binding"]}, {"at": b["bound_at"]})
        require((timestamp(now()) - timestamp(started["result"]["at"])).total_seconds() <= self.config["actor_wall_seconds"],
                "collaboration_wall_deadline")
        # Intent is committed before a side effect; an unknown check/grade cannot
        # silently be re-executed after a controller crash.
        intent_key = "intent-" + slot + "-" + str(sequence)
        intent = self.ledger.receipt(intent_key, request)
        require(intent is None, "unsealed_collaboration_message_needs_attention")
        self._commit(intent_key, request, {"status": "received", "raw_sha256": sha256(raw),
                    "raw": raw, "agent_id": agent_id, "sequence": sequence, "actor": a["ref"]})
        with self.ledger.transaction():
            op = "invalid"; service_request = {"unparseable": True}
            try:
                message = parse_json(raw); require(type(message) is dict, "invalid_collaboration_message")
                op = message.get("op", "invalid"); service_request = message
                with self.ledger.savepoint():
                    response, service_request = self._execute_service(a, message, raw)
            except (ContractError, ValueError, UnicodeError) as exc:
                response = {"error": getattr(exc, "code", "invalid_collaboration_json"),
                            "status": "rejected_request_no_semantic_commit"}
            result = {"actor_slot": slot, "operation": op, "request": service_request, "response": response,
                      "raw": raw, "agent_id": agent_id, "sequence": sequence}
            self.ledger.commit_receipt(key, request, result, "control")
            return result

    def _execute_service(self, a, message, raw):
            slot = a["id"]; i = a["internal"]; op = message.get("op")
            ws = self.workspace(a) if i["role"] == "solver" else None
            if op == "packet":
                exact_object(message, {"op"})
                require(self.config["transport"] == "controller_collaboration_tools_with_local_role_ipc",
                        "packet_service_not_selected")
                response = {"wire": self.prompt(slot)}; service_request = message
            elif op == "scenario":
                exact_object(message, {"op", "request"})
                require(i["role"] == "solver", "scenario_role_denied")
                policy = ScenarioPolicy(ws, self.path, i["episode"])
                events = [m["response"]["event"] for m in self._messages(slot) if m["operation"] == "scenario" and "event" in m["response"]]
                policy.events = events; policy.feedback_count = sum(bool(e["response"].get("feedback")) for e in events)
                wire = policy.dispatch(message["request"])
                response = {"wire": wire.decode(), "event": policy.events[-1]}; service_request = message["request"]
            elif op == "write_engine":
                exact_object(message, {"op", "text"})
                require(i["role"] == "solver", "engine_write_role_denied")
                bounded_text(message["text"], self.config["engine_bytes_max"], "collaboration_engine_limit")
                hashes = tree_hash(ws)
                require(all(hashes.get(k) == v for k, v in i["protected"].items()), "protected_workspace_drift")
                before = read_workspace_text(ws, "engine.py")
                (ws / "engine.py").write_text(message["text"])
                response = {"written": "engine.py", "before_sha256": sha256(before), "after_sha256": sha256(message["text"])}
                service_request = message
            elif op == "open_memory":
                exact_object(message, {"op", "id"})
                require(i["role"] in ("solver", "evaluator", "proposer"), "memory_role_denied")
                response = {"wire": self._runtime(a).open(message).decode()}; service_request = message
            elif op == "read_evidence":
                require(i["role"] in ("extractor", "consolidation", "reflector"), "source_role_denied")
                response = {"wire": self._reader(a).read(message).decode()}; service_request = message
            elif op == "finish":
                exact_object(message, {"op", "result"})
                response = self._finish(a, message["result"], raw); service_request = message
            else:
                require(False, "unsupported_collaboration_operation")
            return response, service_request

    def _finish(self, a, result, raw):
        i = a["internal"]; slot = a["id"]; ns = i["namespace"]
        require(type(result) is dict, "invalid_collaboration_final")
        capture = self.ledger.put({"kind": "collaboration_capture", "id": slot, "version": 1,
                    "actor": a["ref"], "packet": a["packet"], "binding": self.ledger.receipt("bind-" + slot)["result"]["binding"],
                    "raw": raw, "final": result, "intent_id": "intent-" + slot + "-" + str(len(self._messages(slot)) + 1)}, "control")
        resolve_capture(self.ledger, capture)
        if i["role"] == "solver":
            ws = self.workspace(a)
            grade = verify(ws, self.path, i["episode"], i["protected"], result)
            sources = []
            def source(kind, value):
                sources.append(self.evidence.register("source-" + slot + "-" + str(len(sources)), ns, kind,
                               slot, now(), canonical(value).decode()))
            source("user_input", {"contract": a["packet"]["files"]["SPEC.md"],
                                   "sample": a["packet"]["files"]["sample_input.json"]})
            for msg in self._messages(slot):
                if self._delivery(slot, msg["sequence"]):
                    # Rejected envelopes may contain undeclared private fields.
                    # The actual raw stays in control; memory gets only the
                    # public rejection and a registered operation descriptor.
                    if "error" in msg["response"]:
                        op = msg["operation"]
                        public_call = {"op": op if type(op) is str and op in
                            ("scenario", "open_memory", "write_engine", "read_evidence") else "invalid",
                            "request_rejected": True, "error": msg["response"]["error"]}
                    else:
                        public_call = msg["request"]
                    source("tool_call", public_call)
                    source("tool_result", self._public_reply(msg["response"]))
            snapshot = workspace_snapshot(ws)
            if snapshot["boundary_error"] is None:
                source("file_diff", {"before": i["initial_engine"], "after": read_workspace_text(ws, "engine.py")})
            else:
                source("file_diff", {"before_sha256": sha256(i["initial_engine"]),
                       "collection_rejected": snapshot["boundary_error"], "after": None})
            source("agent_output", result)
            public = {"full_success": grade["full_success"], "behavior_success": grade["behavior_success"],
                      "false_completion": grade["false_completion"], "protected_violations": grade["protected_violations"]}
            source("visible_outcome", public)
            usage = UsageStore(self.cards); runtime = self._runtime(a)
            claims = self._record_memory_usage(a, runtime, result.get("memory_uses", []))
            usage.record("outcome-" + slot, ns, slot, "independent_outcome",
                         outcome="success" if grade["full_success"] else "failure", evidence_ref=sources[-1])
            completed = {"grade": grade, "sources": sources, "workspace_snapshot": snapshot,
                         "memory_events": runtime.events, "memory_claims": claims, "final": result}
        elif i["role"] == "extractor":
            reader = self._reader(a)
            # Every claimed citation must have actually been opened by this actor.
            read_refs = {canonical(e["response"]["evidence"]["ref"]) for e in reader.events
                         if e["response"] and "evidence" in e["response"] and e["response"]["end"] > e["response"]["start"]}
            exact_object(result, {"cards"})
            require(type(result["cards"]) is list, "invalid_extraction_count")
            for card in result["cards"]:
                require(type(card) is dict and all(canonical(r) in read_refs for r in card.get("evidence_refs", [])),
                        "collaboration_unread_extraction_source")
            receipt = self.cards.extract("extract-result-" + slot, result, ns, i["cutoff"], now())
            completed = {"materialization": receipt, "source_events": reader.events, "final": result}
        elif i["role"] in ("consolidation", "reflector"):
            receipt = DerivedStage(self.cards, self.registry).accept_collaboration("derived-result-" + slot,
                       i["derived_input"], capture)
            completed = {"materialization": receipt, "source_events": self._reader(a).events, "final": result}
        else:
            require(result.get("parent") == i["parent"], "collaboration_research_parent_mismatch")
            receipt = self.registry.propose("proposal-result-" + slot, result, i["role"], i["cutoff"], self.cards.proposal_evidence)
            sources = list(i["source_refs"])
            sources.append(self.evidence.register("research-output-" + slot, ns, "agent_output", slot, now(), canonical(result).decode()))
            sources.append(self.evidence.register("research-feedback-" + slot, ns, "visible_outcome", slot, now(),
                           canonical({"structure": receipt["result"], "quality": "unavailable_not_promoted"}).decode()))
            runtime = self._runtime(a)
            self._record_memory_usage(a, runtime)
            completed = {"materialization": receipt, "final": result, "sources": sources, "promotion_eligible": False,
                         "memory_events": runtime.events}
        completed["capture"] = capture
        completed.update(agent_slot=slot, category="collaboration_observed", provider_tokens=None,
                         provider_cost=None, formal_admission=False, gain_evidence=False)
        self.ledger.commit_receipt("done-" + slot, {"actor": a["ref"], "raw": raw}, completed, "control")
        return {"status": "completed", "public_result": completed.get("materialization", {}).get("result", public if i["role"] == "solver" else {}),
                "formal_admission": False}

    def report(self):
        self.ledger.audit()
        actors = []
        for ref, a in self.ledger.objects("collaboration_actor", "control"):
            slot = a["id"]; done = self.ledger.receipt("done-" + slot); binding = self.ledger.receipt("bind-" + slot)
            termination = self.ledger.receipt("terminate-" + slot)
            registered = self._actor(slot)
            require(registered == {**a, "ref": ref}, "collaboration_actor_receipt_mismatch")
            a = registered
            if "service_client" in a["internal"]:
                client = a["internal"]["service_client"]
                require(Path(client["directory"]) == self._client_root() / slot and
                        sha256(read_workspace_text(client["directory"], "client.py")) == client["client_sha256"],
                        "ipc_client_drift")
            for m in self._messages(slot):
                require(self.ledger.receipt("intent-" + slot + "-" + str(m["sequence"])) is not None,
                        "collaboration_message_intent_missing")
                self._delivery(slot, m["sequence"])
                require(m["sequence"] == 1 or self._delivery(slot, m["sequence"] - 1) is not None,
                        "collaboration_previous_reply_not_delivered")
                if "event" in m["response"]:
                    require(m["response"]["event"]["delivered"] is False, "collaboration_service_claimed_delivery")
            if termination:
                t = self.ledger.get(termination["result"]["termination"])
                require(done is None and t["actor"] == ref and binding is not None and
                        t["binding"] == binding["result"]["binding"] and t["final"] is None and
                        termination["request_sha256"] == sha256({k: t[k] for k in
                            ("actor", "binding", "agent_id", "reason", "actual_interrupt_tool_result")}),
                        "collaboration_termination_binding_mismatch")
                if a["internal"]["role"] == "solver":
                    require(workspace_snapshot(self.workspace(a)) == t["workspace_snapshot"],
                            "terminated_collaboration_workspace_drift")
            if done and a["internal"]["role"] == "solver":
                require(workspace_snapshot(self.workspace(a)) == done["result"]["workspace_snapshot"],
                        "completed_collaboration_workspace_drift")
            if done:
                if self.config["transport"] == "controller_collaboration_tools_with_local_role_ipc":
                    start = self.ledger.receipt("start-" + slot); ack = self.ledger.receipt("start-ack-" + slot)
                    require(start is not None and ack is not None and start["result"]["binding"] == binding["result"]["binding"] and
                            start["request_sha256"] == sha256({"actor": a["ref"], "binding": start["result"]["binding"], "native_ready": start["result"]["native_ready"]}) and
                            start["result"]["native_ready"]["agent_name"] == self.ledger.get(binding["result"]["binding"])["agent_id"] and
                            parse_json(start["result"]["native_ready"]["agent_status"]["completed"]) == {"status": "awaiting_controller_binding", "packet_sha256": a["packet_sha256"]} and
                            start["result"]["tool_request"] == {"target": self.ledger.get(binding["result"]["binding"])["agent_id"], "message": canonical({"status": "CONTROLLER_BOUND", "packet_sha256": a["packet_sha256"]}).decode()} and
                            ack["result"]["record"]["actor"] == a["ref"] and
                            ack["result"]["record"]["start_request_sha256"] == start["request_sha256"] and
                            ack["result"]["record"]["agent_id"] == self.ledger.get(binding["result"]["binding"])["agent_id"] and
                            ack["result"]["record"]["tool_result"] == "" and
                            ack["request_sha256"] == sha256(ack["result"]["record"]), "collaboration_start_ack_mismatch")
                capture = resolve_capture(self.ledger, done["result"]["capture"])
                require(done["request_sha256"] == sha256({"actor": a["ref"], "raw": capture["raw"]}) and
                        done["result"]["final"] == capture["final"], "collaboration_completion_binding_mismatch")
                if a["internal"]["role"] in ("extractor", "consolidation", "reflector"):
                    require(self._reader(a).events == done["result"]["source_events"], "collaboration_source_replay_drift")
                elif "projection" in a["internal"]:
                    require(self._runtime(a).events == done["result"]["memory_events"], "collaboration_memory_replay_drift")
            actors.append({"slot": slot, "role": a["internal"]["role"],
                           "status": "completed" if done else "terminated" if termination else "started" if binding else "dispatch_unknown" if self.ledger.receipt("dispatch-" + slot) else "pending",
                           "agent_id": self.ledger.get(binding["result"]["binding"])["agent_id"] if binding else None,
                           "turns": len(self._messages(slot)), "result": done["result"] if done else termination["result"] if termination else None})
        phases = [e["data"]["receipt"]["result"]["name"] for e in self.ledger.events("control")
                  if e["data"].get("type") == "decision" and e["data"]["receipt"]["request_id"].startswith("phase-")]
        return {"category": "collaboration_observed", "plan": self.plan["sha256"], "actors": actors,
                "planned_schedule": [{"phase": p, "materialized": p in phases or self.ledger.receipt("actor-" + p) is not None}
                                     for p in self.plan["schedule"]],
                "phases": [self.summary(p) for p in phases], "provider_tokens": None, "provider_cost": None,
                "formal_admission": False, "gain_evidence": False, "kernel_frozen": False,
                "qualification": "instruction_only_isolation_and_unknown_provider_usage"}


def main(argv=None):
    p = argparse.ArgumentParser(description="Actual collaboration controller bridge; no external API")
    p.add_argument("--run", required=True)
    sub = p.add_subparsers(dest="op", required=True)
    sub.add_parser("prepare")
    t = sub.add_parser("task"); t.add_argument("--phase", required=True); t.add_argument("--index", type=int, required=True)
    t = sub.add_parser("extractor"); t.add_argument("--phase", required=True); t.add_argument("--index", type=int, required=True)
    t = sub.add_parser("phase"); t.add_argument("--name", required=True); t.add_argument("--specs", required=True); t.add_argument("--seed", type=int, required=True)
    t = sub.add_parser("research"); t.add_argument("--slot", required=True); t.add_argument("--role", choices=["evaluator", "proposer"], required=True); t.add_argument("--request", required=True)
    for op in ("prompt", "dispatch", "bind", "start", "ack-start", "receive", "followup", "ack", "interrupt", "terminate"):
        t = sub.add_parser(op); t.add_argument("--slot", required=True)
        if op in ("bind", "start", "ack-start", "receive", "followup", "ack", "interrupt", "terminate"): t.add_argument("--request", required=True)
    sub.add_parser("report"); sub.add_parser("next")
    args = p.parse_args(argv)
    if args.op == "prepare": result = prepare(args.run)
    elif args.op == "report":
        with Loop(args.run, readonly=True) as loop: result = loop.report()
    else:
        with exclusive_run(args.run), Loop(args.run) as loop:
            if args.op == "next": result = loop.next()
            elif args.op == "phase":
                specs = loop.initial() if args.specs == "initial" else read_json(args.specs)
                result = loop.phase(args.name, specs["memory"], specs["scenario"], args.seed)
            elif args.op in ("task", "extractor"): result = getattr(loop, args.op)(args.phase, args.index)
            elif args.op == "research":
                r = read_json(args.request); result = loop.research(args.slot, args.role, r["parent"], r["phases"])
            elif args.op == "prompt": result = {"prompt": loop.prompt(args.slot)}
            elif args.op == "dispatch": result = loop.dispatch_intent(args.slot)
            elif args.op == "bind":
                r = read_json(args.request); result = loop.bind(args.slot, r["agent_id"], r["tool_result"])
            elif args.op == "start":
                r = read_json(args.request); result = loop.start_intent(args.slot, r["native_ready"])
            elif args.op == "ack-start":
                r = read_json(args.request); result = loop.acknowledge_start(args.slot, r["agent_id"], r["tool_result"])
            elif args.op == "followup":
                r = read_json(args.request); result = loop.followup_intent(args.slot, r["sequence"])
            elif args.op == "ack":
                r = read_json(args.request); result = loop.acknowledge_followup(args.slot, r["sequence"], r["agent_id"], r["tool_result"])
            elif args.op == "terminate":
                r = read_json(args.request); result = loop.terminate(args.slot, r["agent_id"], r["reason"], r["tool_result"])
            elif args.op == "interrupt":
                r = read_json(args.request); result = loop.interrupt_intent(args.slot, r["reason"])
            else:
                r = read_json(args.request); result = loop.receive(args.slot, r["sequence"], r["agent_id"], r["raw"])
    print(canonical(result).decode())


if __name__ == "__main__": main()

"""Registered causal-chain compiler and deterministic observable tool policy."""
import copy
import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path

from . import sandbox, service_tasks, tasks
from .common import save, tree_hash
from .workspace import read_workspace_text
from .contracts import (bounded_text, canonical, exact_object, identifier, positive_int,
                        require, sha256, validate_spec)
from .grade import equal_json
from .runtime import OfflineByteTokenizer

BACKENDS = {"feedback_retention": "repair", "workflow_reuse": "recovery", "environment_gotcha": "service",
            "stale_memory": "evidence", "abstraction_transfer": "repair", "context_interference": "evidence"}


def read_scenario(path=None):
    from .kernel_cli import read_json
    config = read_json(path or Path(__file__).resolve().parent.parent / "configs/scenario_v0.json")
    return validate_scenario(config)


def validate_scenario(config):
    exact_object(config, {"schema_version", "status", "hypothesis", "backends", "feedback_predicate", "feedback_max_per_episode", "max_tool_requests", "max_response_bytes",
        "production_seconds", "production_stdout_bytes", "production_stderr_bytes", "production_open_files",
        "pressure_clean_lines", "pressure_stressed_lines", "noise_generator", "interference_contracts", "counterexample_required", "live_execution_authorized", "control_corpus_source"}, "invalid_scenario_contract")
    require(config["schema_version"] == "scenario-contract-v0" and config["backends"] == BACKENDS, "unsupported_scenario_contract")
    require(config["feedback_predicate"] == "production_smoke_failed" and type(config["feedback_max_per_episode"]) is int and config["feedback_max_per_episode"] == 1, "unsupported_feedback_policy")
    require(config["noise_generator"] == "independent-operation-records-v0", "unsupported_noise_generator")
    exact_object(config["interference_contracts"],set(BACKENDS.values()),"invalid_interference_registry")
    for text in config["interference_contracts"].values():bounded_text(text,2048,"invalid_interference_contract")
    require(config["counterexample_required"] is True and config["live_execution_authorized"] is False, "invalid_scenario_execution_policy")
    for field in ("pressure_clean_lines", "pressure_stressed_lines", "max_tool_requests", "max_response_bytes",
                  "production_seconds", "production_stdout_bytes", "production_stderr_bytes", "production_open_files"):
        positive_int(config[field], "invalid_pressure_lines")
    require(config["pressure_stressed_lines"] >= 3*config["pressure_clean_lines"], "insufficient_planned_pressure")
    require(config["pressure_stressed_lines"] <= 10000, "pressure_generation_limit")
    return copy.deepcopy(config)


def compile_chain(registry, scenario_ref, seed, contract=None):
    require(type(seed) is int and seed >= 0, "invalid_scenario_seed")
    spec = registry.resolve(scenario_ref)
    require(spec["kind"] == "scenario", "expected_scenario_spec")
    contract = validate_scenario(contract if contract is not None else read_scenario())
    config = spec["config"]; family = config["family"]; backend = contract["backends"][family]
    distance = 2 if config["transfer_distance"] == "near" else 3
    target_stage = 4 if config["rule_change"] == "registered_revision" else distance
    mode = "observed" if family == "stale_memory" else "published"
    if config["rule_change"] == "registered_revision":
        mode = "published" if mode == "observed" else "observed"
    plan = []
    def add(role, domain, stage, mode_=None, profile="clean", interference="none", transfer="near"):
        index = len(plan)
        plan.append({"id": "episode-"+str(index), "role": role, "backend": domain, "stage": stage,
                     "seed": seed*1009+index*37, "rule_mode": mode_, "context_profile": profile,
                     "semantic_interference": interference, "transfer_distance": transfer})
    add("learning", backend, 0, "observed" if family == "stale_memory" else "published" if backend == "evidence" else "both" if backend == "repair" else None)
    if family == "abstraction_transfer":
        add("learning", "recovery", 0)
    for index in range(config["distractor_count"]):
        domain = ["repair", "recovery", "evidence", "service"][index % 4]
        if domain == backend:
            domain = "service" if backend != "service" else "repair"
        add("interference", domain, index % 2, "published" if domain == "evidence" else "both" if domain == "repair" else None,
            config["context_profile"], config["semantic_interference"])
    add("target", backend, target_stage, mode if backend == "evidence" else
        ("add_only" if config["rule_change"] == "registered_revision" else "both") if backend == "repair" else None,
        config["context_profile"], config["semantic_interference"], config["transfer_distance"])
    add("counterexample", backend, 4 if target_stage != 4 else 2,
        ("published" if mode == "observed" else "observed") if backend == "evidence" else
        ("both" if config["rule_change"] == "registered_revision" else "add_only") if backend == "repair" else None,
        config["context_profile"], config["semantic_interference"], "far" if config["transfer_distance"] == "near" else "near")
    value = {"schema_version": 1, "scenario": scenario_ref, "seed": seed, "contract_sha256": sha256(contract),
             "episodes": plan, "category": "historical_failure_derived_synthetic"}
    # Seed-independent signature detects duplicate challenges instead of counting
    # random value/title changes as evaluator novelty.
    value["semantic_signature"] = sha256({"backend": backend, "family": family, "config": config,
        "episodes": [{k: v for k,v in p.items() if k not in ("id", "seed")} for p in plan]})
    return {**value, "sha256": sha256(value)}


def probes(episode, seed=None):
    seed = episode["seed"] if seed is None else seed
    backend, stage = episode["backend"], episode["stage"]
    if backend == "service":
        rows = service_tasks.cases(seed, stage == 4)
        if episode["transfer_distance"] == "far":
            name = "scheduler_"+str(seed % 10007)
            for row in rows:
                case = row["input"]
                if "queue" in case["services"]:
                    case["services"][name] = case["services"].pop("queue")
                for job in case["jobs"]:
                    if job["dependency"] == "queue":
                        job["dependency"] = name
        return rows
    rows = tasks.cases(backend, seed, stage)
    if backend == "evidence":
        for row in rows:
            case = row["input"]
            old_metric, old_period = case["metric"], case["period"]
            case["metric"] = "revenue" if episode["transfer_distance"] == "near" else "operating_flow_"+str(seed % 10007)
            case["period"] = "2025FY" if episode["transfer_distance"] == "near" else "2026Q2"
            for fact in case["facts"]:
                if fact["metric"] == old_metric:
                    fact["metric"] = case["metric"]
                if fact["period"] == old_period:
                    fact["period"] = case["period"]
            case["contract"] = episode["rule_mode"]
            row["expected"] = tasks.oracle(backend, row["input"])
    elif backend == "repair":
        for row in rows:
            case = row["input"]
            case["capability_mode"] = episode["rule_mode"]
            if episode["transfer_distance"] == "far":
                case["state"] = {"package": {"records": case["state"]}, "outer_preserved": seed}
                from .common import digest
                case["snapshot"] = digest(case["state"])
                for edit in case["edits"]+case["grants"]:
                    edit["path"] = "/package/records" + edit["path"]
            row["expected"] = tasks.oracle(backend, case)
            if episode["rule_mode"] == "add_only" and any(e["op"] != "add" for e in case["edits"]):
                row["expected"] = {"accepted": False, "state": copy.deepcopy(case["state"])}
    elif backend == "recovery" and episode["transfer_distance"] == "near":
        for row in rows:
            case = row["input"]
            value = case["state"].pop(case["marker_key"])
            case["marker_key"] = "last_completed_cycle"
            case["state"][case["marker_key"]] = value
    return rows


def materialize(workspace, episode):
    workspace = Path(workspace)
    require(not workspace.exists() or not any(workspace.iterdir()), "scenario_workspace_not_empty")
    workspace.mkdir(parents=True, exist_ok=True)
    if episode["backend"] == "service":
        (workspace/"engine.py").write_text(service_tasks.BROKEN)
        (workspace/"run.py").write_text(tasks.ENTRY)
        spec = service_tasks.CONTRACT
    else:
        tasks.materialize(workspace, episode["backend"], episode["seed"], episode["stage"])
        spec = tasks.CONTRACTS[episode["backend"]]
    if episode["backend"] == "repair":
        spec += "\nCurrent case.capability_mode='add_only' additionally rejects any non-add edit; 'both' permits the op+path grants above. An empty edit list remains valid. Preserve the whole state on rejection.\n"
    (workspace/"SPEC.md").write_text(spec + "\nUse the scenario tool to inspect the current request environment and verify the production entry.\n")
    sample = probes(episode, episode["seed"]+7001)[0]["input"]
    save(workspace/"sample_input.json", [sample])
    (workspace/"AGENTS.md").write_text("Read SPEC.md. Modify engine.py only. run.py is the production entry. "
        "Use scenario.py inspect/check for current observable environment and verification. Do not access outside this workspace or edit control files.\n")
    return {k: v for k,v in tree_hash(workspace).items() if k != "engine.py"}


def production(workspace, control_root, inputs, contract=None, denied_roots=()):
    import shutil
    from .process import bounded_run
    config=validate_scenario(contract if contract is not None else read_scenario())
    try:
        tree_hash(workspace)
        for cache in Path(workspace).rglob("__pycache__"):
            if cache.is_dir() and not cache.is_symlink():
                shutil.rmtree(cache)
        # Set hard limits inside a fresh interpreter, avoiding preexec_fn in the
        # threaded socket controller. They apply to grader code and its children.
        script="import os,sys,runpy,resource; resource.setrlimit(resource.RLIMIT_CPU,(%d,%d)); resource.setrlimit(resource.RLIMIT_NOFILE,(%d,%d)); sys.path.insert(0,os.getcwd()); runpy.run_path('run.py',run_name='__main__')" % (
            config["production_seconds"],config["production_seconds"],config["production_open_files"],config["production_open_files"])
        process = bounded_run(sandbox.command(Path(workspace), Path(control_root),
            [sys.executable, "-I", "-B", "-c", script],write_isolation=True, network=False,denied_roots=denied_roots),
            workspace,canonical(inputs),config["production_seconds"],config["production_stdout_bytes"],config["production_stderr_bytes"],env=sandbox.worker_environment())
        output = None
        if process["exit_code"] == 0 and process["error"] is None:
            from .kernel_cli import parse_json
            try:output = parse_json(process["stdout"])
            except ValueError:process["error"]="invalid_production_json"
        return {**process,"output":output}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"exit_code": None, "stdout": "", "stderr": "", "output": None, "error": getattr(exc,"code",type(exc).__name__)}


def verify(workspace, control_root, episode, protected, final=None, contract=None, denied_roots=()):
    rows = probes(episode)
    execution = production(workspace, control_root, [r["input"] for r in rows], contract,denied_roots)
    outputs = execution["output"]
    shape = type(outputs) is list and len(outputs) == len(rows)
    groups = {}
    for i, row in enumerate(rows):
        group = groups.setdefault(row["group"], {"passed": 0, "total": 0})
        group["total"] += 1
        group["passed"] += int(shape and equal_json(outputs[i], row["expected"]))
    try:
        changed = tree_hash(Path(workspace))
        violations = sorted(k for k,v in protected.items() if changed.get(k) != v)
    except ValueError as exc:
        violations = ["workspace_boundary:"+getattr(exc,"code",type(exc).__name__)]
    success = shape and not execution["error"] and not violations and all(g["passed"] == g["total"] for g in groups.values())
    # The full shape is checked below without coercion; reported success is never
    # evidence of production execution.
    return _finish_grade(success, final, groups, violations, execution, rows)


def _finish_grade(success, final, groups, violations, execution, rows):
    valid_final = (type(final) is dict and set(final) == {"summary", "completion_claim", "memory_uses"} and
        type(final["summary"]) is str and bool(final["summary"].strip()) and
        final["completion_claim"] in ("complete", "partial") and type(final["memory_uses"]) is list and
        all(type(id_) is str for id_ in final["memory_uses"]))
    return {"behavior_success": bool(success), "full_success": bool(success and valid_final and final["completion_claim"] == "complete"),
        "valid_final": bool(valid_final), "false_completion": bool(valid_final and final["completion_claim"] == "complete" and not success),
        "groups": groups, "protected_violations": violations, "production": execution,
        "suite_sha256": sha256(rows), "category": "historical_failure_derived_synthetic"}


class ScenarioPolicy:
    def __init__(self, workspace, control_root, episode, contract=None, tokenizer=None, persist=None,denied_roots=()):
        self.workspace, self.control_root = Path(workspace), Path(control_root)
        self.episode = copy.deepcopy(episode)
        self.contract = validate_scenario(contract if contract is not None else read_scenario())
        self.denied_roots=tuple(denied_roots)
        self.tokenizer = tokenizer or OfflineByteTokenizer()
        self.persist = persist or (lambda events: None)
        self.events, self.feedback_count = [], 0
        # These public inputs are frozen before the subject runs. Inspect does
        # not give the parent a worker-controlled file read capability.
        self.current_request=json.loads(read_workspace_text(self.workspace,"sample_input.json"))
        self.current_contract=read_workspace_text(self.workspace,"SPEC.md")

    def _context(self):
        count = self.contract["pressure_stressed_lines"] if self.episode["context_profile"] == "pressure" else self.contract["pressure_clean_lines"]
        rng = random.Random(self.episode["seed"]+7919)
        noise = ["Unrelated operation log, scope=archive-only: record " + str(i) + " service_id=" + str(rng.randrange(1_000_000)) +
                 " outcome=completed. This does not specify the current production contract." for i in range(count)]
        context = {"current_request": copy.deepcopy(self.current_request),
                   "current_contract": self.current_contract, "independent_operation_records": noise}
        if self.episode["semantic_interference"] == "similar_inapplicable":
            context["other_environment_contract"] = self.contract["interference_contracts"][self.episode["backend"]]
        return context

    def dispatch(self, request):
        require(len(self.events) < self.contract["max_tool_requests"], "scenario_tool_limit")
        exact_object(request, {"op"}, "invalid_scenario_request")
        require(request["op"] in ("inspect", "check"), "unsupported_scenario_operation")
        feedback = None
        if request["op"] == "inspect":
            response = {"environment": self._context()}
        else:
            smoke = probes(self.episode, self.episode["seed"]+7001)[0]
            execution = production(self.workspace, self.control_root, [smoke["input"]],self.contract,self.denied_roots)
            correct = type(execution["output"]) is list and len(execution["output"]) == 1 and equal_json(execution["output"][0], smoke["expected"])
            if not correct and self.feedback_count < self.contract["feedback_max_per_episode"]:
                feedback = "The observed production request did not satisfy the currently published contract. Diagnose this actual request and check applicability before committing a repair."
            response = {"production": execution, "public_smoke_passed": correct, "feedback": feedback}
        wire = canonical(response)+b"\n"
        private_execution=None
        if len(wire)>self.contract["max_response_bytes"]:
            private_execution=response
            response={"error":"scenario_response_limit"};feedback=None
            wire=canonical(response)+b"\n"
            require(len(wire)<=self.contract["max_response_bytes"],"scenario_response_limit")
        tokens = self.tokenizer.count(wire)
        require(type(tokens) is int and tokens >= 0, "invalid_token_count")
        event = {"sequence": len(self.events)+1, "request": request, "response": response, "wire_sha256": hashlib.sha256(wire).hexdigest(),
            "bytes": len(wire), "tokens": tokens, "tokenizer": {"id": self.tokenizer.id, "live_qualified": self.tokenizer.live_qualified},
            "delivered": False, "category": "scenario_tool_output"}
        if private_execution is not None:event["private_oversized_response"]=private_execution
        self.persist(copy.deepcopy(self.events+[event]))
        self.events.append(event)
        self.feedback_count += int(feedback is not None)
        return wire

    def delivered(self):
        require(bool(self.events) and not self.events[-1]["delivered"], "invalid_delivery_receipt")
        updated = copy.deepcopy(self.events); updated[-1]["delivered"] = True
        self.persist(updated)
        self.events = updated


class ScenarioSocket:
    def __init__(self, workspace, policy):
        import threading
        self.path = Path(workspace)/".scenario.sock"
        self.policy = policy
        self.stop = threading.Event()
        self.error = None

    def __enter__(self):
        import socket
        import threading
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.bind(str(self.path)); self.sock.listen(4); self.sock.settimeout(0.1)
        self.thread = threading.Thread(target=self._serve, daemon=True); self.thread.start()
        return self

    def _serve(self):
        import socket
        from .kernel_cli import parse_json
        while not self.stop.is_set() and len(self.policy.events) < self.policy.contract["max_tool_requests"]:
            try:
                connection, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(2)
                try:
                    raw = b""
                    while b"\n" not in raw and len(raw) < 8192:
                        chunk = connection.recv(8192-len(raw))
                        if not chunk:
                            break
                        raw += chunk
                    require(len(raw) < 8192 and b"\n" in raw, "invalid_scenario_wire")
                    response = self.policy.dispatch(parse_json(raw.split(b"\n",1)[0].decode()))
                    connection.sendall(response)
                    self.policy.delivered()
                except BaseException as exc:
                    # No successful delivery is invented. The containing trial
                    # must retain this technical failure rather than score success.
                    self.error = exc
                    self.stop.set()
                    try: connection.sendall(canonical({"error": getattr(exc,"code",type(exc).__name__)})+b"\n")
                    except OSError: pass

    def __exit__(self, *_):
        self.stop.set(); self.sock.close(); self.thread.join(timeout=3)
        self.path.unlink(missing_ok=True)
        require(not self.thread.is_alive(), "scenario_socket_shutdown_failure")
        if self.error is not None:
            raise self.error


CLIENT = '''import argparse,json,socket
from pathlib import Path
p=argparse.ArgumentParser(description="Inspect the current environment or verify production")
p.add_argument("op",choices=["inspect","check"])
a=p.parse_args()
with socket.socket(socket.AF_UNIX) as s:
    s.connect(str(Path(__file__).with_name(".scenario.sock")))
    s.sendall((json.dumps({"op":a.op})+"\\n").encode())
    response=b""
    while True:
        chunk=s.recv(65536)
        if not chunk:break
        response+=chunk
    print(response.decode(),end="")
'''

import copy
import json
import os
import random
import selectors
import shutil
import signal
import sys
import subprocess
import tempfile
import time
from pathlib import Path

from .common import digest, encode, load, save, tree_hash, exclusive_run
from .workspace import copy_workspace,read_workspace_text
from .grade import feedback, grade
from .memory import CLIENT, DisclosureServer, ReferenceMemory
from .sandbox import command, probe,worker_environment
from .tasks import FAMILIES, STAGES, materialize, cases

ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ("AGENTS.md", "SPEC.md", "run.py", "sample_input.json", "memory_tool.py", "final.schema.json", "reflection.schema.json")

FINAL_SCHEMA = {"type": "object", "properties": {
    "summary": {"type": "string"}, "completion_claim": {"type": "string", "enum": ["complete", "partial"]},
    "memory_uses": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "action": {"type": "string"}}, "required": ["id", "action"], "additionalProperties": False}}},
    "required": ["summary", "completion_claim", "memory_uses"], "additionalProperties": False}
NOTE_FIELDS = {"id": {"type": "string"}, "expected_version": {"type": "integer"},
    "category": {"type": "string"}, "level": {"type": "integer"},
    **{k: {"type": "string"} for k in ("brief", "content", "conditions", "limits")},
    "source_refs": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "sha256": {"type": "string"}}, "required": ["id", "sha256"], "additionalProperties": False}},
    **{k: {"type": "array", "items": {"type": "string"}} for k in ("support_ids", "links", "supersedes")}}
REFLECTION_SCHEMA = {"type": "object", "properties": {"notes": {"type": "array", "items": {
    "type": "object", "properties": NOTE_FIELDS, "required": list(NOTE_FIELDS), "additionalProperties": False}}},
    "required": ["notes"], "additionalProperties": False}


def source_hashes():
    files = list((ROOT / "memorylab").glob("*.py")) + list((ROOT / "docs").glob("*.md"))
    files += [ROOT / "AGENTS.md"]
    return {str(p.relative_to(ROOT)): digest(p.read_text()) for p in files}


def validate_config(config):
    if config.get("schema_version") != 1:
        raise ValueError("unsupported config schema")
    arms = config.get("arms")
    if not isinstance(arms, list) or not arms:
        raise ValueError("at least one execution arm required")
    ids = set()
    import re
    for arm in arms:
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", str(arm.get("id", ""))) or arm["id"] in ids:
            raise ValueError("invalid/duplicate arm id")
        ids.add(arm["id"])
        if arm.get("adapter") not in ("off", "reference"):
            raise ValueError("no mechanism variants implemented; unknown adapter")
        if type(arm.get("reflect")) is not bool or (arm["adapter"] == "off" and arm["reflect"]):
            raise ValueError("invalid reflection setting")
    if not config.get("families") or any(x not in FAMILIES for x in config["families"]) or len(set(config["families"])) != len(config["families"]):
        raise ValueError("invalid/duplicate task families")
    for key, maximum in (("repeats", 10), ("episodes", 5), ("timeout_seconds", 3600),
                         ("command_limit", 200), ("brief_budget_bytes", 100000), ("disclosure_budget_bytes", 200000)):
        if type(config.get(key)) is not int or not 1 <= config[key] <= maximum:
            raise ValueError("invalid " + key)
    if type(config.get("seed")) is not int or config["seed"] < 0:
        raise ValueError("invalid seed")
    if config.get("reasoning_effort") not in ("low", "medium", "high", "xhigh", "max", "ultra"):
        raise ValueError("explicit reasoning_effort required")
    if config.get("model") is not None and (not isinstance(config["model"], str) or not config["model"].strip()):
        raise ValueError("invalid model")


def prepare(config_path, output):
    config = load(config_path)
    validate_config(config)
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("refusing to overwrite a run directory")
    output.mkdir(parents=True)
    rng = random.Random(config["seed"])
    trials = []
    for repeat in range(config["repeats"]):
        order = list(config["arms"])
        rng.shuffle(order)
        for family in config["families"]:
            for arm in order:
                for episode in range(config["episodes"]):
                    trials.append({"id": f"r{repeat}-{family}-{arm['id']}-e{episode+1}",
                                   "repeat": repeat, "family": family, "arm": arm["id"],
                                   "episode": episode, "seed": config["seed"]+repeat*1009,
                                   "suite_sha256": digest(cases(family, config["seed"]+repeat*1009, episode))})
    manifest = {"schema_version": 1, "kind": "planned_live_codex", "config": config,
                "source_hashes": source_hashes(), "trials": trials,
                "solve_calls": len(trials), "reflection_calls_max": sum(
                    1 for t in trials if next(a for a in config["arms"] if a["id"] == t["arm"])["reflect"])}
    save(output / "manifest.json", manifest)
    save(output / "lock.json", {"manifest_sha256": digest(manifest)})
    save(output / "state.json", {"status": "prepared", "trials": {t["id"]: "pending" for t in trials}})
    return {"run_dir": str(output), "solve_calls": manifest["solve_calls"],
            "reflection_calls_max": manifest["reflection_calls_max"], "model_configured": bool(config["model"]),
            "status": "prepared_no_model_calls"}


def verify_manifest(run):
    manifest = load(run / "manifest.json")
    if digest(manifest) != load(run / "lock.json")["manifest_sha256"]:
        raise ValueError("manifest changed after freeze")
    if manifest["source_hashes"] != source_hashes():
        raise ValueError("protocol/implementation changed after freeze; prepare a new run")
    validate_config(manifest["config"])
    return manifest


def codex_command(workspace, control, config, phase):
    if not config.get("model"):
        raise ValueError("set explicit model before live execution; never guess an alias")
    binary = shutil.which("codex")
    if not binary:
        raise RuntimeError("Codex CLI not found")
    args = [binary, "--no-daemon", "-a", "never", "exec", "--json", "--ephemeral",
            "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
            "--sandbox", "workspace-write", "--cd", str(workspace), "--model", config["model"],
            "-c", 'model_reasoning_effort="'+config["reasoning_effort"]+'"',
            "-c", 'web_search="disabled"', "-c", "sandbox_workspace_write.network_access=false",
            "--enable", "skip_host_skill_discovery"]
    for feature in ("memories", "apps", "plugins", "browser_use", "computer_use", "multi_agent", "shell_snapshot", "skill_search"):
        args += ["--disable", feature]
    args += ["--output-schema", str(workspace / ("final.schema.json" if phase == "solve" else "reflection.schema.json")),
             "--output-last-message", str(workspace / (phase+".final.json")), "-"]
    return command(workspace, control, args, network=True)


def parse_events(path):
    events, malformed = [], 0
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("event must be an object")
            events.append(value)
        except (json.JSONDecodeError, ValueError):
            malformed += 1
    usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    available = False
    commands, all_items = [], set()
    completed, failed = False, False
    for event in events:
        if event.get("type") == "turn.completed":
            completed = True
            if event.get("usage"):
                available = True
                for k in usage:
                    usage[k] += event["usage"].get(k, 0)
        if event.get("type") in ("turn.failed", "error"):
            failed = True
        item = event.get("item", {})
        if item.get("id"):
            all_items.add(item["id"])
        if event.get("type") == "item.completed" and item.get("type") == "command_execution":
            commands.append(item.get("command", ""))
    return {"events": len(events), "malformed_events": malformed, "turn_completed": completed,
            "turn_failed": failed, "usage": usage if available else None,
            "total_tokens": usage["input_tokens"]+usage["output_tokens"] if available else None,
            "command_count": len(commands), "repeated_commands": len(commands)-len(set(commands)),
            "item_count": len(all_items)}


def invoke(workspace, control, config, prompt, directory, phase):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "prompt.txt").write_text(prompt)
    env = worker_environment()
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    started, reason, command_ids = time.monotonic(), None, set()
    # No shell interpolation, no auth/config files read or copied by the controller.
    with (directory / "events.jsonl").open("w") as out, (directory / "stderr.txt").open("w") as err:
        proc = subprocess.Popen(codex_command(workspace, control, config, phase), cwd=workspace,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, env=env, start_new_session=True)
        try:
            proc.stdin.write(prompt.encode()); proc.stdin.close()
            selector = selectors.DefaultSelector()
            selector.register(proc.stdout, selectors.EVENT_READ)
            buffered = b""
            while selector.get_map():
                if time.monotonic()-started > config["timeout_seconds"]:
                    reason = "timeout"; break
                for key, _ in selector.select(timeout=0.25):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj); continue
                    buffered += chunk
                    while b"\n" in buffered:
                        line, buffered = buffered.split(b"\n", 1)
                        out.write(line.decode(errors="replace")+"\n"); out.flush()
                        try:
                            event = json.loads(line)
                            item = event.get("item", {})
                            if item.get("type") == "command_execution" and item.get("id"):
                                command_ids.add(item["id"])
                        except (json.JSONDecodeError, AttributeError):
                            continue
                        if len(command_ids) > config["command_limit"]:
                            reason = "command_limit"; break
                if reason:
                    break
            if buffered:
                out.write(buffered.decode(errors="replace"))
            if reason and proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                exit_code = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL); exit_code = proc.wait(timeout=10)
        except BaseException:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL); proc.wait(timeout=5)
            raise
        finally:
            if 'selector' in locals():
                selector.close()
            proc.stdout.close()
            if not proc.stdin.closed:
                proc.stdin.close()
    metrics = parse_events(directory / "events.jsonl")
    metrics.update(wall_seconds=time.monotonic()-started, exit_code=exit_code,
                   stop_reason=reason, status="ok" if exit_code == 0 and metrics["turn_completed"] and not reason and not metrics["turn_failed"] and not metrics["malformed_events"] else "failed")
    final = None
    try:
        final = load(workspace / (phase+".final.json"))
    except (OSError, json.JSONDecodeError):
        metrics.update(status="failed", final_error="missing_or_invalid_json")
    save(directory / "metrics.json", metrics)
    save(directory / "final.json", final)
    return metrics, final


def execute(run_path, limit=None):
    with exclusive_run(run_path):
        return _execute(run_path, limit)


def _execute(run_path, limit=None):
    run = Path(run_path).resolve()
    manifest = verify_manifest(run)
    config = manifest["config"]
    if not config["model"]:
        raise ValueError("model is not configured; prepare a new plan with an explicit model")
    state = load(run / "state.json")
    if any(status == "running" for status in state["trials"].values()):
        raise ValueError("interrupted trial present; audit evidence and close it as interrupted before resume")
    version = subprocess.run([shutil.which("codex") or "codex", "--version"], capture_output=True, text=True, check=True).stdout.strip()
    environment_file = run / "environment.json"
    if environment_file.exists():
        prior = load(environment_file)
        if prior["codex_version"] != version or prior["python_version"] != sys.version:
            raise ValueError("CLI/Python version changed after execution started")
    save(environment_file, {"codex_version": version, "python_version": sys.version, "model_requested": config["model"],
                           "reasoning_effort": config["reasoning_effort"], "actual_resolved_model": None,
                           "note": "requested model recorded; CLI JSONL does not always expose resolved backend identity"})
    performed = 0
    for trial in manifest["trials"]:
        if state["trials"][trial["id"]] != "pending":
            continue
        if limit is not None and performed >= limit:
            break
        arm = next(a for a in config["arms"] if a["id"] == trial["arm"])
        memory_file = run / "memory" / f"r{trial['repeat']}-{trial['family']}-{trial['arm']}.json"
        memory = ReferenceMemory(load(memory_file) if memory_file.exists() else None)
        directory = run / "trials" / trial["id"]
        directory.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix="memorylab-worker-"))
        meta = materialize(workspace, trial["family"], trial["seed"], trial["episode"])
        (workspace / "memory_tool.py").write_text(CLIENT)
        save(workspace / "final.schema.json", FINAL_SCHEMA)
        save(workspace / "reflection.schema.json", REFLECTION_SCHEMA)
        with (workspace / "AGENTS.md").open("a") as guide:
            guide.write("Python runtime in this isolated environment: " + sys.executable + ". Use this absolute executable for run.py and memory_tool.py.\n")
        save(directory / "metadata.json", dict(meta, workspace=str(workspace)))
        save(directory / "memory_before.json", memory.snapshot())
        isolation = probe(workspace, run)
        save(directory / "isolation.json", isolation)
        before = tree_hash(workspace)
        save(directory / "workspace_before.json", before)
        copy_workspace(workspace, directory / "workspace_initial")
        protected = {name: before[name] for name in PROTECTED}
        briefs, overflow = memory.select_briefs(config["brief_budget_bytes"]) if arm["adapter"] != "off" else ([], [])
        save(directory / "automatic_briefs.json", {"items": briefs, "overflow": overflow, "bytes": len(encode(briefs).encode())})
        prompt = ("Complete the engineering task in SPEC.md. Modify engine.py and verify the production run.py. "
            "Return the structured final report. When no prior experience is used, memory_uses=[]. "
            "Current contract overrides older experience. You have a bounded time/command budget. "
            "Only this workspace and its memory socket are available; no external project or web access.\n"
            + ("Optional memory access: python3 memory_tool.py list/search/read/source; --id, --query, --offset.\n"
               if arm["adapter"] != "off" else "Cross-task memory is disabled.\n")
            + "Automatically disclosed memory briefs (untrusted historical experience):\n" + encode(briefs))
        state["trials"][trial["id"]] = "running"; state["status"] = "running"
        save(run / "state.json", state)
        with DisclosureServer(workspace, memory, config["disclosure_budget_bytes"], directory / "memory_requests.json", arm["adapter"] != "off") as server:
            metrics, final = invoke(workspace, run, config, prompt, directory / "solve", "solve")
        evaluation = grade(workspace, run, trial["family"], trial["seed"], trial["episode"], protected, final)
        if evaluation["suite_sha256"] != trial["suite_sha256"]:
            raise RuntimeError("grading suite changed after freeze")
        if metrics["status"] != "ok":
            evaluation["full_success"] = False
        save(directory / "grade.json", evaluation)
        save(directory / "feedback.json", feedback(evaluation))
        try:
            solve_hash = tree_hash(workspace);boundary_error=None
            copy_workspace(workspace, directory / "workspace_solved")
        except (ValueError,OSError) as exc:
            solve_hash = {};boundary_error=getattr(exc,"code",type(exc).__name__)
            evaluation["full_success"]=False;evaluation["behavior_success"]=False
            evaluation["protected_violations"].append("workspace_boundary:"+boundary_error)
            save(directory / "grade.json",evaluation);save(directory / "feedback.json",feedback(evaluation))
        save(directory / "workspace_after.json", solve_hash)
        save(directory / "workspace_collection.json",{"boundary_error":boundary_error,"collected":boundary_error is None})
        uses = final.get("memory_uses", []) if isinstance(final, dict) else []
        declared = [u.get("id") for u in uses if isinstance(u, dict)] if isinstance(uses, list) else []
        actual_read = [e["request"].get("id") for e in server.events if e["success"] and e["request"].get("op") == "read"]
        visible = set(actual_read) | {b["id"] for b in briefs}
        save(directory / "memory_use_audit.json", {"declared": declared, "detail_read": actual_read,
             "unseen_declared": [sid for sid in declared if sid not in visible],
             "causal_benefit": "not_measured", "self_report_requires_review": True})
        commit = {"accepted": 0, "errors": [], "status": "not_requested"}
        if arm["reflect"] and boundary_error is None:
            handles = [memory.register_source(trial["id"]+":"+kind, text) for kind, text in (
                ("task", read_workspace_text(workspace,"SPEC.md")), ("solution", encode(final)),
                ("feedback", encode(feedback(evaluation))),
                ("trace", (directory / "solve" / "events.jsonl").read_text(errors="replace")),
                ("code", read_workspace_text(workspace,"engine.py") if (workspace / "engine.py").exists() else "[engine.py missing after solve]"))]
            reflection_prompt = ("Reflect on this completed task using actual sources. Propose zero to three "
                "reusable experiences with brief AND actual context, conditions and counterexamples. "
                "Do not invent causes, research gain or successful behavior. An empty notes list is valid. "
                "Use id='' and expected_version=0 for new notes; existing id/version only for revisions. "
                "Each source_refs entry must match a supplied source handle. Atomic level=0, support_ids=[]; "
                "higher levels need at least two earlier lower-level notes under same/descendant category. "
                "links and supersedes may reference existing active ids only. "
                "Use python3 memory_tool.py source --id HANDLE --offset N for full context, "
                "or list/read for earlier experiences. Do not modify code or infrastructure.\n"
                + "Category hint (not a memory strategy): " + meta["category"] + "\n"
                + "Feedback: " + encode(feedback(evaluation)) + "\nSources: " + encode(handles))
            with DisclosureServer(workspace, memory, config["disclosure_budget_bytes"], directory / "reflection_requests.json"):
                reflection_metrics, proposal = invoke(workspace, run, config, reflection_prompt, directory / "reflection", "reflection")
            try:current = tree_hash(workspace)
            except ValueError as exc:
                current={};reflection_metrics["status"]="workspace_boundary:"+getattr(exc,"code",type(exc).__name__)
            changes = [name for name, sha in solve_hash.items() if current.get(name) != sha]
            changes += [name for name in current if name not in solve_hash and name != "reflection.final.json"]
            if reflection_metrics["status"] == "ok" and not changes:
                commit = memory.commit(proposal, trial["id"])
            else:
                commit = {"accepted": 0, "errors": ["reflection failed or edited solve workspace"], "changes": changes}
        save(directory / "memory_commit.json", commit)
        save(directory / "memory_after.json", memory.snapshot())
        save(memory_file, memory.snapshot())
        # Commit marker follows all result files and memory persistence.
        state["trials"][trial["id"]] = "completed" if metrics["status"] == "ok" else "failed"
        save(run / "state.json", state)
        performed += 1
    state["status"] = "completed" if all(s in ("completed", "failed", "interrupted") for s in state["trials"].values()) else "partial"
    save(run / "state.json", state)
    return {"status": state["status"], "tasks_executed_this_call": performed}

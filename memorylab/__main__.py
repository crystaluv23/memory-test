import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .common import load, save, exclusive_run
from .runner import ROOT, execute, prepare, verify_manifest
from .sandbox import probe
from .tasks import materialize
from .report import summarize


def doctor():
    binary = shutil.which("codex")
    result = {"codex_available": bool(binary), "live_model_called": False}
    if binary:
        result["codex_version"] = subprocess.run([binary, "--version"], capture_output=True, text=True).stdout.strip()
        help_text = subprocess.run([binary, "exec", "--help"], capture_output=True, text=True).stdout
        result["required_flags"] = {f: f in help_text for f in ("--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--output-schema")}
    with tempfile.TemporaryDirectory(prefix="memorylab-doctor-control-") as control, tempfile.TemporaryDirectory(prefix="memorylab-doctor-worker-") as workspace:
        materialize(workspace, "recovery", 10, 0)
        result["isolation"] = probe(Path(workspace), Path(control))
    return result


def close_interrupted(run_path, trial_id, reason):
    with exclusive_run(run_path):
        return _close_interrupted(run_path, trial_id, reason)


def _close_interrupted(run_path, trial_id, reason):
    run = Path(run_path).resolve()
    manifest = verify_manifest(run)
    state = load(run / "state.json")
    if state["trials"].get(trial_id) != "running":
        raise ValueError("only running trials may be closed as interrupted")
    trial = next(t for t in manifest["trials"] if t["id"] == trial_id)
    directory = run / "trials" / trial_id
    before = directory / "memory_before.json"
    if before.exists():
        save(run / "memory" / f"r{trial['repeat']}-{trial['family']}-{trial['arm']}.json", load(before))
    save(directory / "interruption.json", {"reason": reason, "memory_restored_to_before_trial": before.exists(),
         "requires_process_exit_audit": True})
    # Preserve any diagnostic grader output separately; interrupted tasks are always failures.
    existing = directory / "grade.json"
    if existing.exists():
        save(directory / "grade.before_interruption.json", load(existing))
    save(existing, {"full_success": False, "behavior_success": False, "groups": {},
                    "entry_error": "interrupted", "false_completion": False})
    state["trials"][trial_id] = "interrupted"; state["status"] = "partial"
    save(run / "state.json", state)
    return {"status": "interrupted", "trial": trial_id, "record_preserved": True}


def main():
    import sys
    if len(sys.argv)>1 and sys.argv[1]=="graph-round":
        from .graph_run import main as graph_main
        return graph_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=="role-canary":
        from .role_canary import main as canary_main
        return canary_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=="history":
        from .history import main as history_main
        return history_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=="offline-round":
        from .offline import main as offline_main
        return offline_main(sys.argv[2:])
    # Keep historical v1 commands separate from the new offline controller API.
    # A planning or kernel config is never accepted as a live v1 execution plan.
    if len(sys.argv) > 1 and sys.argv[1] == "kernel":
        from .kernel_cli import main as kernel_main
        return kernel_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description="Codex memory experiment environment; no live calls unless execute is explicitly used")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="offline CLI and sandbox preflight, no model calls")
    p = sub.add_parser("prepare", help="freeze plan only, no model calls")
    p.add_argument("--config", required=True); p.add_argument("--output", required=True)
    p = sub.add_parser("execute", help="live Codex calls; consumes account quota")
    p.add_argument("--run", required=True); p.add_argument("--limit", type=int)
    p = sub.add_parser("report", help="summarize persisted state, no model calls")
    p.add_argument("--run", required=True)
    p = sub.add_parser("close-interrupted", help="after process-exit audit, preserve interrupted task as failure")
    p.add_argument("--run", required=True); p.add_argument("--trial", required=True); p.add_argument("--reason", required=True)
    args = parser.parse_args()
    if args.command == "doctor":
        result = doctor()
    elif args.command == "prepare":
        result = prepare(args.config, args.output)
    elif args.command == "execute":
        if args.limit is not None and args.limit < 1:
            parser.error("limit must be positive")
        result = execute(args.run, args.limit)
    elif args.command == "report":
        result = summarize(args.run)
        result = {"status": result["status"], "arms": result["arms"], "comparison_conclusion": result["comparison_conclusion"]}
    else:
        result = close_interrupted(args.run, args.trial, args.reason)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

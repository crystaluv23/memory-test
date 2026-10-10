import json
import subprocess
import sys
import shutil
from collections import defaultdict

from . import sandbox
from .common import encode, tree_hash, digest
from .tasks import cases


def grade(workspace, control_root, family, seed, episode, protected, final=None):
    # Execute the archived source version, never an earlier verification run's bytecode cache.
    for cache in workspace.rglob("__pycache__"):
        if cache.is_dir() and not cache.is_symlink():
            shutil.rmtree(cache)
    probes = cases(family, seed, episode)
    inputs = [r["input"] for r in probes]
    groups = defaultdict(lambda: {"passed": 0, "total": 0})
    error = None
    try:
        tree_hash(workspace)
        process = subprocess.run(sandbox.command(workspace, control_root,
            [sys.executable, "-I", "-B", "-c", "import os,sys,runpy; sys.path.insert(0,os.getcwd()); runpy.run_path('run.py',run_name='__main__')"], write_isolation=True, network=False),
            cwd=workspace, input=encode(inputs), capture_output=True, text=True, timeout=15,env=sandbox.worker_environment())
        if process.returncode:
            error = "entry_failure: " + process.stderr[-1000:]
            outputs = []
        else:
            outputs = json.loads(process.stdout)
            if not isinstance(outputs, list) or len(outputs) != len(probes):
                error, outputs = "invalid_batch_shape", []
    except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
        error, outputs = getattr(exc,"code",type(exc).__name__), []
    for index, probe in enumerate(probes):
        group = groups[probe["group"]]
        group["total"] += 1
        if index < len(outputs) and equal_json(outputs[index], probe["expected"]):
            group["passed"] += 1
    try:
        current = tree_hash(workspace)
        violated = [name for name, sha in protected.items() if current.get(name) != sha]
    except ValueError as exc:
        violated = ["workspace_boundary:"+getattr(exc,"code",type(exc).__name__)]
    behavior_success = not error and all(g["passed"] == g["total"] for g in groups.values()) and not violated
    valid_final = isinstance(final, dict) and isinstance(final.get("summary"), str) and bool(final["summary"].strip()) and final.get("completion_claim") in ("complete", "partial") and isinstance(final.get("memory_uses"), list)
    return {"behavior_success": behavior_success, "full_success": behavior_success and valid_final,
            "valid_final": valid_final, "groups": dict(groups), "entry_error": error,
            "protected_violations": violated, "false_completion": bool(valid_final and final["completion_claim"] == "complete" and not behavior_success),
            "suite_sha256": digest(probes),
            "provenance": "historical_failure_derived_synthetic"}


def equal_json(a, b):
    """JSON numeric int/float equivalence, but bool must never pass as int."""
    if type(a) is bool or type(b) is bool:
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal_json(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal_json(x, y) for x, y in zip(a, b))
    if type(a) in (int, float) and type(b) in (int, float):
        return a == b
    return type(a) is type(b) and a == b


def feedback(grade):
    # No hidden inputs or expected values cross back to solver/reflection.
    return {"full_success": grade["full_success"], "behavior_success": grade["behavior_success"],
            "failed_groups": [name for name, score in grade["groups"].items() if score["passed"] != score["total"]],
            "entry_error": grade["entry_error"], "protected_violations": grade["protected_violations"],
            "false_completion": grade["false_completion"]}

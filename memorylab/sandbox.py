"""Fail-closed macOS read isolation; no unsandboxed live fallback."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def worker_environment(source=None):
    """No model credentials or controller channels in subject/grade children."""
    source=os.environ if source is None else source
    env={key:value for key,value in source.items() if key in ("PATH","HOME","USER","TMPDIR","LANG","LC_ALL")}
    env.update(PYTHONNOUSERSITE="1",PYTHONDONTWRITEBYTECODE="1")
    if sys.platform=="darwin":env["__CF_USER_TEXT_ENCODING"]=str(os.getuid())+":0x0:0x0"
    return env


def profile(workspace, control_root, write_isolation=False, network=False, denied_roots=()):
    ws = str(Path(workspace).resolve())
    home = Path.home().resolve()
    denied = [str(Path(control_root).resolve())]+[str(Path(root).resolve()) for root in denied_roots]+[str(home / "Project"),
              str(home / ".agents"), str(home / ".codex" / "memories"),
              str(home / ".codex" / "sessions"), str(home / ".codex" / "archived_sessions"),
              str(home / ".codex" / "skills"), str(home / ".codex" / "automations"),
              str(home / ".codex" / "worktrees"), str(home / ".config")]
    literals = [str(home / ".codex" / filename) for filename in
                ("AGENTS.md", "AGENTS.override.md", "auth.json", "history.jsonl", "state_5.sqlite", "state_5.sqlite-wal", "state_5.sqlite-shm")]
    filters = ["(subpath " + json.dumps(p) + ")" for p in denied]
    filters += ["(literal " + json.dumps(p) + ")" for p in literals]
    # Sibling worker dirs are denied even though all are temporary.
    parent = str(Path(ws).parent)
    filters.append("(require-all (subpath " + json.dumps(parent) + ") (require-not (subpath " + json.dumps(ws) + ")))")
    rules = ["(version 1)", "(allow default)", "(deny process-info*)",
             "(deny file-read* " + " ".join(filters) + ")"]
    if write_isolation:
        rules.append("(deny file-write* (require-not (subpath " + json.dumps(ws) + ")))")
    if not network:
        rules.append("(deny network*)")
        rules.append("(allow network* (local unix-socket (subpath " + json.dumps(ws) + ")))")
    return "\n".join(rules)


def command(workspace, control_root, inner, write_isolation=False, network=True, denied_roots=()):
    executable = shutil.which("sandbox-exec")
    if sys.platform != "darwin" or not executable:
        raise RuntimeError("v1 requires macOS sandbox-exec; refusing unisolated execution")
    return [executable, "-p", profile(workspace, control_root, write_isolation, network,denied_roots)] + list(inner)


def probe(workspace, control_root):
    workspace, control_root = Path(workspace), Path(control_root)
    secret = control_root / "isolation-canary.txt"
    secret.write_text("private evaluation canary")
    outside = control_root / "write-canary.txt"
    script = '''import pathlib,sys,json
w=pathlib.Path(sys.argv[1]); target=pathlib.Path(sys.argv[2]); out=pathlib.Path(sys.argv[3])
checks={}
checks['workspace_read']=(w/'SPEC.md').is_file()
try: target.read_text(); checks['control_read_denied']=False
except PermissionError: checks['control_read_denied']=True
try: out.write_text('bad'); checks['outside_write_denied']=False
except PermissionError: checks['outside_write_denied']=True
(w/'probe-ok.txt').write_text('ok')
checks['workspace_write']=True
print(json.dumps(checks))
'''
    try:
        proc = subprocess.run(command(workspace, control_root,
            [sys.executable, "-c", script, str(workspace), str(secret), str(outside)], True, False),
            cwd=workspace, text=True, capture_output=True, timeout=15)
        if proc.returncode:
            raise RuntimeError("isolation probe failed: " + proc.stderr[-1000:])
        result = json.loads(proc.stdout)
        if not all(result.values()):
            raise RuntimeError("isolation boundary not enforced: " + str(result))
        return result
    finally:
        secret.unlink(missing_ok=True)
        outside.unlink(missing_ok=True)
        (workspace / "probe-ok.txt").unlink(missing_ok=True)

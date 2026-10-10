"""Identity of the actual fixture environment, without credential values."""
import hashlib
import platform
import shutil
from pathlib import Path

from .contracts import sha256
from .freeze import environment_identity
from .sandbox import worker_environment


def stage_environment_identity():
    sandbox = shutil.which("sandbox-exec")
    executable = Path(sandbox).resolve() if sandbox else None
    return {
        "python": environment_identity(),
        "system": {"system": platform.system(), "release": platform.release(),
                   "version": platform.version(), "machine": platform.machine()},
        "native_sandbox": {"path": str(executable) if executable else None,
                           "sha256": hashlib.sha256(executable.read_bytes()).hexdigest() if executable else None},
        "filtered_worker_environment_sha256": sha256(worker_environment()),
        "formal_environment_qualified": False,
    }

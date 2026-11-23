from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from flowkit.errors import FlowError
from flowkit.util import utcnow
from flowkit.validate import is_test_path


def snapshot_tests(repo: Path) -> dict[str, str]:
    import hashlib

    out: dict[str, str] = {}
    skip_parts = {".git", ".flow", "node_modules", "__pycache__", ".venv", "dist", "build"}
    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(repo)
        if any(part in skip_parts for part in rel.parts):
            continue
        rel_s = rel.as_posix()
        if is_test_path(rel_s):
            out[rel_s] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(out.items()))


def snapshot_delta(before: dict[str, str], after: dict[str, str]) -> list[str]:
    keys = set(before) | set(after)
    return sorted(key for key in keys if before.get(key) != after.get(key))


def run_command(repo: Path, command: str, timeout: int = 600) -> dict:
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise FlowError(f"The test command cannot be parsed: {exc}") from exc
    if not argv:
        raise FlowError("The test command is empty. Set one with --test-command on start.")
    try:
        proc = subprocess.run(
            argv,
            cwd=repo,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FlowError(f"The test command exceeded {timeout} seconds.") from exc
    except FileNotFoundError as exc:
        raise FlowError(f"Could not run `{argv[0]}`. It is not on PATH.") from exc
    return {
        "at": utcnow(),
        "command": command,
        "exit_code": proc.returncode,
        "stdout": (proc.stdout or "")[-20000:],
        "stderr": (proc.stderr or "")[-20000:],
    }

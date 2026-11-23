from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from flowkit.errors import FlowError


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(obj: object) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def write_json(path: Path, obj: object) -> None:
    atomic_write(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise FlowError(f"{path.name} must contain a JSON object.")
    return data


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise FlowError(f"git {' '.join(args)} failed: {detail}")
    return proc


def repo_root(start: Path) -> Path:
    proc = git(start if start.is_dir() else start.parent, "rev-parse", "--show-toplevel")
    return Path(proc.stdout.strip())


def require(obj: dict, key: str, kind: type, where: str) -> object:
    if key not in obj:
        raise FlowError(f"{where} is missing `{key}`.")
    value = obj[key]
    if not isinstance(value, kind):
        raise FlowError(f"{where}.{key} must be a {kind.__name__}.")
    return value


def require_str(obj: dict, key: str, where: str, min_len: int = 1) -> str:
    value = require(obj, key, str, where)
    assert isinstance(value, str)
    if len(value.strip()) < min_len:
        raise FlowError(f"{where}.{key} must be at least {min_len} characters.")
    return value.strip()

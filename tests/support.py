from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from flowkit.util import git


def init_repo(path: Path, *files: tuple[str, str]) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "flowkit@example.com")
    git(path, "config", "user.name", "Flowkit Test")
    (path / ".gitignore").write_text(".flow/\n__pycache__/\n*.pyc\n")
    for rel, text in files:
        dest = path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-m", "initial")
    return path


def commit_all(path: Path, message: str) -> None:
    git(path, "add", "-A")
    git(path, "commit", "-m", message)


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).resolve().parents[1]
    return subprocess.run(
        ["python3", "-m", "flowkit", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )

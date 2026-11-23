from __future__ import annotations

import json
import re
from pathlib import Path

from flowkit.engine import Run, active_slice_files, forbidden_prefixes, load_run
from flowkit.errors import FlowError
from flowkit.validate import is_doc_path, is_test_path

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "StrReplace"}
READ_PHASES = {"explore", "spec", "plan", "test_plan", "self_review", "diff", "pr", "done"}
BLOCKED_SHELL = re.compile(
    r"(git\s+push\b.*(?:-f\b|--force)|git\s+reset\s+--hard|\brm\s+-rf\b|drop\s+table\b|curl[^\n]*\|\s*(?:sh|bash))",
    re.IGNORECASE,
)


def evaluate(payload: dict, run: Run | None) -> tuple[int, str]:
    """Return (exit code, message). Exit 2 blocks the tool call. No run means allow."""
    if run is None or not isinstance(payload, dict):
        return 0, ""
    tool = str(payload.get("tool_name") or payload.get("tool") or "")
    phase = run.phase
    path = _path_of(payload, run.root)
    command = _command_of(payload)

    if BLOCKED_SHELL.search(command):
        return 2, "flowkit: that command is blocked in every phase (force-push, hard reset, rm -rf, drop table, curl|sh)."

    if tool in WRITE_TOOLS and phase in READ_PHASES:
        return 2, (
            f"flowkit: phase `{phase}` is read-only. "
            "Finish the artifact and let the human run the next flowkit command."
        )

    if tool in {"Bash", "Shell"} and phase in READ_PHASES and re.search(
        r"\bgit\s+(commit|add|checkout|reset|push|merge)\b", command
    ):
        return 2, f"flowkit: git writes are blocked while the phase is `{phase}`."

    if tool in WRITE_TOOLS and phase == "red":
        if path and not is_test_path(path):
            return 2, "flowkit: the red phase may only create or edit test files."

    if tool in WRITE_TOOLS and phase == "implement" and run.tier == "skip":
        if path and not is_doc_path(path):
            return 2, "flowkit: a skip run may only edit docs. Code moves the run onto the light tier."

    if tool in WRITE_TOOLS and phase == "implement" and run.tier != "skip":
        if not run.state.get("active_slice"):
            return 2, "flowkit: run `python -m flowkit prompt implement --slice <id>` before editing."
        if path and is_test_path(path):
            return 2, "flowkit: implementation does not edit tests. Those were locked at the red snapshot."
        allowed = set(active_slice_files(run) or [])
        if path and path not in allowed:
            return 2, (
                f"flowkit: `{path}` is outside the active slice. "
                "Allowed: " + (", ".join(sorted(allowed)) or "(none)") + "."
            )
        for prefix in forbidden_prefixes(run):
            norm = prefix.replace("\\", "/").lstrip("./")
            if path and norm and (path == norm or path.startswith(norm.rstrip("/") + "/")):
                return 2, f"flowkit: `{path}` is under forbidden prefix `{prefix}`."

    return 0, ""


def _input(payload: dict) -> dict:
    raw = payload.get("tool_input") or payload.get("input") or {}
    return raw if isinstance(raw, dict) else {}


def _path_of(payload: dict, root: Path) -> str | None:
    raw = _input(payload).get("file_path") or _input(payload).get("path") or _input(payload).get("file")
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw.strip())
    if path.is_absolute():
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()
    return path.as_posix()


def _command_of(payload: dict) -> str:
    data = _input(payload)
    command = data.get("command") or data.get("cmd") or ""
    return command if isinstance(command, str) else ""


def main() -> int:
    import sys

    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    try:
        run = load_run(Path.cwd())
    except FlowError:
        return 0
    code, message = evaluate(payload if isinstance(payload, dict) else {}, run)
    if message:
        print(message, file=sys.stderr)
    return code

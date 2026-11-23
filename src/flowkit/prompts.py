from __future__ import annotations

import json
from pathlib import Path

from flowkit.engine import Run, _manifest, _plan, _review_risk, set_active_slice
from flowkit.errors import FlowError
from flowkit.models import EFFORT, HOST, coder_for_slice, reviewer_choice
from flowkit.util import digest, read_json

PACKAGE = Path(__file__).resolve().parent
PROMPT_DIR = PACKAGE / "prompts"

PREAMBLE = """This is one phase of a flowkit run. Do this phase and stop.
The next phase is a new session with a different prompt. Do not start it.
Do not push, force-push, or merge. Do not claim that tests passed.
The human runs the flowkit commands named at the end.

"""


def _dump(obj: object) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def render(run: Run, phase: str, slice_id: str | None = None) -> str:
    if phase == "extract":
        raise FlowError("Use `python -m flowkit prompt extract --file <notes>` before a run exists.")
    if phase != run.phase and not (phase == "implement" and run.phase == "implement"):
        if phase != run.phase:
            raise FlowError(
                f"The current phase is `{run.phase}`. The prompt you asked for is `{phase}`. "
                "Run `python -m flowkit next`."
            )
    intake = read_json(run.run_dir / "intake.json")
    manifest = _manifest(run)
    mapping: dict[str, str] = {
        "run_id": run.state["id"],
        "tier": run.tier,
        "risk": run.state["risk"],
        "intake": _dump(intake),
        "test_command": manifest.get("test_command") or "(none detected — the human must set one)",
        "branch": run.state["branch"],
        "out": str(run.run_dir / {
            "explore": "explore.json",
            "spec": "spec.json",
            "plan": "plan.json",
            "test_plan": "test_plan.json",
            "red": "red-notes.md",
            "implement": "implement-notes.md",
            "self_review": "review.json",
        }.get(phase, f"{phase}.json")),
    }
    if (run.run_dir / "explore.json").exists():
        mapping["explore"] = _dump(read_json(run.run_dir / "explore.json"))
    if (run.run_dir / "spec.json").exists():
        spec = read_json(run.run_dir / "spec.json")
        mapping["spec"] = _dump(spec)
        mapping["spec_hash"] = digest(spec)
    if (run.run_dir / "plan.json").exists():
        plan = read_json(run.run_dir / "plan.json")
        mapping["plan"] = _dump(plan)
    if (run.run_dir / "test_plan.json").exists():
        mapping["test_plan"] = _dump(read_json(run.run_dir / "test_plan.json"))
    if phase == "implement":
        if not slice_id:
            raise FlowError("Implementation prompts are per slice. Pass --slice S1.")
        info = set_active_slice(run, slice_id)
        escalated = bool(run.state.get("escalation")) and run.state["escalation"].get("slice") == slice_id
        model, host = coder_for_slice(info, escalated)
        mapping["slice_id"] = slice_id
        mapping["slice"] = _dump(info)
        mapping["model"] = model
        mapping["host"] = host
        mapping["effort"] = EFFORT[model]
        mapping["allowed_files"] = ", ".join(entry["path"] for entry in info["files"])
        mapping["forbidden"] = ", ".join(_plan(run).get("forbidden_paths") or []) or "(none)"
    if phase == "self_review":
        authors = set(run.state.get("author_models") or [])
        if not authors:
            raise FlowError("No author model is recorded. Verify a slice with --author-model first.")
        primary, allowed = reviewer_choice(authors, _review_risk(run))
        mapping["model"] = primary
        mapping["host"] = HOST[primary]
        mapping["effort"] = EFFORT[primary]
        mapping["allowed_reviewers"] = ", ".join(sorted(allowed))
        mapping["authors"] = ", ".join(sorted(authors))
        mapping["diff"] = _diff_text(run)
        evidence = run.run_dir / "evidence"
        logs = []
        if evidence.exists():
            for path in sorted(evidence.glob("*.json")):
                payload = read_json(path)
                logs.append(f"### {path.name} exit {payload.get('exit_code')}\n{(payload.get('stdout') or '')[-1500:]}")
        mapping["evidence"] = "\n".join(logs) or "(no evidence files)"
    for key in ("explore", "spec", "spec_hash", "plan", "test_plan"):
        mapping.setdefault(key, "(none for this tier)")
    if phase in {"spec", "plan"} and run.state["risk"] == "high":
        mapping["model_rule"] = (
            "Triage risk is high. Use claude-opus-5-5 at high effort, or claude-fable-5-2 "
            "if your organization allows Fable's data retention. Otherwise stay on Opus."
        )
    elif phase == "spec":
        mapping["model_rule"] = "Use claude-opus-5-5 at high effort, in a fresh Claude Code session."
    elif phase == "plan":
        mapping["model_rule"] = "Use claude-opus-5-5 at high effort, in a fresh Claude Code session that has not edited this repo."
    elif phase == "explore":
        mapping["model_rule"] = "Use composer-2.5 in Cursor Ask mode. claude-sonnet-5-5 is acceptable in a large repo. Do not edit."
    elif phase == "test_plan":
        mapping["model_rule"] = "Use gpt-5.6-sol. grok-4.7 is the alternate. Do not use an Anthropic model; that family wrote the spec."
    elif phase == "red":
        mapping["model_rule"] = "Use composer-2.5 in Cursor. Edit test files only."
    template_path = PROMPT_DIR / f"{phase}.md"
    if not template_path.exists():
        raise FlowError(f"There is no prompt for `{phase}`.")
    text = PREAMBLE + template_path.read_text()
    for key, value in mapping.items():
        text = text.replace("{{" + key + "}}", value)
    missing = [token for token in _tokens(text)]
    if missing:
        raise FlowError(f"Prompt `{phase}` still has placeholders: {', '.join(missing)}.")
    out = run.run_dir / "prompts" / f"{phase}.md"
    if slice_id:
        out = run.run_dir / "prompts" / f"{phase}-{slice_id}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    return text


def _tokens(text: str) -> list[str]:
    import re

    return re.findall(r"\{\{([a-z0-9_]+)\}\}", text)


def _diff_text(run: Run) -> str:
    from flowkit.util import git

    base = _manifest(run)["base_sha"]
    diff = git(run.root, "diff", "--unified=3", base).stdout
    if len(diff) <= 12000:
        return diff or "(empty diff)"
    names = git(run.root, "diff", "--name-only", base).stdout
    return (
        "The diff is longer than the prompt budget. Files:\n"
        + names
        + "\n--- first 12000 characters ---\n"
        + diff[:12000]
        + "\n--- truncated ---"
    )


def extract_prompt(source: Path) -> str:
    if not source.is_file():
        raise FlowError(f"No notes file at {source}.")
    text = (PROMPT_DIR / "extract.md").read_text()
    return PREAMBLE + text.replace("{{notes}}", source.read_text())


def routing_markdown() -> str:
    from flowkit.models import ROUTING_ROWS

    lines = ["| Phase | Who | Model | Where | Why |", "| --- | --- | --- | --- | --- |"]
    for row in ROUTING_ROWS:
        lines.append(
            f"| {row['phase']} | {row['actor']} | {row['model']} | {row['host']} | {row['why']} |"
        )
    return "\n".join(lines)

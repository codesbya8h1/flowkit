from __future__ import annotations

import json
import re
import secrets
from pathlib import Path

from flowkit.errors import FlowError
from flowkit.intake import fetch_jira, from_file, from_text
from flowkit.models import FAMILY, coder_for_slice, family, reviewer_choice
from flowkit.triage import classify
from flowkit.util import digest, git, read_json, repo_root, utcnow, write_json
from flowkit.validate import (
    blocking_questions,
    is_doc_path,
    scan_secrets,
    validate_explore,
    validate_plan,
    validate_review,
    validate_spec,
    validate_test_plan,
)
from flowkit.verify import run_command, snapshot_delta, snapshot_tests

PHASES = {
    "full": [
        "intake",
        "triage",
        "explore",
        "spec",
        "plan",
        "test_plan",
        "red",
        "implement",
        "diff",
        "self_review",
        "pr",
        "done",
    ],
    "light": [
        "intake",
        "triage",
        "explore",
        "plan",
        "test_plan",
        "red",
        "implement",
        "diff",
        "self_review",
        "pr",
        "done",
    ],
    "skip": ["intake", "triage", "implement", "diff", "pr", "done"],
}

APPROVALS = {
    "full": {"spec", "plan", "test_plan", "diff", "self_review", "pr"},
    "light": {"plan", "test_plan", "diff", "self_review", "pr"},
    "skip": {"diff", "pr"},
}

ARTIFACTS = {
    "explore": "explore.json",
    "spec": "spec.json",
    "plan": "plan.json",
    "test_plan": "test_plan.json",
    "self_review": "review.json",
}

EXPLORE_MODELS = {"composer-2.5", "claude-sonnet-5-5", "claude-opus-5-5"}
SPEC_MODELS = {"claude-opus-5-5", "claude-fable-5-2"}
PLAN_MODELS = {"claude-opus-5-5", "claude-fable-5-2"}
TEST_MODELS = {"gpt-5.6-sol", "grok-4.7"}


def phases_for(tier: str) -> list[str]:
    return list(PHASES[tier])


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug or "change")[:40]


class Run:
    def __init__(self, root: Path, run_dir: Path, state: dict):
        self.root = root
        self.run_dir = run_dir
        self.state = state

    @property
    def tier(self) -> str:
        return self.state["tier"]

    @property
    def phase(self) -> str:
        return self.state["phase"]


def flow_dir(root: Path) -> Path:
    return root / ".flow"


def _event(run: Run, name: str, **detail: object) -> None:
    run.state.setdefault("history", []).append({"at": utcnow(), "event": name, **detail})


def save(run: Run) -> None:
    write_json(run.run_dir / "state.json", run.state)
    write_json(flow_dir(run.root) / "current", {"id": run.state["id"]})


def load_run(start: Path, run_id: str | None = None) -> Run:
    root = repo_root(start)
    pointer = flow_dir(root) / "current"
    if run_id is None:
        if not pointer.exists():
            raise FlowError("No flow run in this repository. Start one with `python -m flowkit start`.")
        run_id = read_json(pointer)["id"]
    run_dir = flow_dir(root) / "runs" / run_id
    state_path = run_dir / "state.json"
    if not state_path.exists():
        raise FlowError(f"No run named {run_id}.")
    return Run(root, run_dir, read_json(state_path))


def _detect_test_command(root: Path) -> str | None:
    if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists() or (root / "setup.cfg").exists():
        return "python -m pytest"
    package = root / "package.json"
    if package.exists():
        try:
            data = json.loads(package.read_text())
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict) and "test" in (data.get("scripts") or {}):
            return "npm test --silent"
    if (root / "go.mod").exists():
        return "go test ./..."
    if (root / "Cargo.toml").exists():
        return "cargo test"
    return None


def _fingerprint(root: Path, test_command: str | None) -> dict:
    sha = git(root, "rev-parse", "HEAD").stdout.strip()
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    detected = test_command or _detect_test_command(root)
    return {"base_sha": sha, "base_branch": branch, "test_command": detected}


_IGNORED_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".flow"}
_IGNORED_SUFFIXES = {".pyc", ".pyo"}


def _clean_tree(root: Path) -> list[str]:
    proc = git(root, "status", "--porcelain")
    dirty = []
    for line in proc.stdout.splitlines():
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        parts = Path(path).parts
        if any(part in _IGNORED_DIRS for part in parts):
            continue
        if Path(path).suffix in _IGNORED_SUFFIXES:
            continue
        dirty.append(line.strip())
    return dirty


def start_run(
    start: Path,
    *,
    text: str | None = None,
    title: str | None = None,
    file: Path | None = None,
    jira: str | None = None,
    jira_file: Path | None = None,
    ac_field: str | None = None,
    tier: str | None = None,
    test_command: str | None = None,
    branch: bool = True,
) -> Run:
    sources = [text is not None, file is not None, jira is not None, jira_file is not None]
    if sum(sources) != 1:
        raise FlowError("Pass exactly one of --text, --file, --jira, or --jira-file.")
    root = repo_root(start)
    dirty = _clean_tree(root)
    if dirty:
        raise FlowError(
            "The worktree has uncommitted changes. Commit or stash them, then start the run. "
            "A flow branch has to begin from a clean tree."
        )
    if text is not None:
        intake = from_text(text, title=title)
    elif file is not None:
        intake = from_file(file, ac_field=ac_field)
    elif jira_file is not None:
        intake = from_file(jira_file, ac_field=ac_field)
    else:
        assert jira is not None
        intake = fetch_jira(jira, ac_field=ac_field)
    try:
        triage = classify(intake, override=tier)
    except ValueError as exc:
        raise FlowError(str(exc)) from exc
    stamp = utcnow().replace("-", "").replace(":", "")
    run_id = f"FLOW-{stamp}-{secrets.token_hex(2)}"
    label = intake.get("key") or intake["title"]
    branch_name = f"flow/{_slug(str(label))}-{run_id[-4:]}"
    manifest = _fingerprint(root, test_command)
    if branch:
        git(root, "checkout", "-b", branch_name)
        manifest["flow_branch"] = branch_name
    else:
        manifest["flow_branch"] = manifest["base_branch"]
    order = phases_for(triage["tier"])
    # intake and triage are finished inside start.
    phase = order[2]
    status = "in_progress" if phase == "implement" else "awaiting_artifact"
    state = {
        "id": run_id,
        "created_at": utcnow(),
        "tier": triage["tier"],
        "risk": triage["risk"],
        "phase": phase,
        "status": status,
        "branch": manifest["flow_branch"],
        "approvals": {},
        "slices_green": {},
        "strikes": {},
        "author_models": [],
        "deviations": [],
        "waivers": [],
        "resolved_findings": [],
        "active_slice": None,
        "escalation": None,
        "history": [],
    }
    run = Run(root, flow_dir(root) / "runs" / run_id, state)
    write_json(run.run_dir / "intake.json", intake)
    write_json(run.run_dir / "triage.json", triage)
    write_json(run.run_dir / "manifest.json", manifest)
    _event(run, "start", tier=triage["tier"], risk=triage["risk"], phase=phase)
    save(run)
    return run


def _manifest(run: Run) -> dict:
    return read_json(run.run_dir / "manifest.json")


def _artifact_path(run: Run, phase: str) -> Path:
    return run.run_dir / ARTIFACTS[phase]


def load_artifact(path: Path) -> dict:
    raw = path.read_text()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
        if match is None:
            raise FlowError(
                "The artifact must be a JSON object, or a markdown file with one ```json object."
            )
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise FlowError(f"The JSON block in {path.name} does not parse: {exc}") from exc
    if not isinstance(data, dict):
        raise FlowError("The artifact must be a JSON object.")
    return data


def _spec(run: Run) -> dict | None:
    path = run.run_dir / "spec.json"
    if not path.exists():
        return None
    return read_json(path)


def _plan(run: Run) -> dict:
    path = run.run_dir / "plan.json"
    if not path.exists():
        raise FlowError("There is no approved plan yet.")
    return read_json(path)


def _criteria_ids(run: Run) -> set[str]:
    spec = _spec(run)
    if spec is not None:
        return {item["id"] for item in spec["acceptance_criteria"]}
    plan = _plan(run)
    return {item["id"] for item in plan["acceptance_criteria"]}


def _allowed_for(run: Run, phase: str) -> set[str]:
    risk = run.state["risk"]
    if phase == "explore":
        return set(EXPLORE_MODELS)
    if phase == "spec":
        if risk == "high":
            return set(SPEC_MODELS)
        return {"claude-opus-5-5"}
    if phase == "plan":
        if risk == "high":
            return set(PLAN_MODELS)
        return {"claude-opus-5-5"}
    if phase == "test_plan":
        return set(TEST_MODELS)
    if phase == "self_review":
        authors = set(run.state.get("author_models") or [])
        if not authors:
            raise FlowError("Verify at least one slice with --author-model before requesting review.")
        _primary, allowed = reviewer_choice(authors, _review_risk(run))
        return allowed
    raise FlowError(f"{phase} has no model allow-list.")


def _review_risk(run: Run) -> str:
    plan_path = run.run_dir / "plan.json"
    if plan_path.exists():
        return str(read_json(plan_path).get("risk") or run.state["risk"])
    return str(run.state["risk"])


def _advance(run: Run) -> None:
    order = phases_for(run.tier)
    index = order.index(run.phase)
    if index + 1 >= len(order):
        run.state["phase"] = "done"
        run.state["status"] = "done"
        return
    nxt = order[index + 1]
    run.state["phase"] = nxt
    run.state["active_slice"] = None
    if nxt == "done":
        run.state["status"] = "done"
    elif nxt == "implement":
        run.state["status"] = "in_progress"
    elif nxt == "red":
        run.state["status"] = "in_progress"
    elif nxt == "diff":
        _capture_diff(run)
        run.state["status"] = "awaiting_approval"
    elif nxt == "pr":
        run.state["status"] = "awaiting_artifact"
    else:
        run.state["status"] = "awaiting_artifact"
    _event(run, "advance", phase=nxt, status=run.state["status"])


def _require_phase(run: Run, phase: str) -> None:
    if run.phase != phase:
        raise FlowError(
            f"This run is in `{run.phase}` ({run.state['status']}). "
            f"`{phase}` is not the current phase. `{next_action(run)}`"
        )


def accept_artifact(
    run: Run,
    phase: str,
    source: Path,
    *,
    allow_model: bool = False,
    reason: str = "",
    replace: bool = False,
) -> None:
    if phase not in ARTIFACTS:
        raise FlowError(f"`{phase}` does not take an artifact file.")
    revising = phase == "plan" and run.phase == "implement" and run.state["status"] == "blocked"
    replacing = phase == "plan" and replace and run.phase in {"plan", "test_plan", "red", "implement"}
    if phase != run.phase and not revising and not replacing:
        _require_phase(run, phase)
    if allow_model and len(reason.strip()) < 20:
        raise FlowError("--allow-model needs a --reason of at least 20 characters, recorded on the run.")
    if not source.is_file():
        raise FlowError(f"No artifact file at {source}.")
    data = load_artifact(source)
    allowed = _allowed_for(run, phase) if not (phase == "self_review" and allow_model) else set(FAMILY)
    if phase != "self_review":
        allowed = _allowed_for(run, phase)
    if phase == "explore":
        validate_explore(run.root, data, allowed, allow_model)
    elif phase == "spec":
        validate_spec(data, allowed, allow_model)
    elif phase == "plan":
        spec = _spec(run) if run.tier == "full" else None
        spec_hash = digest(spec) if spec is not None else None
        if spec is not None and "spec" not in run.state["approvals"]:
            raise FlowError("Approve the spec before accepting a plan. `python -m flowkit approve spec --note ...`")
        validate_plan(run.root, data, allowed, allow_model, spec, spec_hash)
    elif phase == "test_plan":
        validate_test_plan(data, _criteria_ids(run), allowed, allow_model)
    elif phase == "self_review":
        if not allow_model:
            allowed = _allowed_for(run, "self_review")
        validate_review(data, allowed, allow_model)
        data_authors = set(run.state.get("author_models") or [])
        if family(data["model"]) in {family(author) for author in data_authors} and not allow_model:
            raise FlowError(
                f"Review model `{data['model']}` is in the same family as the author. "
                "Open a fresh session on the model `flowkit prompt self_review` names."
            )
    if allow_model:
        run.state["deviations"].append(
            {"at": utcnow(), "phase": phase, "model": data.get("model"), "reason": reason.strip()}
        )
    write_json(_artifact_path(run, phase), data)
    _event(run, "accept", phase=phase, model=data.get("model"))
    if revising or (replacing and run.phase != "plan"):
        run.state["phase"] = "plan"
        run.state["status"] = "awaiting_approval"
        run.state["strikes"] = {}
        run.state["escalation"] = None
        run.state["slices_green"] = {}
        run.state["active_slice"] = None
        for gate in ("plan", "test_plan", "diff", "self_review"):
            run.state["approvals"].pop(gate, None)
        red = run.run_dir / "red.json"
        if red.exists():
            red.unlink()
        evidence = run.run_dir / "evidence"
        if evidence.exists():
            for child in evidence.glob("*.json"):
                child.unlink()
        _event(run, "plan_revised")
        save(run)
        return
    if phase == "self_review":
        run.state["review_sha"] = git(run.root, "rev-parse", "HEAD").stdout.strip()
        blocking = _blocking_findings(run)
        run.state["status"] = "blocked" if blocking else "awaiting_approval"
        save(run)
        return
    if phase in APPROVALS[run.tier]:
        run.state["status"] = "awaiting_approval"
        save(run)
        return
    _advance(run)
    save(run)


def _note(note: str, gate: str) -> str:
    cleaned = note.strip()
    if len(cleaned) < 8:
        raise FlowError(f"Approving {gate} needs a --note of at least 8 characters. Say what you checked.")
    return cleaned


def approve(run: Run, gate: str, note: str) -> None:
    if gate not in APPROVALS[run.tier]:
        allowed = ", ".join(sorted(APPROVALS[run.tier]))
        raise FlowError(f"Tier `{run.tier}` has no `{gate}` gate. Its gates are: {allowed}.")
    _require_phase(run, gate)
    text = _note(note, gate)
    if gate == "spec":
        spec = _spec(run)
        if spec is None:
            raise FlowError("Accept a spec file before approving it.")
        questions = blocking_questions(spec)
        if questions:
            raise FlowError("The spec still has a blocking question: " + questions[0])
    if gate in ARTIFACTS and not _artifact_path(run, gate).exists():
        raise FlowError(f"Accept the {gate} artifact before approving it.")
    if gate == "diff":
        _assert_diff_clean(run)
    if gate == "self_review":
        blocking = _blocking_findings(run)
        if blocking:
            raise FlowError(
                "High findings still open: "
                + ", ".join(blocking)
                + ". Waive one with a reason, or resolve it with a new commit."
            )
    if gate == "pr":
        if not (run.run_dir / "pr.md").exists():
            raise FlowError("Write the pull request body first: `python -m flowkit pr`.")
    run.state["approvals"][gate] = {"at": utcnow(), "note": text, "hash": _gate_hash(run, gate)}
    _event(run, "approve", gate=gate)
    _advance(run)
    save(run)


def _gate_hash(run: Run, gate: str) -> str:
    if gate in ARTIFACTS and _artifact_path(run, gate).exists():
        return digest(read_json(_artifact_path(run, gate)))
    if gate == "diff" and (run.run_dir / "diff.json").exists():
        return digest(read_json(run.run_dir / "diff.json"))
    if gate == "pr" and (run.run_dir / "pr.md").exists():
        return digest((run.run_dir / "pr.md").read_text())
    return ""


def _blocking_findings(run: Run) -> list[str]:
    path = run.run_dir / "review.json"
    if not path.exists():
        return []
    review = read_json(path)
    waived = {item["id"] for item in run.state.get("waivers") or []}
    resolved = set(run.state.get("resolved_findings") or [])
    blocking = []
    for finding in review.get("findings") or []:
        if finding.get("severity") == "high" and finding["id"] not in waived and finding["id"] not in resolved:
            blocking.append(finding["id"])
    return blocking


def waive(run: Run, finding_id: str, reason: str) -> None:
    _require_phase(run, "self_review")
    if len(reason.strip()) < 20:
        raise FlowError("A waiver needs a --reason of at least 20 characters. Say why the finding can ship.")
    review = read_json(run.run_dir / "review.json")
    ids = {item["id"] for item in review.get("findings") or []}
    if finding_id not in ids:
        raise FlowError(f"The review has no finding `{finding_id}`.")
    run.state["waivers"].append({"id": finding_id, "reason": reason.strip(), "at": utcnow()})
    if not _blocking_findings(run):
        run.state["status"] = "awaiting_approval"
    _event(run, "waive", finding=finding_id)
    save(run)


def resolve_finding(run: Run, finding_id: str) -> None:
    _require_phase(run, "self_review")
    review = read_json(run.run_dir / "review.json")
    ids = {item["id"] for item in review.get("findings") or []}
    if finding_id not in ids:
        raise FlowError(f"The review has no finding `{finding_id}`.")
    head = git(run.root, "rev-parse", "HEAD").stdout.strip()
    if head == run.state.get("review_sha"):
        raise FlowError("Resolve requires a new commit after the review. The HEAD sha has not moved.")
    if finding_id not in run.state["resolved_findings"]:
        run.state["resolved_findings"].append(finding_id)
    if not _blocking_findings(run):
        run.state["status"] = "awaiting_approval"
    _event(run, "resolve", finding=finding_id, sha=head)
    save(run)


def _slice_map(run: Run) -> dict[str, dict]:
    plan = _plan(run)
    return {item["id"]: item for item in plan["slices"]}


def verify(run: Run, mode: str, *, slice_id: str | None = None, author_model: str | None = None) -> dict:
    if mode not in {"red", "green"}:
        raise FlowError("Verification mode must be red or green.")
    manifest = _manifest(run)
    command = manifest.get("test_command")
    if not command:
        raise FlowError(
            "This repository has no detected test command. "
            "Restart with --test-command, or set manifest test_command."
        )
    if mode == "red":
        _require_phase(run, "red")
        evidence = run_command(run.root, command)
        write_json(run.run_dir / "evidence" / "red.json", evidence)
        if evidence["exit_code"] == 0:
            raise FlowError(
                "The tests passed before implementation. They do not lock the new behavior. "
                "Add a failing test for an acceptance criterion and run `python -m flowkit verify --red` again."
            )
        snapshot = snapshot_tests(run.root)
        write_json(
            run.run_dir / "red.json",
            {"at": utcnow(), "exit_code": evidence["exit_code"], "snapshot": snapshot, "head": _head(run)},
        )
        _event(run, "verify_red", exit_code=evidence["exit_code"])
        _advance(run)
        save(run)
        return evidence
    _require_phase(run, "implement")
    if run.state["status"] == "blocked":
        raise FlowError("This slice is blocked. Revise the plan, or follow the escalation note from `flowkit status`.")
    if run.tier == "skip":
        raise FlowError("Skip tier has no test gate. Use `python -m flowkit ready` after the docs edit.")
    if not slice_id:
        raise FlowError("Name the slice: `python -m flowkit verify --green --slice S1 --author-model <model>`.")
    slices = _slice_map(run)
    if slice_id not in slices:
        raise FlowError(f"The plan has no slice `{slice_id}`.")
    if not author_model:
        raise FlowError("Pass --author-model with the model that wrote this slice.")
    if author_model not in FAMILY:
        raise FlowError(f"Unknown author model `{author_model}`.")
    evidence = run_command(run.root, slices[slice_id]["verification"] or command)
    count = len(list((run.run_dir / "evidence").glob("green-*.json"))) + 1 if (run.run_dir / "evidence").exists() else 1
    write_json(run.run_dir / "evidence" / f"green-{slice_id}-{count}.json", evidence)
    if evidence["exit_code"] != 0:
        strikes = run.state["strikes"].get(slice_id, 0) + 1
        run.state["strikes"][slice_id] = strikes
        _event(run, "verify_fail", slice=slice_id, strikes=strikes)
        if strikes >= 3:
            run.state["status"] = "blocked"
            save(run)
            raise FlowError(
                f"Slice {slice_id} failed verification {strikes} times. Stop the coding session. "
                "Write a revised plan and `python -m flowkit accept plan <file>`. "
                "The run will return to the plan gate."
            )
        if strikes == 2:
            run.state["escalation"] = {"slice": slice_id, "model": "claude-opus-5-5", "host": "Claude Code"}
            save(run)
            raise FlowError(
                f"Slice {slice_id} failed twice. The next session uses claude-opus-5-5 in a fresh Claude Code window. "
                f"Run `python -m flowkit prompt implement --slice {slice_id}` and do the work there."
            )
        save(run)
        raise FlowError(
            f"Slice {slice_id} failed verification (strike {strikes} of 2). "
            "Fix the code in the same session and run the same verify command."
        )
    red_path = run.run_dir / "red.json"
    if not red_path.exists():
        raise FlowError("Green verification requires a recorded red run. `python -m flowkit verify --red`.")
    red = read_json(red_path)
    changed = snapshot_delta(red["snapshot"], snapshot_tests(run.root))
    if changed and slice_id not in set(run.state.get("test_edit_allow") or []):
        raise FlowError(
            "Test files changed after the red snapshot: "
            + ", ".join(changed)
            + ". The implementation phase does not edit tests. "
            "If a fixture truly must change, `python -m flowkit allow-test-edits --slice "
            + slice_id
            + " --reason ...`, then re-run verify."
        )
    recommended, _host = coder_for_slice(slices[slice_id], bool(run.state.get("escalation")))
    if author_model != recommended:
        run.state["deviations"].append(
            {
                "at": utcnow(),
                "phase": "implement",
                "slice": slice_id,
                "model": author_model,
                "recommended": recommended,
                "reason": "Author model differed from the slice recommendation.",
            }
        )
    if author_model not in run.state["author_models"]:
        run.state["author_models"].append(author_model)
    run.state["slices_green"][slice_id] = {"at": utcnow(), "model": author_model, "head": _head(run)}
    run.state["strikes"][slice_id] = 0
    run.state["escalation"] = None
    _event(run, "verify_green", slice=slice_id, model=author_model)
    if set(run.state["slices_green"]) >= set(slices):
        _advance(run)
    save(run)
    return evidence


def allow_test_edits(run: Run, slice_id: str, reason: str) -> None:
    _require_phase(run, "implement")
    if slice_id not in _slice_map(run):
        raise FlowError(f"The plan has no slice `{slice_id}`.")
    if len(reason.strip()) < 20:
        raise FlowError("--reason must be at least 20 characters.")
    run.state.setdefault("test_edit_allow", []).append(slice_id)
    _event(run, "allow_test_edits", slice=slice_id, reason=reason.strip())
    save(run)


def ready(run: Run) -> None:
    if run.tier != "skip":
        raise FlowError("`ready` is for a docs-only skip run. This run verifies with `flowkit verify --green`.")
    _require_phase(run, "implement")
    if _clean_tree(run.root):
        raise FlowError("Commit the docs change, then run `python -m flowkit ready`.")
    ahead = git(run.root, "rev-list", "--count", f"{_manifest(run)['base_sha']}..HEAD").stdout.strip()
    if int(ahead or "0") < 1:
        raise FlowError("Commit the docs change on this branch, then run `python -m flowkit ready`.")
    _capture_diff(run)
    names = [item["path"] for item in read_json(run.run_dir / "diff.json")["files"]]
    code = [path for path in names if not is_doc_path(path)]
    if code:
        run.state["tier"] = "light"
        run.state["phase"] = "plan"
        run.state["status"] = "awaiting_artifact"
        _event(run, "retier", tier="light", reason="diff contains non-doc files")
        save(run)
        raise FlowError(
            "The diff touches code (" + ", ".join(code) + "). "
            "The run is now tier light and is waiting for a plan."
        )
    run.state["phase"] = "diff"
    run.state["status"] = "awaiting_approval"
    _event(run, "ready")
    save(run)


def _head(run: Run) -> str:
    return git(run.root, "rev-parse", "HEAD").stdout.strip()


def _capture_diff(run: Run) -> dict:
    base = _manifest(run)["base_sha"]
    name_status = git(run.root, "diff", "--name-status", base).stdout.splitlines()
    numstat = git(run.root, "diff", "--numstat", base).stdout.splitlines()
    files = []
    for line in name_status:
        parts = line.split("\t")
        if len(parts) >= 2:
            files.append({"status": parts[0], "path": parts[-1]})
    added = deleted = 0
    for line in numstat:
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            added += int(parts[0])
            deleted += int(parts[1])
    diff_text = git(run.root, "diff", "--unified=3", base).stdout
    secrets_hits = scan_secrets(diff_text)
    dirty = _clean_tree(run.root)
    ahead = git(run.root, "rev-list", "--count", f"{base}..HEAD").stdout.strip()
    report = {
        "base": base,
        "head": _head(run),
        "files": files,
        "added": added,
        "deleted": deleted,
        "secrets": secrets_hits,
        "dirty": dirty,
        "commits_ahead": int(ahead or "0"),
        "tests_changed_after_red": _tests_changed(run),
    }
    write_json(run.run_dir / "diff.json", report)
    return report


def _tests_changed(run: Run) -> list[str]:
    red_path = run.run_dir / "red.json"
    if not red_path.exists():
        return []
    red = read_json(red_path)
    return snapshot_delta(red.get("snapshot") or {}, snapshot_tests(run.root))


def _assert_diff_clean(run: Run) -> None:
    report = _capture_diff(run)
    if report["dirty"]:
        raise FlowError(
            "Commit the change before approving the diff. Uncommitted paths: "
            + "; ".join(report["dirty"][:8])
        )
    if report["commits_ahead"] < 1:
        raise FlowError("The branch has no commit on top of the base. Commit the change, then approve the diff.")
    if report["secrets"] and not run.state.get("secret_waiver"):
        raise FlowError(
            f"The diff matches {len(report['secrets'])} secret pattern(s). "
            "Remove them, or `python -m flowkit waive-secret --reason ...` if the match is a fixture."
        )
    if report["tests_changed_after_red"] and not run.state.get("test_edit_allow"):
        raise FlowError(
            "Tests changed after the red snapshot: "
            + ", ".join(report["tests_changed_after_red"])
            + ". That breaks the lock. Put the behavior change in product code, or revise the plan."
        )
    if run.tier == "skip":
        code = [item["path"] for item in report["files"] if not is_doc_path(item["path"])]
        if code:
            raise FlowError("Skip tier cannot approve a code diff. The run should have been re-tiered.")


def waive_secret(run: Run, reason: str) -> None:
    if len(reason.strip()) < 20:
        raise FlowError("--reason must be at least 20 characters and must say why the match is safe.")
    run.state["secret_waiver"] = {"at": utcnow(), "reason": reason.strip()}
    _event(run, "waive_secret")
    save(run)


def write_pr(run: Run) -> Path:
    _require_phase(run, "pr")
    _assert_diff_clean(run)
    report = read_json(run.run_dir / "diff.json")
    intake = read_json(run.run_dir / "intake.json")
    lines = [
        f"## {intake['title']}",
        "",
        f"Run `{run.state['id']}` · tier `{run.tier}` · risk `{_review_risk(run)}`",
        "",
        "### Request",
        "",
        intake.get("body") or intake["title"],
        "",
        "### What changed",
        "",
        f"{report['added']} lines added, {report['deleted']} lines deleted, {report['commits_ahead']} commit(s) ahead of `{report['base'][:8]}`.",
        "",
    ]
    for item in report["files"]:
        lines.append(f"- `{item['status']}` `{item['path']}`")
    lines += ["", "### Gates", ""]
    for gate, info in run.state["approvals"].items():
        lines.append(f"- {gate}: {info['note']}")
    if run.state.get("deviations"):
        lines += ["", "### Model deviations", ""]
        for item in run.state["deviations"]:
            lines.append(f"- {item['phase']}: used `{item.get('model')}` ({item.get('reason')})")
    if run.state.get("waivers"):
        lines += ["", "### Waived review findings", ""]
        for item in run.state["waivers"]:
            lines.append(f"- {item['id']}: {item['reason']}")
    review_path = run.run_dir / "review.json"
    if review_path.exists():
        review = read_json(review_path)
        lines += ["", f"### Self review ({review['model']})", "", review.get("checked") or "", ""]
        for finding in review.get("findings") or []:
            lines.append(
                f"- {finding['id']} {finding['severity']} `{finding['path']}`: {finding['claim']}"
            )
    plan_path = run.run_dir / "plan.json"
    if plan_path.exists():
        plan = read_json(plan_path)
        lines += ["", "### Rollback", "", plan.get("rollback") or "", ""]
    lines += [
        "### Evidence",
        "",
        "Test logs are in `.flow/runs/" + run.state["id"] + "/evidence/`.",
        "This body was assembled from those artifacts. It was not rewritten by a model.",
        "",
    ]
    path = run.run_dir / "pr.md"
    path.write_text("\n".join(lines))
    run.state["status"] = "awaiting_approval"
    _event(run, "pr_body")
    save(run)
    return path


def fix(run: Run) -> None:
    """Send a reviewed change back to implementation. The diff and the review are taken again."""
    _require_phase(run, "self_review")
    if run.tier == "skip":
        raise FlowError("Skip tier has no review to fix against.")
    run.state["phase"] = "implement"
    run.state["status"] = "in_progress"
    run.state["slices_green"] = {}
    run.state["active_slice"] = None
    for gate in ("diff", "self_review"):
        run.state["approvals"].pop(gate, None)
    run.state["waivers"] = []
    run.state["resolved_findings"] = []
    _event(run, "fix")
    save(run)


def push_branch(run: Run) -> str:
    if run.phase != "done":
        raise FlowError("Approve the pull request before pushing. `python -m flowkit approve pr --note ...`.")
    branch = run.state["branch"]
    git(run.root, "push", "-u", "origin", branch)
    _event(run, "push", branch=branch)
    save(run)
    return branch


def set_active_slice(run: Run, slice_id: str) -> dict:
    _require_phase(run, "implement")
    info = _slice_map(run)[slice_id] if slice_id in _slice_map(run) else None
    if info is None:
        raise FlowError(f"The plan has no slice `{slice_id}`.")
    run.state["active_slice"] = slice_id
    _event(run, "active_slice", slice=slice_id)
    save(run)
    return info


def next_action(run: Run) -> str:
    phase = run.phase
    status = run.state["status"]
    if phase == "done":
        return "python -m flowkit push"
    if status == "blocked" and phase == "implement":
        return "python -m flowkit accept plan <revised-plan.json>"
    if status == "blocked" and phase == "self_review":
        return "python -m flowkit waive <F1> --reason \"...\"   or commit a fix and `python -m flowkit resolve <F1>`"
    if phase == "explore":
        return "python -m flowkit prompt explore"
    if phase == "spec" and status == "awaiting_artifact":
        return "python -m flowkit prompt spec"
    if phase == "spec":
        return "python -m flowkit approve spec --note \"what you confirmed\""
    if phase == "plan" and status == "awaiting_artifact":
        return "python -m flowkit prompt plan"
    if phase == "plan":
        return "python -m flowkit approve plan --note \"what you confirmed\""
    if phase == "test_plan" and status == "awaiting_artifact":
        return "python -m flowkit prompt test_plan"
    if phase == "test_plan":
        return "python -m flowkit approve test_plan --note \"cases cover the criteria\""
    if phase == "red":
        return "python -m flowkit prompt red"
    if phase == "implement" and run.tier == "skip":
        return "python -m flowkit ready"
    if phase == "implement":
        pending = [
            item["id"]
            for item in (_plan(run)["slices"] if (run.run_dir / "plan.json").exists() else [])
            if item["id"] not in run.state.get("slices_green", {})
        ]
        slice_id = pending[0] if pending else "S1"
        if run.state.get("slices_green"):
            return f"python -m flowkit verify --green --slice {slice_id} --author-model <model>"
        return f"python -m flowkit prompt implement --slice {slice_id}"
    if phase == "diff":
        return "python -m flowkit diff    then    python -m flowkit approve diff --note \"what you read\""
    if phase == "self_review" and status == "awaiting_artifact":
        return "python -m flowkit prompt self_review"
    if phase == "self_review":
        return "python -m flowkit approve self_review --note \"how you treated the findings\""
    if phase == "pr" and status == "awaiting_artifact":
        return "python -m flowkit pr"
    if phase == "pr":
        return "python -m flowkit approve pr --note \"ship it because ...\""
    return "python -m flowkit status"


def status_text(run: Run) -> str:
    triage = read_json(run.run_dir / "triage.json")
    lines = [
        f"Run {run.state['id']}",
        f"Tier {run.tier}  risk {run.state['risk']}  phase {run.phase}  status {run.state['status']}",
        f"Branch {run.state['branch']}",
        "Triage: " + " ".join(triage.get("reasons") or []),
        f"Next: {next_action(run)}",
    ]
    if run.phase == "spec" or "spec" in run.state["approvals"]:
        spec_path = run.run_dir / "spec.json"
        if spec_path.exists():
            lines.append(f"Spec hash: {digest(read_json(spec_path))}")
    if run.state.get("escalation"):
        item = run.state["escalation"]
        lines.append(f"Escalation: slice {item['slice']} moves to {item['model']} in {item['host']}.")
    if run.state.get("author_models"):
        lines.append("Author models: " + ", ".join(run.state["author_models"]))
    if run.phase == "self_review" and run.state.get("author_models"):
        primary, allowed = reviewer_choice(set(run.state["author_models"]), _review_risk(run))
        lines.append(f"Reviewer: {primary} (also allowed: {', '.join(sorted(allowed))})")
    history = run.state.get("history") or []
    if history:
        lines.append("Recent:")
        for item in history[-6:]:
            extra = {key: value for key, value in item.items() if key not in {"at", "event"}}
            lines.append(f"  {item['at']}  {item['event']}  {extra}")
    return "\n".join(lines)


def active_slice_files(run: Run) -> list[str] | None:
    """Files the current slice may edit. None when the phase is not implement."""
    if run.phase != "implement" or run.tier == "skip":
        return None
    slice_id = run.state.get("active_slice")
    if not slice_id or not (run.run_dir / "plan.json").exists():
        return []
    info = _slice_map(run).get(slice_id)
    if not info:
        return []
    return [entry["path"] for entry in info.get("files") or []]


def forbidden_prefixes(run: Run) -> list[str]:
    if not (run.run_dir / "plan.json").exists():
        return []
    return [str(item) for item in read_json(run.run_dir / "plan.json").get("forbidden_paths") or []]


def render_models() -> str:
    from flowkit.models import ROUTING_ROWS

    lines = ["phase\twho\tmodel\thost\twhy"]
    for row in ROUTING_ROWS:
        lines.append("\t".join([row["phase"], row["actor"], row["model"], row["host"], row["why"]]))
    return "\n".join(lines)

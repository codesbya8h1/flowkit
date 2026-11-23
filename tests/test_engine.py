import json
from pathlib import Path

from flowkit.engine import load_run, start_run
from flowkit.errors import FlowError
from flowkit.guard import evaluate
from flowkit.install import install
from flowkit.prompts import render
from flowkit.util import digest, write_json
from tests.support import commit_all, init_repo, run_cli, write_json as dump

APP = 'def feature():\n    return "old"\n'
CHECK = """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from app import feature

raise SystemExit(0 if feature() == "exported" else 1)
"""
COMMAND = "python3 tests/check_feature.py"


def _explore():
    return {
        "model": "composer-2.5",
        "summary": "feature() currently returns the previous billing token state.",
        "current_behavior": "A second refresh creates another charge instead of returning the first one.",
        "paths": [{"path": "src/app.py", "why": "This is the function the ticket changes.", "new": False}],
        "existing_tests": [],
        "risks": ["A retry can charge the customer twice."],
        "unknowns": [],
    }


def _spec():
    return {
        "model": "claude-opus-5-5",
        "problem": "Refreshing a billing token twice creates two charges. The second call has to be safe.",
        "user_visible_outcome": "A repeated refresh with the same key returns the original charge.",
        "non_goals": ["Changing the price of a charge."],
        "acceptance_criteria": [
            {
                "id": "AC-1",
                "given": "a billing token",
                "when": "refresh is called twice with one key",
                "then": "the caller receives the original charge and no second charge",
            }
        ],
        "open_questions": [],
        "risk_notes": ["This is payment-adjacent."],
    }


def _plan(spec_hash: str, verification: str = COMMAND):
    return {
        "model": "claude-opus-5-5",
        "spec_hash": spec_hash,
        "summary": "Return a stable exported marker from feature() and lock it with the check script.",
        "non_goals": ["Reworking the charge table."],
        "risk": "high",
        "rollback": "Revert the feature() return value to the previous string.",
        "forbidden_paths": ["migrations/"],
        "acceptance_criteria": [
            {"id": "AC-1", "statement": "A repeated refresh returns the original charge and creates no second charge."}
        ],
        "slices": [
            {
                "id": "S1",
                "goal": "feature() reports the exported state",
                "foundational": False,
                "verification": verification,
                "files": [{"path": "src/app.py", "new": False}],
            }
        ],
    }


def _tests():
    return {
        "model": "gpt-5.6-sol",
        "cases": [
            {
                "name": "second refresh is free",
                "covers": ["AC-1"],
                "negative": False,
                "expect": "the original charge id comes back",
            },
            {
                "name": "missing key is rejected",
                "covers": ["AC-1"],
                "negative": True,
                "expect": "no charge is created",
            },
        ],
    }


def _review(model: str = "claude-opus-5-5", high: bool = True):
    findings = []
    if high:
        findings.append(
            {
                "id": "F1",
                "severity": "high",
                "path": "src/app.py",
                "claim": "The check only looks at a string, not at a second charge.",
                "evidence": "tests/check_feature.py compares feature() to exported.",
            }
        )
    return {
        "model": model,
        "verdict": "block" if high else "approve",
        "checked": "AC-1, src/app.py, and the negative missing-key case.",
        "findings": findings,
    }


def _start(tmp_path: Path, command: str = COMMAND):
    repo = init_repo(tmp_path / "repo", ("src/app.py", APP))
    run = start_run(
        repo,
        text="Make billing token refresh idempotent. A second refresh with the same key must not create a second charge.",
        test_command=command,
    )
    assert run.tier == "full"
    assert run.phase == "explore"
    return run


def _through_plan(run, verification: str = COMMAND):
    scratch = run.root.parent
    from flowkit.engine import accept_artifact, approve

    accept_artifact(run, "explore", dump(scratch / "explore.json", _explore()))
    assert run.phase == "spec"
    accept_artifact(run, "spec", dump(scratch / "spec.json", _spec()))
    approve(run, "spec", "The charge-once outcome is the ticket.")
    spec_hash = digest(json.loads((run.run_dir / "spec.json").read_text()))
    accept_artifact(run, "plan", dump(scratch / "plan.json", _plan(spec_hash, verification)))
    approve(run, "plan", "One slice, and migrations stay forbidden.")
    accept_artifact(run, "test_plan", dump(scratch / "tests.json", _tests()))
    approve(run, "test_plan", "The negative case is the missing key.")
    assert run.phase == "red"
    return run


def test_prompt_and_illegal_skip(tmp_path: Path):
    run = _start(tmp_path)
    text = render(run, "explore")
    assert "composer-2.5" in text
    assert "{{" not in text
    from flowkit.engine import accept_artifact

    try:
        accept_artifact(run, "plan", dump(tmp_path / "early.json", {"model": "claude-opus-5-5"}))
        raise AssertionError("plan was accepted during explore")
    except FlowError as exc:
        assert "explore" in str(exc)


def test_full_run_blocks_on_review_until_a_human_waives(tmp_path: Path):
    run = _through_plan(_start(tmp_path))
    (run.root / "tests").mkdir()
    (run.root / "tests" / "check_feature.py").write_text(CHECK)
    from flowkit.engine import accept_artifact, approve, verify, waive, write_pr

    red = verify(run, "red")
    assert red["exit_code"] != 0
    assert run.phase == "implement"
    (run.root / "src" / "app.py").write_text('def feature():\n    return "exported"\n')
    commit_all(run.root, "return the exported token state")
    green = verify(run, "green", slice_id="S1", author_model="composer-2.5")
    assert green["exit_code"] == 0
    assert run.phase == "diff"
    approve(run, "diff", "Read the one-line behavior change.")
    assert run.phase == "self_review"
    accept_artifact(run, "self_review", dump(tmp_path / "review.json", _review()))
    assert run.state["status"] == "blocked"
    try:
        approve(run, "self_review", "Shipping with the finding open.")
        raise AssertionError("high finding did not block")
    except FlowError:
        pass
    waive(run, "F1", "The fixture stands in for the charge id. A follow-up ticket covers the ledger.")
    approve(run, "self_review", "Waived F1 with a follow-up named.")
    assert run.phase == "pr"
    body = write_pr(run).read_text()
    assert "Waived review findings" in body
    assert "composer-2.5" not in body.split("Self review")[1][:80]
    approve(run, "pr", "Ship the idempotent refresh.")
    assert run.phase == "done"
    try:
        from flowkit.engine import push_branch

        push_branch(run)
        raise AssertionError("push should fail without a remote")
    except FlowError as exc:
        assert "git push" in str(exc)


def test_same_family_review_is_rejected(tmp_path: Path):
    run = _through_plan(_start(tmp_path))
    (run.root / "tests").mkdir()
    (run.root / "tests" / "check_feature.py").write_text(CHECK)
    from flowkit.engine import accept_artifact, approve, verify

    verify(run, "red")
    (run.root / "src" / "app.py").write_text('def feature():\n    return "exported"\n')
    commit_all(run.root, "implement")
    verify(run, "green", slice_id="S1", author_model="claude-opus-5-5")
    approve(run, "diff", "Read the diff after an Opus implementation.")
    try:
        accept_artifact(run, "self_review", dump(tmp_path / "review.json", _review("claude-opus-5-5", high=False)))
        raise AssertionError("author reviewed its own family")
    except FlowError as exc:
        assert "family" in str(exc).lower() or "gpt-5.6-sol" in str(exc)


def test_two_strikes_then_a_revised_plan(tmp_path: Path):
    command = 'python3 -c "import sys; raise SystemExit(1)"'
    run = _through_plan(_start(tmp_path, command), verification=command)
    from flowkit.engine import accept_artifact, verify

    verify(run, "red")
    for _ in range(2):
        try:
            verify(run, "green", slice_id="S1", author_model="composer-2.5")
        except FlowError:
            pass
    assert run.state["escalation"]["model"] == "claude-opus-5-5"
    try:
        verify(run, "green", slice_id="S1", author_model="composer-2.5")
        raise AssertionError("third failure should block")
    except FlowError as exc:
        assert "3" in str(exc)
    assert run.state["status"] == "blocked"
    spec_hash = digest(json.loads((run.run_dir / "spec.json").read_text()))
    accept_artifact(run, "plan", dump(tmp_path / "plan2.json", _plan(spec_hash, verification=command)))
    assert run.phase == "plan"
    assert run.state["status"] == "awaiting_approval"


def test_secret_and_test_edit_block_the_diff(tmp_path: Path):
    run = _through_plan(_start(tmp_path))
    (run.root / "tests").mkdir()
    (run.root / "tests" / "check_feature.py").write_text(CHECK)
    from flowkit.engine import approve, verify

    verify(run, "red")
    (run.root / "src" / "app.py").write_text(
        'def feature():\n    return "exported"\n\napi_key = "abcdefghijklmnop"\n'
    )
    commit_all(run.root, "leaky implementation")
    verify(run, "green", slice_id="S1", author_model="composer-2.5")
    try:
        approve(run, "diff", "I ignored the key.")
        raise AssertionError("secret did not block")
    except FlowError as exc:
        assert "secret" in str(exc).lower()
    (run.root / "src" / "app.py").write_text('def feature():\n    return "exported"\n')
    (run.root / "tests" / "check_feature.py").write_text(CHECK + "\n")
    commit_all(run.root, "drop the key and touch the test")
    try:
        approve(run, "diff", "Tests moved after red.")
        raise AssertionError("test edit did not block")
    except FlowError as exc:
        assert "red snapshot" in str(exc)


def test_skip_tier_rejects_code(tmp_path: Path):
    repo = init_repo(tmp_path / "docs", ("README.md", "# Notes\n\nSpeling.\n"), ("src/app.py", APP))
    run = start_run(repo, text="Changelog spelling\n\nA spelling fix in the changelog only.", tier="skip")
    assert run.phase == "implement"
    (repo / "README.md").write_text("# Notes\n\nSpelling.\n")
    commit_all(repo, "fix spelling")
    from flowkit.engine import approve, ready, write_pr

    ready(run)
    assert run.phase == "diff"
    approve(run, "diff", "One word in the changelog.")
    write_pr(run)
    approve(run, "pr", "Docs only, ship it.")
    assert run.phase == "done"

    repo2 = init_repo(tmp_path / "code", ("README.md", "# Notes\n"), ("src/app.py", APP))
    run2 = start_run(repo2, text="Changelog spelling only, no code.", tier="skip")
    (repo2 / "src" / "app.py").write_text(APP + "\n")
    commit_all(repo2, "quietly change code")
    try:
        ready(run2)
        raise AssertionError("code slipped through skip")
    except FlowError as exc:
        assert "light" in str(exc)
    reloaded = load_run(repo2)
    assert reloaded.tier == "light"
    assert reloaded.phase == "plan"


def test_guard_phase_lock(tmp_path: Path):
    run = _start(tmp_path)
    blocked, message = evaluate(
        {"tool_name": "Write", "tool_input": {"file_path": str(run.root / "src" / "app.py")}},
        run,
    )
    assert blocked == 2
    assert "read-only" in message
    blocked, _ = evaluate(
        {"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}},
        run,
    )
    assert blocked == 2
    allowed, _ = evaluate(
        {"tool_name": "Bash", "tool_input": {"command": "git status"}},
        run,
    )
    assert allowed == 0

    write_json(
        run.run_dir / "plan.json",
        {
            "forbidden_paths": ["migrations/"],
            "slices": [
                {
                    "id": "S1",
                    "files": [
                        {"path": "src/app.py"},
                        {"path": "migrations/001.py"},
                    ],
                }
            ],
        },
    )
    run.state["phase"] = "implement"
    run.state["tier"] = "full"
    blocked, message = evaluate(
        {"tool_name": "Write", "tool_input": {"file_path": "src/app.py"}},
        run,
    )
    assert blocked == 2
    assert "prompt implement" in message
    run.state["active_slice"] = "S1"
    assert evaluate({"tool_name": "Write", "tool_input": {"file_path": "src/app.py"}}, run)[0] == 0
    assert evaluate({"tool_name": "Write", "tool_input": {"file_path": "tests/check_feature.py"}}, run)[0] == 2
    blocked, message = evaluate(
        {"tool_name": "Write", "tool_input": {"file_path": "migrations/001.py"}},
        run,
    )
    assert blocked == 2
    assert "forbidden" in message


def test_install_does_not_clobber_claude_md(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("local rules\n")
    lines = install(tmp_path)
    assert (tmp_path / ".cursor" / "rules" / "flowkit.mdc").is_file()
    assert (tmp_path / ".claude" / "FLOW.md").is_file()
    assert (tmp_path / "CLAUDE.md").read_text() == "local rules\n"
    assert any("CLAUDE.md" in line for line in lines)


def test_cli_start_status_and_models(tmp_path: Path):
    repo = init_repo(tmp_path / "cli", ("src/app.py", APP))
    started = run_cli(
        "start",
        "--repo",
        str(repo),
        "--text",
        "Make billing token refresh idempotent so a second call does not charge.",
        "--test-command",
        COMMAND,
    )
    assert started.returncode == 0, started.stderr
    assert "phase explore" in started.stdout
    status = run_cli("status", "--repo", str(repo))
    assert status.returncode == 0
    assert "Next:" in status.stdout
    models = run_cli("models")
    assert models.returncode == 0
    assert "gpt-5.6-sol" in models.stdout
    assert "claude-opus-5-5" in models.stdout

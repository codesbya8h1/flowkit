from __future__ import annotations

import argparse
import sys
from pathlib import Path

from flowkit.engine import (
    accept_artifact,
    allow_test_edits,
    approve,
    fix,
    load_run,
    next_action,
    push_branch,
    ready,
    resolve_finding,
    start_run,
    status_text,
    verify,
    waive,
    waive_secret,
    write_pr,
    _capture_diff,
)
from flowkit.errors import FlowError
from flowkit.guard import main as guard_main
from flowkit.install import install
from flowkit.prompts import extract_prompt, render, routing_markdown


def _repo(args: argparse.Namespace) -> Path:
    return Path(args.repo).resolve()


def _run(args: argparse.Namespace):
    return load_run(_repo(args), getattr(args, "run", None))


def _print_status(run) -> None:
    print(status_text(run))


def cmd_start(args: argparse.Namespace) -> int:
    run = start_run(
        _repo(args),
        text=args.text,
        title=args.title,
        file=Path(args.file).resolve() if args.file else None,
        jira=args.jira,
        jira_file=Path(args.jira_file).resolve() if args.jira_file else None,
        ac_field=args.ac_field,
        tier=args.tier,
        test_command=args.test_command,
        branch=not args.no_branch,
    )
    _print_status(run)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    _print_status(_run(args))
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    print(next_action(_run(args)))
    return 0


def cmd_prompt(args: argparse.Namespace) -> int:
    if args.phase == "extract":
        if not args.file:
            raise FlowError("prompt extract needs --file with the notes to structure.")
        print(extract_prompt(Path(args.file).resolve()))
        return 0
    run = _run(args)
    text = render(run, args.phase, slice_id=args.slice)
    print(f"Wrote {run.run_dir / 'prompts'}", file=sys.stderr)
    print(text)
    return 0


def cmd_accept(args: argparse.Namespace) -> int:
    run = _run(args)
    accept_artifact(
        run,
        args.phase,
        Path(args.file).resolve(),
        allow_model=args.allow_model,
        reason=args.reason or "",
        replace=args.replace,
    )
    _print_status(run)
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    run = _run(args)
    approve(run, args.gate, args.note)
    _print_status(run)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    run = _run(args)
    mode = "red" if args.red else "green"
    evidence = verify(run, mode, slice_id=args.slice, author_model=args.author_model)
    print(f"exit {evidence['exit_code']}  {evidence['command']}")
    _print_status(run)
    return 0


def cmd_ready(args: argparse.Namespace) -> int:
    run = _run(args)
    ready(run)
    _print_status(run)
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    run = _run(args)
    report = _capture_diff(run)
    print(f"{report['added']} added, {report['deleted']} deleted, {report['commits_ahead']} commit(s) ahead")
    for item in report["files"]:
        print(f"  {item['status']}\t{item['path']}")
    if report["dirty"]:
        print("Uncommitted:")
        for line in report["dirty"]:
            print(f"  {line}")
    if report["secrets"]:
        print(f"Secret patterns: {len(report['secrets'])}")
    if report["tests_changed_after_red"]:
        print("Tests changed after red: " + ", ".join(report["tests_changed_after_red"]))
    return 0


def cmd_waive(args: argparse.Namespace) -> int:
    run = _run(args)
    waive(run, args.finding, args.reason)
    _print_status(run)
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    run = _run(args)
    resolve_finding(run, args.finding)
    _print_status(run)
    return 0


def cmd_waive_secret(args: argparse.Namespace) -> int:
    run = _run(args)
    waive_secret(run, args.reason)
    _print_status(run)
    return 0


def cmd_allow_tests(args: argparse.Namespace) -> int:
    run = _run(args)
    allow_test_edits(run, args.slice, args.reason)
    _print_status(run)
    return 0


def cmd_fix(args: argparse.Namespace) -> int:
    run = _run(args)
    fix(run)
    _print_status(run)
    return 0


def cmd_pr(args: argparse.Namespace) -> int:
    run = _run(args)
    path = write_pr(run)
    print(path.read_text())
    print(f"Wrote {path}", file=sys.stderr)
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    run = _run(args)
    branch = push_branch(run)
    print(f"Pushed {branch}")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    for line in install(_repo(args)):
        print(line)
    return 0


def cmd_models(_args: argparse.Namespace) -> int:
    print(routing_markdown())
    return 0


def _add_run_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", default=".", help="Product repository. Defaults to the current directory.")
    parser.add_argument("--run", help="Run id. Defaults to the current run.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flowkit",
        description="Gated Cursor and Claude coding workflow. Python owns the order; a human owns the gates.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    start = sub.add_parser("start", help="Open a run from a ticket, a file, or a message.")
    _add_run_flags(start)
    source = start.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="The request as a message.")
    source.add_argument("--file", help="A markdown brief or a JSON request.")
    source.add_argument("--jira", help="A Jira issue key, for example PROJ-123.")
    source.add_argument("--jira-file", help="A Jira issue saved as JSON.")
    start.add_argument("--title", help="Title, when --text has no heading.")
    start.add_argument("--tier", choices=["skip", "light", "full"])
    start.add_argument("--test-command", help="Override the detected test command.")
    start.add_argument("--ac-field", help="Jira custom field id that holds acceptance criteria.")
    start.add_argument("--no-branch", action="store_true", help="Stay on the current branch.")
    start.set_defaults(func=cmd_start)

    status = sub.add_parser("status", help="Show the phase, the risk, and the next command.")
    _add_run_flags(status)
    status.set_defaults(func=cmd_status)

    nxt = sub.add_parser("next", help="Print the single next command.")
    _add_run_flags(nxt)
    nxt.set_defaults(func=cmd_next)

    prompt = sub.add_parser("prompt", help="Write the prompt for the current phase.")
    _add_run_flags(prompt)
    prompt.add_argument(
        "phase",
        choices=["extract", "explore", "spec", "plan", "test_plan", "red", "implement", "self_review"],
    )
    prompt.add_argument("--slice", help="Slice id for an implement prompt.")
    prompt.add_argument("--file", help="Notes file for prompt extract.")
    prompt.set_defaults(func=cmd_prompt)

    accept = sub.add_parser("accept", help="Validate a model artifact and store it on the run.")
    _add_run_flags(accept)
    accept.add_argument("phase", choices=["explore", "spec", "plan", "test_plan", "self_review"])
    accept.add_argument("file")
    accept.add_argument("--allow-model", action="store_true")
    accept.add_argument("--reason", default="")
    accept.add_argument("--replace", action="store_true", help="Send a revised plan back through the plan gate.")
    accept.set_defaults(func=cmd_accept)

    approve = sub.add_parser("approve", help="Human gate. The note is stored on the run and copied into the PR.")
    _add_run_flags(approve)
    approve.add_argument("gate", choices=["spec", "plan", "test_plan", "diff", "self_review", "pr"])
    approve.add_argument("--note", required=True)
    approve.set_defaults(func=cmd_approve)

    verify_cmd = sub.add_parser("verify", help="Run the tests and record the exit code.")
    _add_run_flags(verify_cmd)
    mode = verify_cmd.add_mutually_exclusive_group(required=True)
    mode.add_argument("--red", action="store_true", help="Require a failing test.")
    mode.add_argument("--green", action="store_true", help="Require the slice command to pass.")
    verify_cmd.add_argument("--slice")
    verify_cmd.add_argument("--author-model")
    verify_cmd.set_defaults(func=cmd_verify)

    ready_cmd = sub.add_parser("ready", help="Finish a docs-only skip run and open the diff gate.")
    _add_run_flags(ready_cmd)
    ready_cmd.set_defaults(func=cmd_ready)

    diff = sub.add_parser("diff", help="Summarize the branch diff, secrets, and test-file drift.")
    _add_run_flags(diff)
    diff.set_defaults(func=cmd_diff)

    waive_cmd = sub.add_parser("waive", help="Waive a high review finding with a written reason.")
    _add_run_flags(waive_cmd)
    waive_cmd.add_argument("finding")
    waive_cmd.add_argument("--reason", required=True)
    waive_cmd.set_defaults(func=cmd_waive)

    resolve_cmd = sub.add_parser("resolve", help="Mark a finding resolved after a new commit.")
    _add_run_flags(resolve_cmd)
    resolve_cmd.add_argument("finding")
    resolve_cmd.set_defaults(func=cmd_resolve)

    secret = sub.add_parser("waive-secret", help="Record why a secret-pattern match is safe to ship.")
    _add_run_flags(secret)
    secret.add_argument("--reason", required=True)
    secret.set_defaults(func=cmd_waive_secret)

    allow = sub.add_parser("allow-test-edits", help="Allow a slice to change tests after the red snapshot.")
    _add_run_flags(allow)
    allow.add_argument("--slice", required=True)
    allow.add_argument("--reason", required=True)
    allow.set_defaults(func=cmd_allow_tests)

    fix_cmd = sub.add_parser("fix", help="Send the branch back to implement after review.")
    _add_run_flags(fix_cmd)
    fix_cmd.set_defaults(func=cmd_fix)

    pr = sub.add_parser("pr", help="Assemble the pull request body from the artifacts.")
    _add_run_flags(pr)
    pr.set_defaults(func=cmd_pr)

    push = sub.add_parser("push", help="Push the branch. Refuses until the PR gate is approved.")
    _add_run_flags(push)
    push.set_defaults(func=cmd_push)

    inst = sub.add_parser("install", help="Copy Cursor rules, Claude instructions, and a PR template.")
    inst.add_argument("--repo", default=".")
    inst.set_defaults(func=cmd_install)

    models = sub.add_parser("models", help="Print which model does which phase, and why.")
    models.set_defaults(func=cmd_models)

    guard = sub.add_parser("guard", help="Claude Code hook. Reads a tool call on stdin and blocks illegal edits.")
    guard.set_defaults(func=lambda _args: guard_main())

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FlowError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

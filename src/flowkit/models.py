"""Which model does which job, and why that job is separate.

Routing is a workflow decision, recorded here so prompts, the CLI, and the
reviewer check all read the same table. November 2026 roles:

- Opus 5.5 is the judgment model: ambiguous requirements, the plan, and the
  review of code it did not write.
- Fable 5.2 is the escalation model for high-risk design and high-risk review,
  and only when the org's retention policy allows it. Opus remains valid.
- Composer 2.5 is the in-editor model: read-only exploration and scoped edits
  inside Cursor, where the files are already open.
- Sonnet 5.5 is the long tool-loop model in Claude Code for a multi-file slice
  that is already specified.
- Sol 5.6 writes the test plan, because the model that wrote the spec should
  not also invent the tests.
- Grok 4.7 is the alternate adversary when Sol's family already touched the
  code, and the second reviewer when the author was an Anthropic model.
- Haiku 5.5 is optional extraction for a messy note. Intake itself is Python.
"""

from __future__ import annotations

FAMILY = {
    "composer-2.5": "cursor",
    "claude-haiku-5-5": "anthropic",
    "claude-sonnet-5-5": "anthropic",
    "claude-opus-5-5": "anthropic",
    "claude-fable-5-2": "anthropic",
    "gpt-5.6-sol": "openai",
    "grok-4.7": "xai",
}

HOST = {
    "composer-2.5": "Cursor",
    "claude-haiku-5-5": "Claude",
    "claude-sonnet-5-5": "Claude Code",
    "claude-opus-5-5": "Claude Code",
    "claude-fable-5-2": "Claude Code",
    "gpt-5.6-sol": "a fresh Cursor or Codex chat",
    "grok-4.7": "a fresh Cursor chat",
}

EFFORT = {
    "composer-2.5": "medium",
    "claude-haiku-5-5": "low",
    "claude-sonnet-5-5": "medium",
    "claude-opus-5-5": "high",
    "claude-fable-5-2": "high",
    "gpt-5.6-sol": "medium",
    "grok-4.7": "medium",
}


def known(model: str) -> bool:
    return model in FAMILY


def family(model: str) -> str:
    if model not in FAMILY:
        known_list = ", ".join(sorted(FAMILY))
        raise KeyError(f"Unknown model `{model}`. Known models: {known_list}.")
    return FAMILY[model]


def reviewer_choice(author_models: set[str], risk: str) -> tuple[str, set[str]]:
    """Pick a reviewer whose family wrote none of the code.

    Code written outside Anthropic is reviewed by Opus, the strongest judgment
    model that did not write it. Code written by Anthropic is reviewed by Sol,
    with Grok as the alternate, so the author cannot grade itself.
    """
    families = {family(model) for model in author_models}
    if "anthropic" not in families:
        allowed = {"claude-opus-5-5"}
        if risk == "high":
            allowed.add("claude-fable-5-2")
        return "claude-opus-5-5", allowed

    allowed = {"gpt-5.6-sol", "grok-4.7"}
    if "openai" in families:
        allowed.discard("gpt-5.6-sol")
    if "xai" in families:
        allowed.discard("grok-4.7")
    if not allowed:
        raise KeyError(
            "Every reviewer family already wrote code in this run. "
            "Keep a single author model and re-verify the slices."
        )
    primary = "gpt-5.6-sol" if "gpt-5.6-sol" in allowed else "grok-4.7"
    return primary, allowed


def coder_for_slice(slice_info: dict, escalated: bool) -> tuple[str, str]:
    """Return (model, host) for one implementation slice."""
    if escalated or slice_info.get("foundational") is True:
        return "claude-opus-5-5", "Claude Code"
    files = slice_info.get("files") or []
    if len(files) >= 4:
        return "claude-sonnet-5-5", "Claude Code"
    return "composer-2.5", "Cursor"


ROUTING_ROWS = [
    {
        "phase": "intake",
        "actor": "Python",
        "model": "none",
        "host": "shell",
        "why": "A ticket, a file, or a message becomes one JSON record. No model is required to copy fields.",
    },
    {
        "phase": "extract (optional)",
        "actor": "model",
        "model": "claude-haiku-5-5",
        "host": "Claude",
        "why": "Only when the note is unstructured. The model fills the intake schema; Python still opens the run.",
    },
    {
        "phase": "triage",
        "actor": "Python",
        "model": "none",
        "host": "shell",
        "why": "Tier and risk come from the words in the request, so two runs of the same ticket triage the same way.",
    },
    {
        "phase": "explore",
        "actor": "model, read-only",
        "model": "composer-2.5",
        "host": "Cursor Ask",
        "why": "The repo is already open. This pass only reports paths it actually opened.",
    },
    {
        "phase": "spec",
        "actor": "model",
        "model": "claude-opus-5-5",
        "host": "Claude Code",
        "why": "Acceptance criteria are a judgment call. Fable 5.2 is allowed when triage risk is high.",
    },
    {
        "phase": "plan",
        "actor": "model",
        "model": "claude-opus-5-5",
        "host": "Claude Code",
        "why": "The plan names files, slices, and a rollback. A fresh session, separate from the session that will type the code.",
    },
    {
        "phase": "test plan",
        "actor": "model, different family",
        "model": "gpt-5.6-sol",
        "host": "fresh chat",
        "why": "The model that wrote the spec does not invent the tests. Grok 4.7 is the alternate.",
    },
    {
        "phase": "red",
        "actor": "model, then pytest",
        "model": "composer-2.5",
        "host": "Cursor",
        "why": "Tests are written first. Python runs them and keeps the run here until the new behavior fails.",
    },
    {
        "phase": "implement",
        "actor": "model",
        "model": "composer-2.5, or Sonnet / Opus per slice",
        "host": "Cursor, or Claude Code",
        "why": "Scoped slices stay in Cursor. Four or more files move to Sonnet. Foundational and second-strike slices move to Opus.",
    },
    {
        "phase": "verify",
        "actor": "Python",
        "model": "none",
        "host": "shell",
        "why": "Exit codes and a hash of the test files are the evidence. A model's claim that tests passed is ignored.",
    },
    {
        "phase": "diff",
        "actor": "human",
        "model": "none",
        "host": "git",
        "why": "The human reads the change. Python blocks the gate on secrets, a dirty tree, and tests edited after the red snapshot.",
    },
    {
        "phase": "self review",
        "actor": "model, fresh context",
        "model": "the other family",
        "host": "a new session",
        "why": "The author is not the reviewer. Opus reviews Composer and Sonnet. Sol or Grok reviews Opus.",
    },
    {
        "phase": "pull request",
        "actor": "Python, then human",
        "model": "none",
        "host": "shell",
        "why": "The PR body is assembled from the artifacts. The human approves it, and only then can the branch be pushed.",
    },
]

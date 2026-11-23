from __future__ import annotations

from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
TEMPLATES = PACKAGE / "templates"


def install(root: Path) -> list[str]:
    """Copy Cursor and Claude entry points. Never overwrite a file the repo already has."""
    root = root.resolve()
    written: list[str] = []
    skipped: list[str] = []

    def place(src_rel: str, dest_rel: str, overwrite: bool = False) -> None:
        src = TEMPLATES / src_rel
        dest = root / dest_rel
        if dest.exists() and not overwrite:
            skipped.append(dest_rel)
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text())
        written.append(dest_rel)

    place("cursor/flowkit.mdc", ".cursor/rules/flowkit.mdc", overwrite=True)
    place("claude/FLOW.md", ".claude/FLOW.md", overwrite=True)
    place("claude/CLAUDE.md", "CLAUDE.md", overwrite=False)
    place("claude/settings.hooks.json", ".claude/flowkit.settings.json", overwrite=True)
    place("github/pull_request_template.md", ".github/pull_request_template.md", overwrite=False)
    place("AGENTS.md", "AGENTS.md", overwrite=False)
    return written + [f"(left in place) {name}" for name in skipped]

from __future__ import annotations

import re
from pathlib import Path

from flowkit.errors import FlowError
from flowkit.models import FAMILY, known
from flowkit.util import require, require_str

AC_ID = re.compile(r"^AC-[A-Za-z0-9]+$")
SLICE_ID = re.compile(r"^S[1-9][0-9]*$")
FINDING_ID = re.compile(r"^F[1-9][0-9]*$")
SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(r"(?i)(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{12,}['\"]"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
]
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs"}


def is_test_path(rel: str) -> bool:
    path = Path(rel)
    if any(part in TEST_DIRS for part in path.parts):
        return True
    name = path.name
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith("_test.go")
        or ".test." in name
        or ".spec." in name
    )


def is_doc_path(rel: str) -> bool:
    return Path(rel).suffix.lower() in {".md", ".txt", ".rst", ".adoc"}


def _rel_path(value: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FlowError(f"{where} needs a relative path.")
    rel = value.strip().replace("\\", "/")
    if rel.startswith("/") or ".." in Path(rel).parts:
        raise FlowError(f"{where} path `{value}` must stay inside the repository.")
    return rel


def _existing_or_new(repo: Path, entry: dict, where: str) -> str:
    rel = _rel_path(require_str(entry, "path", where), where)
    is_new = entry.get("new") is True
    if not is_new and not (repo / rel).exists():
        raise FlowError(
            f"{where} cites `{rel}`, which is not in the repository. "
            "Set `new` to true only for a file this change creates."
        )
    if is_new and (repo / rel).exists():
        raise FlowError(f"{where} marks `{rel}` as new, and that file already exists.")
    return rel


def check_model(data: dict, allowed: set[str], where: str) -> str:
    model = require_str(data, "model", where)
    if not known(model):
        raise FlowError(f"{where} uses unknown model `{model}`.")
    if model not in allowed:
        allowed_list = ", ".join(sorted(allowed))
        raise FlowError(
            f"{where} must be written by {allowed_list}. This file says `{model}`. "
            "Re-run the phase in a session on an allowed model, or pass --allow-model with a reason."
        )
    return model


def validate_explore(repo: Path, data: dict, allowed: set[str], allow_model: bool) -> None:
    if not allow_model:
        check_model(data, allowed, "explore")
    else:
        check_model(data, set(FAMILY), "explore")
    require_str(data, "summary", "explore", min_len=20)
    require_str(data, "current_behavior", "explore", min_len=20)
    paths = require(data, "paths", list, "explore")
    if not paths:
        raise FlowError("explore.paths must name at least one file you opened.")
    for index, entry in enumerate(paths):
        if not isinstance(entry, dict):
            raise FlowError(f"explore.paths[{index}] must be an object.")
        _existing_or_new(repo, entry, f"explore.paths[{index}]")
        require_str(entry, "why", f"explore.paths[{index}]", min_len=8)
    for key in ("risks", "unknowns"):
        values = require(data, key, list, "explore")
        if not all(isinstance(item, str) and item.strip() for item in values):
            raise FlowError(f"explore.{key} must be a list of sentences.")


def _criteria(data: dict, where: str) -> list[dict]:
    items = require(data, "acceptance_criteria", list, where)
    if not items:
        raise FlowError(f"{where}.acceptance_criteria must have at least one criterion.")
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise FlowError(f"{where}.acceptance_criteria[{index}] must be an object.")
        ident = require_str(item, "id", f"{where}.acceptance_criteria[{index}]")
        if not AC_ID.match(ident):
            raise FlowError(f"Criterion id `{ident}` must look like AC-1.")
        if ident in seen:
            raise FlowError(f"Criterion id `{ident}` is duplicated.")
        seen.add(ident)
        if "statement" in item:
            require_str(item, "statement", f"{where}.acceptance_criteria[{index}]", min_len=10)
        else:
            for field in ("given", "when", "then"):
                require_str(item, field, f"{where}.acceptance_criteria[{index}]", min_len=3)
    return items


def validate_spec(data: dict, allowed: set[str], allow_model: bool) -> None:
    if not allow_model:
        check_model(data, allowed, "spec")
    else:
        check_model(data, set(FAMILY), "spec")
    require_str(data, "problem", "spec", min_len=20)
    require_str(data, "user_visible_outcome", "spec", min_len=10)
    non_goals = require(data, "non_goals", list, "spec")
    if not non_goals:
        raise FlowError("spec.non_goals needs at least one item, so the slice stays bounded.")
    _criteria(data, "spec")
    questions = require(data, "open_questions", list, "spec")
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            raise FlowError(f"spec.open_questions[{index}] must be an object.")
        require_str(question, "question", f"spec.open_questions[{index}]", min_len=8)
        if "blocking" not in question or not isinstance(question["blocking"], bool):
            raise FlowError(f"spec.open_questions[{index}].blocking must be true or false.")


def blocking_questions(data: dict) -> list[str]:
    return [
        item["question"]
        for item in data.get("open_questions") or []
        if isinstance(item, dict) and item.get("blocking") is True
    ]


def _slice_files(repo: Path, slice_info: dict, where: str) -> list[str]:
    files = require(slice_info, "files", list, where)
    if not files:
        raise FlowError(f"{where}.files must name the files this slice may touch.")
    paths: list[str] = []
    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            raise FlowError(f"{where}.files[{index}] must be an object.")
        paths.append(_existing_or_new(repo, entry, f"{where}.files[{index}]"))
    return paths


def validate_plan(
    repo: Path,
    data: dict,
    allowed: set[str],
    allow_model: bool,
    spec: dict | None,
    spec_hash: str | None,
) -> None:
    if not allow_model:
        check_model(data, allowed, "plan")
    else:
        check_model(data, set(FAMILY), "plan")
    require_str(data, "summary", "plan", min_len=20)
    require_str(data, "rollback", "plan", min_len=10)
    risk = require_str(data, "risk", "plan")
    if risk not in {"low", "medium", "high"}:
        raise FlowError("plan.risk must be low, medium, or high.")
    non_goals = require(data, "non_goals", list, "plan")
    if not non_goals:
        raise FlowError("plan.non_goals needs at least one item.")
    criteria = _criteria(data, "plan")
    if spec is not None:
        got = require_str(data, "spec_hash", "plan")
        if spec_hash and got != spec_hash:
            raise FlowError("plan.spec_hash does not match the approved spec. Copy the hash from `flowkit status`.")
        spec_ids = {item["id"] for item in spec["acceptance_criteria"]}
        plan_ids = {item["id"] for item in criteria}
        if spec_ids != plan_ids:
            raise FlowError(
                "plan.acceptance_criteria must be the same ids as the approved spec: "
                + ", ".join(sorted(spec_ids))
            )
    slices = require(data, "slices", list, "plan")
    if not slices:
        raise FlowError("plan.slices must contain at least one slice.")
    forbidden = require(data, "forbidden_paths", list, "plan")
    forbidden_norm = [str(item).replace("\\", "/").lstrip("./") for item in forbidden]
    seen: set[str] = set()
    for index, slice_info in enumerate(slices):
        if not isinstance(slice_info, dict):
            raise FlowError(f"plan.slices[{index}] must be an object.")
        ident = require_str(slice_info, "id", f"plan.slices[{index}]")
        if not SLICE_ID.match(ident):
            raise FlowError(f"Slice id `{ident}` must look like S1.")
        if ident in seen:
            raise FlowError(f"Slice id `{ident}` is duplicated.")
        seen.add(ident)
        require_str(slice_info, "goal", f"plan.slices[{index}]", min_len=8)
        require_str(slice_info, "verification", f"plan.slices[{index}]", min_len=3)
        if "foundational" not in slice_info or not isinstance(slice_info["foundational"], bool):
            raise FlowError(f"{ident}.foundational must be true or false.")
        paths = _slice_files(repo, slice_info, ident)
        for rel in paths:
            for blocked in forbidden_norm:
                if blocked and (rel == blocked or rel.startswith(blocked.rstrip("/") + "/")):
                    raise FlowError(f"Slice {ident} touches `{rel}`, which the plan forbids ({blocked}).")


def validate_test_plan(data: dict, criteria_ids: set[str], allowed: set[str], allow_model: bool) -> None:
    if not allow_model:
        check_model(data, allowed, "test plan")
    else:
        check_model(data, set(FAMILY), "test plan")
    cases = require(data, "cases", list, "test plan")
    if len(cases) < 2:
        raise FlowError("The test plan needs at least two cases, including one failure case.")
    covered: set[str] = set()
    negatives = 0
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise FlowError(f"test plan case {index} must be an object.")
        require_str(case, "name", f"cases[{index}]", min_len=5)
        require_str(case, "expect", f"cases[{index}]", min_len=5)
        covers = require(case, "covers", list, f"cases[{index}]")
        if not covers:
            raise FlowError(f"cases[{index}].covers must list acceptance-criterion ids.")
        for ident in covers:
            if ident not in criteria_ids:
                raise FlowError(f"Case `{case['name']}` covers `{ident}`, which is not an acceptance criterion.")
            covered.add(ident)
        if case.get("negative") is True:
            negatives += 1
    if negatives < 1:
        raise FlowError("The test plan needs at least one case with `negative` set to true.")
    missing = criteria_ids - covered
    if missing:
        raise FlowError(
            "These acceptance criteria have no test: " + ", ".join(sorted(missing)) + "."
        )


def validate_review(data: dict, allowed: set[str], allow_model: bool) -> None:
    if not allow_model:
        check_model(data, allowed, "review")
    else:
        check_model(data, set(FAMILY), "review")
    verdict = require_str(data, "verdict", "review")
    if verdict not in {"approve", "comment", "block"}:
        raise FlowError("review.verdict must be approve, comment, or block.")
    require_str(data, "checked", "review", min_len=20)
    findings = require(data, "findings", list, "review")
    seen: set[str] = set()
    high = 0
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise FlowError(f"review.findings[{index}] must be an object.")
        ident = require_str(finding, "id", f"findings[{index}]")
        if not FINDING_ID.match(ident):
            raise FlowError(f"Finding id `{ident}` must look like F1.")
        if ident in seen:
            raise FlowError(f"Finding id `{ident}` is duplicated.")
        seen.add(ident)
        severity = require_str(finding, "severity", f"findings[{index}]")
        if severity not in {"low", "medium", "high"}:
            raise FlowError(f"{ident}.severity must be low, medium, or high.")
        if severity == "high":
            high += 1
        require_str(finding, "path", f"findings[{index}]")
        _rel_path(finding["path"], ident)
        require_str(finding, "claim", f"findings[{index}]", min_len=10)
        require_str(finding, "evidence", f"findings[{index}]", min_len=8)
    if high and verdict != "block":
        raise FlowError("A high finding means verdict must be block. The model does not get to soften it.")
    if not high and verdict == "block":
        raise FlowError("verdict is block, and there is no high finding. Use comment, or add the finding.")


def scan_secrets(diff_text: str) -> list[dict]:
    hits: list[dict] = []
    for line_no, line in enumerate(diff_text.splitlines(), start=1):
        if not line.startswith("+") or line.startswith("+++"):
            continue
        added = line[1:]
        for pattern in SECRET_PATTERNS:
            match = pattern.search(added)
            if match:
                hits.append(
                    {
                        "line": line_no,
                        "kind": pattern.pattern[:40],
                        "preview": added[:80],
                    }
                )
                break
    return hits

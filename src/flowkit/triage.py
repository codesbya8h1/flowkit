from __future__ import annotations

import re

HIGH_RISK = re.compile(
    r"\b(auth(n|z)?|authentication|authori[sz]ation|permission|payment|billing|"
    r"migration|schema|pii|credential|secret|password|security|privilege|"
    r"gdpr|encryption|token)\b",
    re.IGNORECASE,
)
MEDIUM_RISK = re.compile(
    r"\b(api|database|cache|concurrency|queue|index|transaction|webhook)\b",
    re.IGNORECASE,
)
SKIP_HINT = re.compile(
    r"\b(typo|readme|changelog|comment only|docs only|spelling)\b",
    re.IGNORECASE,
)
CODE_HINT = re.compile(
    r"\b(bug|fix|endpoint|function|component|test|refactor|implement|add|change)\b",
    re.IGNORECASE,
)


def classify(intake: dict, override: str | None = None) -> dict:
    if override is not None and override not in {"skip", "light", "full"}:
        raise ValueError("tier must be skip, light, or full")
    text = " ".join(
        [
            intake.get("title") or "",
            intake.get("body") or "",
            " ".join(intake.get("labels") or []),
            intake.get("issue_type") or "",
        ]
    )
    reasons: list[str] = []
    if HIGH_RISK.search(text):
        risk = "high"
        reasons.append("The request mentions security, money, credentials, or a schema change.")
    elif MEDIUM_RISK.search(text):
        risk = "medium"
        reasons.append("The request touches an API, data store, or concurrent work.")
    else:
        risk = "low"
        reasons.append("No high-risk or integration keywords.")

    hints = intake.get("acceptance_hints") or []
    if override:
        tier = override
        reasons.append(f"Tier set explicitly to {override}.")
    elif risk == "high":
        tier = "full"
        reasons.append("High-risk work uses the full gate set.")
    elif SKIP_HINT.search(text) and risk == "low" and not CODE_HINT.search(intake.get("title") or ""):
        tier = "skip"
        reasons.append("The request reads as a docs or typo edit.")
    elif not hints and len(intake.get("body") or "") > 1800:
        tier = "full"
        reasons.append("Long request with no acceptance criteria. The spec gate stays on.")
    else:
        tier = "light"
        reasons.append("Scoped change. Spec is folded into the plan; the other gates stay.")

    return {"tier": tier, "risk": risk, "reasons": reasons}

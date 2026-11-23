from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from flowkit.errors import FlowError

JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
AC_HEADING = re.compile(r"(?im)^#{1,3}\s*acceptance criteria\s*$|^acceptance criteria\s*:\s*$")


def flatten_adf(node: object) -> str:
    """Turn an Atlassian document into plain text. ADF is a tree of typed nodes."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(flatten_adf(item) for item in node)
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return str(node.get("text") or "")
    if node.get("type") == "hardBreak":
        return "\n"
    inner = "".join(flatten_adf(child) for child in node.get("content") or [])
    kind = node.get("type")
    if kind == "heading":
        level = int((node.get("attrs") or {}).get("level") or 2)
        return "#" * max(1, min(level, 6)) + " " + inner.strip() + "\n"
    if kind == "listItem":
        return "- " + inner.strip() + "\n"
    if kind in {"paragraph", "blockquote", "codeBlock"}:
        return inner.strip() + "\n"
    return inner


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and value.get("type") == "doc":
        return flatten_adf(value)
    if isinstance(value, (dict, list)):
        return flatten_adf(value)
    return str(value)


def acceptance_hints(text: str) -> list[str]:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if AC_HEADING.match(line.strip()):
            start = index + 1
            break
    if start is None:
        return []
    hints: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            break
        if stripped.startswith(("-", "*")) or re.match(r"^\d+[.)]\s+", stripped):
            hints.append(re.sub(r"^(\d+[.)]|[-*])\s+", "", stripped).strip())
    return [hint for hint in hints if hint]


def from_jira_payload(payload: dict, ac_field: str | None = None) -> dict:
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else payload
    key = str(payload.get("key") or payload.get("id") or fields.get("key") or "").strip()
    summary = _as_text(fields.get("summary") or payload.get("summary")).strip()
    description = _as_text(fields.get("description") if "description" in fields else payload.get("description"))
    labels = fields.get("labels") or payload.get("labels") or []
    if not isinstance(labels, list):
        labels = []
    issue_type = ""
    raw_type = fields.get("issuetype") or payload.get("issuetype")
    if isinstance(raw_type, dict):
        issue_type = str(raw_type.get("name") or "")
    elif isinstance(raw_type, str):
        issue_type = raw_type
    status = ""
    raw_status = fields.get("status") or {}
    if isinstance(raw_status, dict):
        status = str(raw_status.get("name") or "")
    extra_ac = ""
    if ac_field and ac_field in fields:
        extra_ac = _as_text(fields.get(ac_field))
    elif ac_field and ac_field in payload:
        extra_ac = _as_text(payload.get(ac_field))
    body = description.strip()
    if extra_ac.strip():
        body = f"{body}\n\nAcceptance criteria:\n{extra_ac.strip()}\n"
    if not summary:
        raise FlowError("The Jira payload has no summary.")
    return {
        "kind": "jira",
        "key": key or None,
        "title": summary,
        "body": body,
        "labels": [str(label) for label in labels],
        "issue_type": issue_type,
        "status": status,
        "acceptance_hints": acceptance_hints(body),
        "url": payload.get("self") or None,
    }


def from_text(text: str, title: str | None = None) -> dict:
    cleaned = text.strip()
    if not cleaned:
        raise FlowError("The request text is empty.")
    lines = cleaned.splitlines()
    inferred = title
    body = cleaned
    if inferred is None and lines[0].startswith("#"):
        inferred = lines[0].lstrip("#").strip()
        body = "\n".join(lines[1:]).strip()
    if not inferred:
        inferred = lines[0].strip()[:120]
    return {
        "kind": "text",
        "key": None,
        "title": inferred,
        "body": body,
        "labels": [],
        "issue_type": "",
        "status": "",
        "acceptance_hints": acceptance_hints(cleaned),
        "url": None,
    }


def from_file(path: Path, ac_field: str | None = None) -> dict:
    if not path.is_file():
        raise FlowError(f"No request file at {path}.")
    raw = path.read_text()
    if path.suffix.lower() == ".json" or raw.lstrip().startswith("{"):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise FlowError(f"{path.name} is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise FlowError(f"{path.name} must contain a JSON object.")
        if "summary" in payload or "fields" in payload:
            record = from_jira_payload(payload, ac_field=ac_field)
            record["kind"] = "jira-file"
            record["origin"] = str(path)
            return record
        if "title" in payload and "body" in payload:
            record = from_text(str(payload["body"]), title=str(payload["title"]))
            record["kind"] = "file"
            record["origin"] = str(path)
            return record
        raise FlowError(
            f"{path.name} needs a Jira issue shape (summary or fields) or a title and a body."
        )
    record = from_text(raw)
    record["kind"] = "file"
    record["origin"] = str(path)
    return record


def fetch_jira(key: str, ac_field: str | None = None, timeout: float = 30) -> dict:
    if not JIRA_KEY.match(key):
        raise FlowError(f"`{key}` is not a Jira issue key like PROJ-123.")
    missing = [name for name in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN") if not os.environ.get(name)]
    if missing:
        joined = ", ".join(missing)
        raise FlowError(
            f"Jira credentials are not set ({joined}). "
            "Export JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN, or pass --jira-file with the issue JSON."
        )
    base = os.environ["JIRA_BASE_URL"].rstrip("/")
    email = os.environ["JIRA_EMAIL"]
    token = os.environ["JIRA_API_TOKEN"]
    url = f"{base}/rest/api/3/issue/{key}?fields=summary,description,labels,issuetype,status"
    if ac_field:
        url += f",{ac_field}"
    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Basic {auth}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise FlowError(f"Jira returned HTTP {exc.code} for {key}.") from exc
    except urllib.error.URLError as exc:
        raise FlowError(f"Could not reach Jira: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise FlowError("Jira returned a response that is not an issue object.")
    record = from_jira_payload(payload, ac_field=ac_field)
    record["key"] = key
    record["url"] = f"{base}/browse/{key}"
    return record

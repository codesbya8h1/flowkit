from pathlib import Path

from flowkit.intake import acceptance_hints, flatten_adf, from_file, from_jira_payload, from_text
from flowkit.triage import classify
from flowkit.validate import scan_secrets


def test_adf_and_acceptance_hints():
    text = flatten_adf(
        {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Refresh twice."}]},
                {"type": "heading", "content": [{"type": "text", "text": "Acceptance criteria"}]},
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": "Second call is free."}]}
                            ],
                        }
                    ],
                },
            ],
        }
    )
    assert "Refresh twice." in text
    assert acceptance_hints(text) == ["Second call is free."]


def test_example_ticket_keeps_its_criteria():
    example = Path(__file__).resolve().parents[1] / "examples" / "billing-ticket.json"
    record = from_file(example)
    assert record["key"] == "BILL-42"
    assert any("original charge" in hint for hint in record["acceptance_hints"])
    assert any("no key" in hint for hint in record["acceptance_hints"])


def test_jira_file_and_plain_text(tmp_path: Path):
    payload = {
        "key": "BILL-42",
        "fields": {
            "summary": "Make billing token refresh idempotent",
            "description": "A second refresh must not charge again.\n\nAcceptance criteria:\n- Same key returns the original charge.\n",
            "labels": ["billing"],
            "issuetype": {"name": "Story"},
        },
    }
    record = from_jira_payload(payload)
    assert record["key"] == "BILL-42"
    assert record["acceptance_hints"] == ["Same key returns the original charge."]
    path = tmp_path / "issue.json"
    path.write_text(__import__("json").dumps(payload))
    loaded = from_file(path)
    assert loaded["kind"] == "jira-file"
    note = from_text("# Export CSV\n\nAdd a download on the reports page.")
    assert note["title"] == "Export CSV"
    assert note["kind"] == "text"


def test_triage_tiers():
    high = classify(
        {
            "title": "Billing token migration",
            "body": "Change the payment schema for stored credentials.",
            "labels": [],
            "issue_type": "Story",
            "acceptance_hints": [],
        }
    )
    assert high["tier"] == "full"
    assert high["risk"] == "high"
    light = classify(
        {
            "title": "Add CSV export",
            "body": "A download button on the reports page.",
            "labels": [],
            "issue_type": "",
            "acceptance_hints": ["The file opens in a spreadsheet."],
        }
    )
    assert light["tier"] == "light"
    skip = classify(
        {
            "title": "Changelog spelling",
            "body": "A spelling fix in the changelog only.",
            "labels": [],
            "issue_type": "",
            "acceptance_hints": [],
        }
    )
    assert skip["tier"] == "skip"
    forced = classify(
        {"title": "Changelog spelling", "body": "typo", "labels": [], "issue_type": "", "acceptance_hints": []},
        override="full",
    )
    assert forced["tier"] == "full"


def test_secret_scan_reads_added_lines_only():
    diff = "\n".join(
        [
            " unchanged",
            "-token = 'old-value-not-counted'",
            "+api_key = 'abcdefghijklmnop'",
            "+AKIAIOSFODNN7EXAMPLE",
        ]
    )
    hits = scan_secrets(diff)
    assert len(hits) == 2

You are writing the implementation plan. Do not edit the repository.

{{model_rule}}

Run: {{run_id}}
Tier: {{tier}}
Risk: {{risk}}
Detected test command: {{test_command}}
Approved spec hash (copy this into spec_hash when the tier is full; omit spec_hash on a light tier that has no spec): {{spec_hash}}

Request:
{{intake}}

Explore:
{{explore}}

Spec, when this run has one:
{{spec}}

Rules for the plan:
- One slice is one commit-sized change with its own verification command.
- `verification` is a shell command, not a sentence. A focused test command is better than the whole suite when the suite is slow.
- `foundational` is true only for a schema, auth, money, or public-contract change. Those slices are written by Opus.
- Four or more files in a slice are written by Sonnet in Claude Code. Fewer, by Composer in Cursor.
- `forbidden_paths` are prefixes the slice must not touch.
- Acceptance-criterion ids match the spec exactly when a spec exists. On a light run, you write the criteria here.

Return one JSON object:

```json
{
  "model": "claude-opus-5-5",
  "spec_hash": "{{spec_hash}}",
  "summary": "the approach in a few sentences",
  "non_goals": ["what you considered and left out"],
  "risk": "low",
  "rollback": "how to undo this if it ships and misbehaves",
  "forbidden_paths": ["migrations/"],
  "acceptance_criteria": [
    {"id": "AC-1", "statement": "observable result, matching the spec"}
  ],
  "slices": [
    {
      "id": "S1",
      "goal": "what this slice leaves true",
      "foundational": false,
      "verification": "{{test_command}}",
      "files": [{"path": "src/app.py", "new": false}]
    }
  ]
}
```

The human edits this if it is wrong, then runs `python -m flowkit accept plan <file>` and `python -m flowkit approve plan --note "..."`.

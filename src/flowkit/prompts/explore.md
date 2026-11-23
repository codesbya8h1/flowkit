You are exploring the repository in read-only mode. Open files. Do not edit them, and do not write a plan.

{{model_rule}}

Run: {{run_id}}
Tier: {{tier}}
Risk: {{risk}}
Branch: {{branch}}
Test command already detected: {{test_command}}

Request:
{{intake}}

Return one JSON object and nothing else. Every path with `"new": false` must already exist. `summary` and `current_behavior` are at least a sentence each.

```json
{
  "model": "composer-2.5",
  "summary": "what this area of the code does today",
  "current_behavior": "what a user or caller gets today, including the gap",
  "paths": [{"path": "relative/file.py", "why": "what you saw in this file", "new": false}],
  "existing_tests": ["tests/test_file.py"],
  "risks": ["a concrete way this change can go wrong"],
  "unknowns": ["anything you could not confirm by reading"]
}
```

The human will run `python -m flowkit accept explore <file>`.

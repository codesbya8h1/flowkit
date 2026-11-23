You are writing the specification. Do not edit the repository. Do not design the patch.

{{model_rule}}

Run: {{run_id}}
Tier: {{tier}}
Risk: {{risk}}

Request:
{{intake}}

What the explore pass actually opened:
{{explore}}

Write acceptance criteria a test can fail. Use ids AC-1, AC-2, and so on. Put at least one item in `non_goals`. If something blocks you, set `blocking` to true and stop; the human has to answer it before the plan exists.

Return one JSON object:

```json
{
  "model": "claude-opus-5-5",
  "problem": "the user-facing or operator-facing problem, in a few sentences",
  "user_visible_outcome": "what is different when this is done",
  "non_goals": ["something nearby that this change will not do"],
  "acceptance_criteria": [
    {"id": "AC-1", "given": "starting state", "when": "action", "then": "observable result"}
  ],
  "open_questions": [{"question": "what you still need", "blocking": false}],
  "risk_notes": ["risk you want the plan to respect"]
}
```

The human reads this and runs `python -m flowkit accept spec <file>`, then `python -m flowkit approve spec --note "..."`.

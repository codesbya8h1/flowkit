You are writing the test plan. You will not implement it, and you will not edit the repository.

{{model_rule}}

Run: {{run_id}}
Tier: {{tier}}

Plan, including the acceptance criteria you must cover:
{{plan}}

Every criterion id appears in some case's `covers` list. At least one case has `"negative": true` (bad input, empty state, repeated call, or a refused action). Name the observable result, not the function you hope gets called.

Return one JSON object:

```json
{
  "model": "gpt-5.6-sol",
  "cases": [
    {
      "name": "saves a second identical request once",
      "covers": ["AC-1"],
      "negative": false,
      "expect": "what an observer sees"
    },
    {
      "name": "rejects a missing field",
      "covers": ["AC-1"],
      "negative": true,
      "expect": "what an observer sees, and that nothing else changes"
    }
  ]
}
```

The human runs `python -m flowkit accept test_plan <file>` and `python -m flowkit approve test_plan --note "..."`.

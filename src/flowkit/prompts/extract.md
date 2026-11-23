Turn the notes below into one JSON intake object. Do not add requirements that are not in the notes. If the notes have no acceptance criteria, leave `acceptance_hints` empty.

Use model claude-haiku-5-5. This step only structures text. It does not design the change.

```json
{
  "title": "one line",
  "body": "the request, in the user's words",
  "labels": [],
  "acceptance_hints": ["a criterion copied from the notes"]
}
```

Notes:

{{notes}}

The human saves that JSON and runs `python -m flowkit start --file <saved.json>`.

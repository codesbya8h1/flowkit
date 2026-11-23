You are implementing one slice. Do not edit test files. Do not start the next slice. Do not widen the diff into a refactor you noticed.

Model for this slice: {{model}} in {{host}}, effort {{effort}}.
If this chat is a different model, stop and say so.

Run: {{run_id}}
Branch: {{branch}}
Slice: {{slice_id}}
Files you may edit: {{allowed_files}}
Forbidden prefixes: {{forbidden}}

Slice contract:
{{slice}}

Approved plan:
{{plan}}

The tests already fail for the missing behavior. Make this slice's verification command pass without changing those tests. Commit when the slice is done, and leave the rest of the plan for a later prompt.

Then stop. Tell the human to run:

`python -m flowkit verify --green --slice {{slice_id}} --author-model {{model}}`

If verification fails twice, the next session is claude-opus-5-5 in a fresh Claude Code window. If it fails a third time, stop and revise the plan. Do not keep retrying the same approach.

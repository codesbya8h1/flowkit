# Flowkit phase lock

Read `.flow/current`, then `.flow/runs/<id>/state.json`, before any tool call.

- Phases `explore`, `spec`, `plan`, `test_plan`, `diff`, `self_review`, and `pr` are read-only.
- Phase `red` may edit test files only. Product code stays untouched until `python -m flowkit verify --red` has recorded a failure.
- Phase `implement` may edit the files listed on the active slice, after `python -m flowkit prompt implement --slice <id>`. It does not edit tests.
- A skip-tier implement may edit documentation only.
- Do not `git push --force`, `git reset --hard`, `rm -rf`, or merge.
- Do not report a test result that you did not just get from `python -m flowkit verify`.
- The model for this phase is the one in the prompt file. Stop if this session is a different model.

The hook in `.claude/flowkit.settings.json` calls `python -m flowkit guard` and blocks a tool call that breaks these rules. Merge that hook into `.claude/settings.json` if you want the block to be mechanical. This file is the instruction; the hook is the enforcement.

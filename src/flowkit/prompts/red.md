You are writing tests for a change that is not implemented yet. Edit test files only. Do not edit product code. Do not stub the function under test so the test passes.

{{model_rule}}

Run: {{run_id}}
Branch: {{branch}}
Test command: {{test_command}}

Test plan:
{{test_plan}}

Plan, for the behavior the tests lock:
{{plan}}

Make the new tests fail because the behavior is absent, not because of a typo or a missing import you could have fixed. Commit the tests on this branch.

Then stop. Tell the human to run:

`python -m flowkit verify --red`

That command must exit non-zero. If it exits zero, the tests do not lock the change and you rewrite them.

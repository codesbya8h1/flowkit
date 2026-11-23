# Agent rules

This repo uses flowkit. Python decides the phase. You do one phase, then stop.

1. Read `.flow/current` and that run's `state.json`.
2. Obey the phase. Only `implement` edits product code, and only the files on the active slice. Only `red` edits tests.
3. Run `python -m flowkit next` and do that. Do not invent a later step.
4. Test results come from `python -m flowkit verify`. A sentence that says the tests passed is not evidence.
5. Do not push unless the human has approved the pull request and runs `python -m flowkit push`.
6. Use the model named in the phase prompt. The reviewer is a different model family from the author.

Human gates, on a full run: spec, plan, test plan, diff, self review, pull request. You do not pass those gates.

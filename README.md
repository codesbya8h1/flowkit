# Flowkit

Flowkit is a coding workflow for Cursor and Claude Code. A Jira ticket, a markdown brief, or a pasted request becomes one run. Python decides the phase and records the evidence. A model does one job per session. A human approves the plan, the diff, and the review before the branch can be pushed.

The run lives in `.flow/runs/<id>/` inside the product repository. Chat history is not the record. If a session is compacted or closed, the next session reads the files.

## The sequence

| Step | Who | Model | Where | What has to be true before the next step |
| --- | --- | --- | --- | --- |
| Intake | Python | none | shell | The request is one JSON record: title, body, hints. |
| Triage | Python | none | shell | Tier is `full`, `light`, or `skip`, from the words in the request. |
| Explore | model, read-only | Composer 2.5 | Cursor Ask | Every cited path exists. The file says what the code does today. |
| Spec | model | Opus 5.5 | Claude Code | Acceptance criteria are testable. Blocking questions are answered. Human approves. Full tier only. |
| Plan | model | Opus 5.5 | Claude Code, fresh session | Slices, file lists, a rollback, and a shell command per slice. Human approves. |
| Test plan | model, other family | GPT-5.6 Sol | a new chat | Every criterion has a case, including one failure case. Human approves. |
| Red | Composer 2.5, then Python | Composer 2.5 | Cursor | New tests fail because the behavior is missing. Python records the exit code and hashes the test files. |
| Implement | model | Composer 2.5, Sonnet 5.5, or Opus 5.5 | Cursor or Claude Code | One slice, only the files named on that slice, tests left untouched. |
| Verify | Python | none | shell | The slice command exits 0. Test-file hashes still match the red snapshot. |
| Diff | human | none | git | The tree is committed. No secret patterns. The human writes what they read. |
| Self review | model, fresh session | the other family | a new chat | Opus reviews Composer and Sonnet. Sol or Grok reviews Opus. High findings block until fixed or waived. |
| Pull request | Python, then human | none | shell | The body is assembled from the artifacts. The human approves. Then the branch may be pushed. |

`python -m flowkit models` prints this table from the same source the prompts use.

### Which model, and why

Judgment under ambiguity (the spec and the plan) is Opus 5.5 at high effort, in Claude Code. Fable 5.2 is allowed on those two phases only when triage marks the request high risk, and only if your organization allows Fable's retention terms. Otherwise stay on Opus.

Exploration and a small edit stay on Composer 2.5 inside Cursor, because the files are already open and the loop is short. A slice with four or more files moves to Sonnet 5.5 in Claude Code. A slice marked `foundational` (schema, auth, money, or a public contract) is written by Opus 5.5.

The test plan is GPT-5.6 Sol, with Grok 4.7 as the alternate. The family that wrote the spec does not also invent the tests.

A messy note can be structured first by Haiku 5.5 (`prompt extract`). Intake itself does not call a model.

The reviewer is chosen after the code exists, from a family that wrote none of it. That is the self review: the workflow reviews its own pull request, and the author is not the reviewer.

### Tiers

Triage sets the tier. Override it with `--tier` when you already know.

- **Full.** Security, money, credentials, a schema change, or a long brief with no acceptance criteria. Spec is its own gate.
- **Light.** A scoped change with a clear outcome. The plan carries the acceptance criteria. Every later gate still runs.
- **Skip.** A changelog, a spelling fix, a comment. Human gates are the diff and the pull request. If the diff contains code, the run is promoted to light and sent back for a plan.

## Run it

From the product repository, on a clean tree:

```bash
python -m flowkit install
python -m flowkit start --jira PROJ-123
python -m flowkit start --jira-file ./issue.json
python -m flowkit start --file ./brief.md
python -m flowkit start --text "Add a CSV download on the reports page"
```

Jira fetch reads `JIRA_BASE_URL`, `JIRA_EMAIL`, and `JIRA_API_TOKEN`. Nothing is printed from the token. If those are unset, save the issue JSON and pass `--jira-file`. `examples/billing-ticket.json` is a complete sample, including Atlassian document markup.

`start` creates a branch named `flow/<slug>-<id>`, records the base commit, and detects a test command from `pyproject.toml`, `package.json`, `go.mod`, or `Cargo.toml`. Pass `--test-command` when detection is wrong.

Then, until the run is done:

```bash
python -m flowkit status
python -m flowkit next
```

`next` prints the single legal command. The state machine rejects anything else.

## Walk a full ticket

Do each model step in a new session, on the model the prompt names. Paste the prompt. Save the JSON the model returns. Come back to the shell.

1. **Explore, read-only, Composer 2.5 in Cursor Ask.**

   ```bash
   python -m flowkit prompt explore
   python -m flowkit accept explore explore.json
   ```

2. **Spec, Opus 5.5 in Claude Code.** Read it. If a question is marked blocking, answer it and accept the spec again. Then approve.

   ```bash
   python -m flowkit prompt spec
   python -m flowkit accept spec spec.json
   python -m flowkit approve spec --note "Confirmed AC-1 with the ticket."
   ```

   `status` prints the spec hash. The plan has to carry that hash.

3. **Plan, Opus 5.5, a fresh Claude Code session that has not edited the repo.**

   ```bash
   python -m flowkit prompt plan
   python -m flowkit accept plan plan.json
   python -m flowkit approve plan --note "One slice, migrations left alone."
   ```

   Check the file list, the forbidden paths, and that each `verification` value is a shell command.

4. **Test plan, GPT-5.6 Sol, a fresh chat.**

   ```bash
   python -m flowkit prompt test_plan
   python -m flowkit accept test_plan test_plan.json
   python -m flowkit approve test_plan --note "The negative case is a missing key."
   ```

5. **Red. Composer 2.5 in Cursor. Tests only.**

   ```bash
   python -m flowkit prompt red
   python -m flowkit verify --red
   ```

   A zero exit stays in this phase. The tests did not lock the new behavior. Commit the failing tests on the flow branch.

6. **Implement one slice.** `prompt implement` names the model and the host, and it locks the file list for the hook.

   ```bash
   python -m flowkit prompt implement --slice S1
   python -m flowkit verify --green --slice S1 --author-model composer-2.5
   ```

   Use the model the prompt names as `--author-model`. A scoped slice is `composer-2.5`. Four or more files is `claude-sonnet-5-5`. Foundational, or a slice that already failed twice, is `claude-opus-5-5`. Commit the slice before the diff gate.

7. **Read the diff yourself.**

   ```bash
   python -m flowkit diff
   python -m flowkit approve diff --note "Read the charge path and the new test."
   ```

8. **Self review, fresh session, the model `prompt self_review` names.**

   ```bash
   python -m flowkit prompt self_review
   python -m flowkit accept self_review review.json
   ```

   A high finding blocks the gate. Fix it with `python -m flowkit fix` (back to implement, then a new diff and a new review), or waive it in writing:

   ```bash
   python -m flowkit waive F1 --reason "Ledger assertion is covered by BILL-43."
   python -m flowkit approve self_review --note "F1 waived against BILL-43."
   ```

9. **Pull request.** The body is built from the artifacts. It is not rewritten by a model.

   ```bash
   python -m flowkit pr
   python -m flowkit approve pr --note "Evidence is in the run, rollback is a revert."
   python -m flowkit push
   ```

Open the pull request with the body in `.flow/runs/<id>/pr.md`. `push` refuses until the pull-request gate is approved.

A light run skips step 2. The plan holds the acceptance criteria. A skip run is `prompt` free: edit the docs, commit, `ready`, approve the diff, then the pull request.

## What you are checking

The note on each approval is stored and copied into the pull request. Eight characters is the minimum so an empty click cannot pass.

- **Spec.** The outcome matches the ticket. Non-goals are real. No blocking question remains.
- **Plan.** The files are the ones you would have touched. The rollback is possible. Nothing in `forbidden_paths` is on a slice.
- **Test plan.** Each criterion can fail a test. One case is a refusal, an empty state, or a repeat.
- **Diff.** You can explain the behavior change. The diff is that change. Secrets and drive-by edits are absent.
- **Review.** You agree with the disposition of every high finding: fixed, or waived with a reason you would say out loud.
- **Pull request.** The body matches the run. You are willing to own the merge.

## Cursor and Claude

`install` writes, and refreshes, these files:

- `.cursor/rules/flowkit.mdc`, applied to every Cursor session in the repo.
- `.claude/FLOW.md`, the same phase lock for Claude Code.
- `CLAUDE.md` and `AGENTS.md` only when the repo does not already have them.
- `.github/pull_request_template.md` only when the repo does not already have one.
- `.claude/flowkit.settings.json`, a PreToolUse hook. Merge it into `.claude/settings.json` so `python -m flowkit guard` can reject a write. Exit code 2 blocks the tool call.

The hook enforces what the prompt asks. During explore, spec, plan, test plan, diff, review, and the pull-request phase, edits are refused. During red, only test paths can be written. During implement, only the active slice's files can be written, and test files are refused. Force-push, `git reset --hard`, `rm -rf`, and `DROP TABLE` are refused in every phase.

Cursor reads the rule file. Claude Code reads `CLAUDE.md`, which points at `AGENTS.md` and `.claude/FLOW.md`. The hook is the mechanical half, and it runs only after you merge the settings snippet.

## When the run stops you

- **Tests passed at red.** The new behavior is not locked. Add a failing test and run `verify --red` again.
- **Green failed once.** Fix it in the same session and re-run the same verify command.
- **Green failed twice.** The next prompt names Opus 5.5 in a new Claude Code session. Do not retry the same session harder.
- **Green failed a third time.** The run is blocked. Revise the plan and accept it. The plan gate opens again, the red snapshot is cleared, and the slices start over.
- **Tests changed after red.** Implementation does not edit tests. `allow-test-edits` records a reason when a fixture truly has to move.
- **Secret pattern in the diff.** Remove it, or `waive-secret` with a reason when the match is a fixture. The waiver is copied into the pull request.
- **Review model is the author's family.** The artifact is rejected. Open the session the prompt names.
- **Skip run touches code.** The tier becomes light and the phase becomes plan.

`accept plan --replace` sends an in-progress plan back through the plan gate before the work is done. A blocked run accepts a new plan without the flag.

## Develop this package

```bash
python3 -m pytest
```

The tests walk a full billing ticket through every gate, reject a same-family review, stop a slice on the third failure, block a secret and a test edit, and promote a skip run that touches code. No network and no model call. The models are named in the artifacts the tests submit, which is the same contract a real session has to meet.

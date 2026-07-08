# Development cycle

AMC uses a local-first review cadence to keep GitHub Actions and remote AI
review focused on changes that actually need them.

## While Editing

Run the smallest deterministic check for the surface you touched. Common
examples:

```bash
.venv/bin/pytest tests/test_server.py -q -k "apply or rollout"
.venv/bin/pytest tests/test_ci_change_classifier.py -q
.venv/bin/ruff check tests/
git diff --check
```

Use focused tests first, then broaden only when the changed boundary warrants
it. Runtime, parser, server, workflow, dependency, and review-tooling changes
should all name the local check that exercised the changed contract.

## Before Pushing For Review

Use the Trellis full-check script instead of manually remembering the review
guard list:

```bash
SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=0 SD_AI_COMMAND_PACK_FULL_CHECK_GITO=0 bash scripts/sd-ai-command-pack-full-check.sh
```

The pack-provided full-check script runs whitespace checks, the shared
review preflight in `scripts/sd-ai-command-pack-review-preflight.mjs`, AMC's
repo-local review preflight in `scripts/check-review-preflight.mjs`, the
copied/generated scope preflight in `scripts/sd-ai-command-pack-review-scope.sh`,
the structural install audit in `scripts/sd-ai-command-pack-install-audit.py`,
current-diff CI classification, configured package scripts when present, and
optional Prism/Gito review. AMC's repo-local review preflight runs the
CI/review cadence guard, the Copilot instruction contract guard, the PR-body
scope guard, and focused review-churn pytest coverage.

When a PR body exists, pass it to local preflight with
`SD_AI_COMMAND_PACK_PR_BODY_SCOPE_PR_BODY` or
`SD_AI_COMMAND_PACK_SCOPE_PR_BODY`, or write it to a file and run
`python scripts/sd-ai-command-pack-pr-body-scope.py --body-file <path>`.
The scope guard reads `.sd-ai-command-pack/pr-body-scope.json` and
fails broad behavior-changing diffs unless the body contains the matching
`Automation scope:`, `CI/review scope:`,
`Tooling/generated scope:`, `Docs/user-facing scope:`, or
`Runtime/server scope:` section. Without a body, it reports detected categories
and exits successfully so the same command remains useful before a PR exists.

Before marking a PR ready, requesting a final remote review, or applying the
`full-ci` label, run the local gate with Prism enabled when practical:

```bash
bash scripts/sd-ai-command-pack-full-check.sh
```

For high-risk runtime changes, also run the full heavy/non-heavy pytest split:

```bash
.venv/bin/pytest -n 0 -m heavy
.venv/bin/pytest -n 2 --dist loadfile -m "not heavy"
```

## GitHub CI Cadence

The workflow uses `scripts/classify-ci-changes.sh` to select one lane while
keeping the stable aggregate check named `test`.

| Lane | Runs When | Purpose |
| --- | --- | --- |
| `lightweight readiness` | Docs, Trellis specs/tasks, agent prompts/skills, Prism rules, or review-tooling scripts only | Catch whitespace, shell syntax, Python syntax, workflow pip, and Trellis artifact hygiene issues without installing the full dev environment. |
| `quick test` | App paths changed on routine PR updates where full CI was not requested | Run install smoke, ruff, review-churn lint tests, and focused server compatibility tests. |
| `test (py3.14)` | App-required diffs when a PR is opened/reopened/ready, the `full-ci` label is applied, auto-merge is armed (the `auto_merge_enabled` event and every later push to the armed PR), workflow/dependency files change, manual dispatch runs, or code lands on `main` | Run the py3.14 test lane (latest-stable-CPython-only policy) and heavy/non-heavy pytest split. |

CodeQL is advisory on PRs and not a required branch-protection context
(`test` and `socket` are the required checks): it analyzes
opened/reopened/ready_for_review PRs and `full-ci`-labeled updates, skips on
plain synchronize events, and always analyzes merged code on the push-to-main
run. A skipped analysis produces no code-scanning summary check, so `CodeQL`
must not be re-added as a required context while this gating is in place.

The `full-ci` label's lifetime is deliberately asymmetric across the
workflows, and the split is pinned in `tools/check_ci_review_contract.py` so it
cannot drift silently. `ci.yml` and `socket.yml` honor the label **one-shot**
— only at the `labeled` event; a later plain `synchronize` drops the cost-gated
full matrix / Socket scan back to the quick lane or skip unless auto-merge is
armed or dependency/workflow files changed. `codeql.yml` honors it
**persistently** — its `synchronize` arm re-reads the label set on every push
while the label is present, so security analysis keeps running for the life of
a flagged PR. Do not "unify" the two by making CodeQL one-shot: CodeQL is
advisory and cheap, and one-shot would cut security coverage.

Socket keeps a visible
PR check, but fast-skips unless dependency/security-relevant files changed or
full CI was requested. Dependabot auto-merge enables GitHub auto-merge for
patch/minor updates, but does not try to approve the PR with `GITHUB_TOKEN`;
this repo's workflow token is not allowed to create PR reviews.

Auto-merge never lands on quick-lane evidence: arming it triggers a
full-matrix run on the current head, and every subsequent push to an armed PR
classifies as full CI (the event payload's `auto_merge` field gates
`synchronize` runs, so no label-ordering race can leave a quick run as the
surviving gate). Pushes to `main` run in per-commit concurrency groups, so a
merge burst cannot cancel a previous merge commit's full-suite backstop run.

`tools/check_ci_review_contract.py` is the local guard for this cadence
contract, `tools/check_copilot_instruction_contract.py` guards the mechanical
Copilot-instruction contract, and `scripts/sd-ai-command-pack-pr-body-scope.py`
guards behavior-changing PR-body scope sections when a PR body is supplied.
They are text-based and stdlib-only so pre-commit, the lightweight CI lane, and
`scripts/sd-ai-command-pack-full-check.sh` can catch drift between workflows, scripts,
instructions, and docs without installing the full project environment.

## Review Economy Rules

- Keep branch protection on `test`, not lane-specific job names.
- Apply `full-ci` after substantial runtime changes, before merge if the last
  remote full matrix is stale, or whenever the quick lane is not enough
  evidence for the risk.
- Arm auto-merge when you open the PR: arming re-gates the current head on the
  full matrix, so arming after a green full lane costs one redundant full run.
- Prefer a local full-check plus one remote final review over repeated
  Copilot/Actions loops.
- If a review comment points to a recurring mechanical pattern, add or update a
  `tools/check_*.py` guard and test rather than relying on prose alone.

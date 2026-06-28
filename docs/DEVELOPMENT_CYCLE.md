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
TRELLIS_FULL_CHECK_LEVEL=quick bash scripts/trellis-full-check.sh
```

The quick level runs whitespace checks, shell syntax for review tooling, Python
syntax, workflow pip lint, Trellis placeholder lint, trace-payload guardrails,
the CI/review cadence contract guard, ruff lockstep, `ruff check tests/`,
console-script smoke coverage, focused review-churn pytest coverage, focused
server compatibility coverage, and optional Prism/Gito review.

Before marking a PR ready, requesting a final remote review, or applying the
`full-ci` label, run the full local gate when practical:

```bash
bash scripts/trellis-full-check.sh
```

The default full level adds the heavy/non-heavy pytest split:

```bash
.venv/bin/pytest -n 0 -m heavy
.venv/bin/pytest -n 2 --dist loadfile -m "not heavy"
```

## GitHub CI Cadence

The workflow uses `scripts/classify_ci_changes.sh` to select one lane while
keeping the stable aggregate check named `test`.

| Lane | Runs When | Purpose |
| --- | --- | --- |
| `lightweight readiness` | Docs, Trellis specs/tasks, agent prompts/skills, Prism rules, or review-tooling scripts only | Catch whitespace, shell syntax, Python syntax, workflow pip, and Trellis placeholder issues without installing the full dev environment. |
| `quick test` | App paths changed on routine PR updates where full CI was not requested | Run install smoke, ruff, review-churn lint tests, and focused server compatibility tests. |
| `test (py3.11/py3.12)` | App-required diffs when a PR is opened/reopened/ready, the `full-ci` label is applied, workflow/dependency files change, manual dispatch runs, or code lands on `main` | Run the full matrix and heavy/non-heavy pytest split. |

CodeQL runs on PR updates because branch protection requires the GitHub
Advanced Security `CodeQL` context on the latest commit. Socket keeps a visible
PR check, but fast-skips unless dependency/security-relevant files changed or
full CI was requested.

`tools/check_ci_review_contract.py` is the local guard for this contract. It is
text-based and stdlib-only so pre-commit, the lightweight CI lane, and
`scripts/trellis-full-check.sh` can catch drift between workflows, scripts, and
docs without installing the full project environment.

## Review Economy Rules

- Keep branch protection on `test`, not lane-specific job names.
- Apply `full-ci` after substantial runtime changes, before merge if the last
  remote full matrix is stale, or whenever the quick lane is not enough
  evidence for the risk.
- Prefer a local full-check plus one remote final review over repeated
  Copilot/Actions loops.
- If a review comment points to a recurring mechanical pattern, add or update a
  `tools/check_*.py` guard and test rather than relying on prose alone.

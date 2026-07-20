# Fix CI workflow selection and guard runtime Design

## Overview

This child makes lane selection and the lightweight guard runtime agree with
the repository's existing auto-merge and Python-version contracts. It groups
the parent design's first PR only.

## Proposal

- Extend the existing event-selection expressions in `.github/workflows/ci.yml`
  rather than introducing a fourth lane or a second source of truth.
- Add contract fixtures around the labeled and manual-dispatch event paths in
  the existing CI-review contract checker/tests.
- Reuse the workflow's existing `uv` setup pattern for lightweight guards and
  run those commands with the declared Python 3.14 floor.
- Extend the classifier's named path predicates conservatively: command-pack
  metadata and Trellis audit artifacts are non-application inputs, while
  dependencies, workflows, and runtime paths continue to force the full lane.
- Keep the two shell-syntax lists in lockstep and expand the existing Python
  syntax glob rather than adding another syntax job.

## Boundaries And Non-Goals

- No redesign of branch protection, required contexts, CodeQL label behavior,
  or cadence.
- No upstream `sd-ai-command-pack` changes.
- No lint-mirror additions; those belong to the next child.

## Affected Files

- `.github/workflows/ci.yml`
- `scripts/classify-ci-changes.sh`
- `tools/check_ci_review_contract.py`
- `tests/test_ci_change_classifier.py`
- `tests/test_ci_review_contract.py`
- `.trellis/audit/ledger.md`
- Focused CI documentation/specs only if the executable contract changes their
  current wording

## Data And Command Contracts

The classifier's existing outputs and aggregate required-check name remain
unchanged. The new paths only change the boolean lane decision for otherwise
documentation/tooling-only diffs. Manual dispatch and armed labeled events
must resolve to the application lane deterministically.

## Risks And Edge Cases

- Workflow-expression changes are easy to invert; fixture-based contract tests
  must exercise both positive and negative event cases.
- Making too broad a path lightweight could skip application validation;
  enumerate exact path prefixes and retain negative tests.
- The lightweight lane should remain fast after `uv` setup; capture its PR-run
  duration for review.

## Validation

Run the focused classifier and CI-contract tests, syntax-check all workflow
YAML through the existing repo gates, run the relevant pre-commit hooks, then
let the workflow-changing PR prove the full matrix live.

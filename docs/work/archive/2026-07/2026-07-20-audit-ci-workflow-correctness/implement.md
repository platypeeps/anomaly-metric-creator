# Fix CI workflow selection and guard runtime Implementation Plan

## Execution Order

1. Add failing CI-contract cases for auto-merge labeled events and manual
   dispatch, then update the workflow expressions.
2. Add failing classifier cases for `.sd-ai-command-pack/**` and
   `.trellis/audit/**`, then extend the narrow path predicates.
3. Pin the lightweight guards to `uv` + Python 3.14.
4. Extend both shell-syntax lists and the existing Python syntax glob, with
   contract assertions where the repository already pins those lists.
5. Update the audit ledger and any now-stale CI documentation/spec text.

## Validation Plan

```bash
.venv/bin/pytest tests/test_ci_change_classifier.py tests/test_ci_review_contract.py -n 0
.venv/bin/pre-commit run --all-files
git diff --check
```

Run the repo-local full/review gate required by `sd-review-pr` before shipping,
then verify the PR's own full matrix and lightweight-job timing.

## Documentation And Spec Updates

Only update durable CI/spec text whose current contract becomes inaccurate.
Keep the ledger's same-PR `fixed` transitions synchronized with code.

## Review Notes

Review event truth tables and classifier negative cases first. Confirm the
required check remains `CI Result` and that auto-merge cannot use quick-lane
evidence.

## Rollback Points

Each numbered implementation step is independently reversible before commit.
If the live workflow selects the wrong lane, revert the workflow expression
and its matching contract fixture together.

## Follow-Ups

Lint/local parity is owned by `07-20-audit-ci-lint-parity`; recurring
automation and Windows collection are owned by
`07-20-audit-ci-automation-portability`.

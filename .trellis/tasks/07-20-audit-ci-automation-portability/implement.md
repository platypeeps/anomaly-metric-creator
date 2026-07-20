# Add CI automation and Windows collection coverage Implementation Plan

## Execution Order

1. Stop for explicit maintainer approval of recurring pack-sync PR creation.
2. Add contract tests and the idempotent scheduled sync workflow.
3. Add the advisory Windows collect-only job and fix narrow collection-time
   portability issues it exposes.
4. Update docs/specs and flip ledger items only after live verification.

## Validation Plan

```bash
.venv/bin/pytest tests/test_ci_review_contract.py -n 0
.venv/bin/pre-commit run --all-files
git diff --check
```

Verify both a no-change sync run and the PR's live Windows collection result.

## Documentation And Spec Updates

Document the schedule, no-change behavior, ownership of generated refresh PRs,
and advisory Windows scope in the existing CI owners.

## Review Notes

Review permissions, idempotency, branch/PR ownership, required-context
dependencies, and Windows shell/path assumptions.

## Rollback Points

The sync and Windows workflows remain separate files/jobs and can be reverted
independently if production behavior differs from the contract tests.

## Follow-Ups

Consider a real Windows test lane only after the advisory collection job has
been stable for a sustained period; that is a separate cost decision.

# Mirror CI lints and local gates Implementation Plan

## Execution Order

1. Create the mypy-gate checker and tests, then replace the inline workflow
   module list and wire local preflight.
2. Add the three existing guard commands to CI and expand role-name scan roots
   with focused fixtures.
3. Run the branch-name checker against `github.head_ref` and add the
   `commit-msg` hook plus installation guidance.
4. Add and test the Node-absent Python fallback in full-check.
5. Update docs/specs and flip the six covered ledger items.

## Validation Plan

```bash
.venv/bin/pytest tests/test_role_name_leaks_lint.py tests/test_branch_name_lint.py -n 0
.venv/bin/pre-commit run --all-files
git diff --check
```

Add the focused tests for the new mypy owner and fallback to the command above
once their final filenames are chosen.

## Documentation And Spec Updates

Document the local mypy invocation, commit-msg installation, and any durable
CI parity rule in the existing development-cycle/Trellis owners.

## Review Notes

Review single-source ownership, subprocess exit behavior, scan-root false
positives, and hook installation compatibility.

## Rollback Points

Keep mypy ownership, lint mirrors, branch hook, and runtime fallback as
separable commits or reviewable hunks so a portability problem can be reverted
without reopening the other audit items.

## Follow-Ups

None beyond the parent task's automation/Windows child.

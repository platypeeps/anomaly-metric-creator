# Mirror CI lints and local gates Implementation Plan

## Execution Order

1. Create the mypy-gate checker and tests, then replace the inline workflow
   module list and wire local preflight.
2. Add the three existing guard commands to CI and expand role-name scan roots
   with focused fixtures.
3. Run the branch-name checker against `github.head_ref`, add a role-name
   `commit-msg` hook, and document installation of both non-default hook
   stages.
4. Update Ruff to 0.15.22 in both pin owners, regenerate `uv.lock`, and run the
   lockstep checker so Dependabot PR #259 is superseded by the cohesive parity
   change.
5. Update docs/specs and flip the five covered ledger items.

## Validation Plan

```bash
.venv/bin/pytest tests/test_role_name_leaks_lint.py tests/test_branch_name_lint.py tests/test_ruff_lockstep_lint.py -n 0
.venv/bin/python tools/check_ruff_lockstep.py
.venv/bin/pre-commit run --all-files
git diff --check
```

Add the focused test for the new mypy owner to the command above once its final
filename is chosen.

## Documentation And Spec Updates

Document the local mypy invocation, commit-msg installation, and any durable
CI parity rule in the existing development-cycle/Trellis owners.

## Review Notes

Review single-source ownership, subprocess exit behavior, scan-root false
positives, and hook installation compatibility.

## Rollback Points

Keep mypy ownership, lint mirrors, and branch/commit hook changes as separable
reviewable hunks so a problem can be reverted without reopening the other
audit items.

## Follow-Ups

- A-050: implement the Node-free Python preflight fallback in the upstream SD
  command pack, then refresh this consumer through the normal installer so
  provenance remains truthful.

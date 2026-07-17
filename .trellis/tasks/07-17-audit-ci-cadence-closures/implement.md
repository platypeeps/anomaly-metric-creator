# Close CI cadence and lint-mirror gaps — Implementation Plan

## Execution Order

PR 1 (lane selection): A-047 labeled-arm fix + contract anchor →
A-051 dispatch `--force-app` → A-053 uv-pinned guards → A-049+addendum
classifier/syntax-gate widening, each with its classifier/contract test.

PR 2 (mirrors/parity): A-048 `tools/check_mypy_gate.py` (move list out of
YAML, wire CI + docs) → A-060 three lint mirrors in guards step → A-052
scan-root extension → A-061 head-ref branch-name check → A-062 commit-msg
stage hook → A-050 full-check node fallback (record chosen posture).

PR 3 (automation, maintainer-confirmed): A-063 scheduled pack-sync
workflow (PR-on-change, auto-merge gate) → A-065 windows-latest
collect-only advisory job; fix any collection-time guard violations it
surfaces in the same PR.

Each PR: flip its covered ledger items → `fixed` (same-PR rule); draft →
pre-PR checklist (the CI/workflow-hygiene heading is the operative one) →
ready → merge. Sequence PRs 1→2→3; no cross-PR dependencies beyond
review focus.

## Validation Plan

```bash
.venv/bin/pytest tests/test_classify_ci_changes.py tests/test_ci_review_contract.py -n 0  # names per existing suite
.venv/bin/pytest tests/test_role_name_leaks_lint.py tests/test_branch_name_lint.py -n 0
bash -n scripts/*.sh && .venv/bin/pre-commit run --all-files
# per-PR: observe the live matrix selection in the PR's own checks tab
```

Mutation checks: labeled-event-on-armed-PR fixture must select full
matrix (PR 1); dropping a module from the mypy-gate list must fail
`check_mypy_gate.py`'s own test (PR 2).

## Documentation And Spec Updates

- CLAUDE.md CI section: labeled-arm rule, mypy-gate tool, new mirrors,
  commit-msg stage install note, pack-sync workflow, Windows lane.
- docs/DEVELOPMENT_CYCLE.md: local `check_mypy_gate.py` preflight.

## Review Notes

- Workflow diffs auto-select the full matrix — each PR self-proves.
- Keep the lightweight lane's runtime budget in the PR description
  (before/after seconds) for A-053/A-060.

## Follow-Ups

- If the Windows collect-only lane stays quiet for a quarter, consider a
  real Windows test lane (new task; cost decision).

---
title: Neutralize formula triggers in the debug UI client-side CSV download
status: done
created: 2026-08-15
branch: fix/debug-ui-csv-formula-neutralization
---
# Neutralize formula triggers in the debug UI client-side CSV download

## Goal

The debug UI builds its own CSV client-side and carries no formula-injection
guard, so the hole A-018 closed on the `amc trace-bundle export-csv` path is
still open on the sibling surface a workshop operator is most likely to use.

## Context

Filed as the pre-registered follow-up from `07-17-audit-trace-export-hardening`
(ledger A-018). That task's `design.md` scoped the server-side writer only and
recorded the non-goal explicitly: "the UI's own CSV exports are client-side —
check whether the debug UI builds CSV in JS, and if so file it as a follow-up
rather than widening this PR." The check found one.

## Measured baseline

- `src/anomaly_metric_creator/server_debug_ui.py` — `csvCell(value)` quotes for
  `"`, `,`, and newline only; no formula-trigger handling.
- `downloadCSV(filename, rows, columns)` is the only consumer, and
  `exportUnsupportedCsv` (`amc-unsupported-backlog.csv`) is its only call site.
- The exported rows carry recorded command text, which is attacker-influenced:
  whoever reaches the simulator chooses what gets recorded.

## Requirements

- `csvCell` apostrophe-prefixes any value whose first character is `=`, `+`,
  `-`, `@`, tab, or CR — the same OWASP trigger set the Python writer uses.
- The guard applies to every column, matching the server-side posture: no
  per-column allowlist.
- Neutralization is idempotent and composes with the existing quoting rather
  than replacing it.
- The `csvCell` guard and `_neutralize_csv_cell` in `trace_bundle.py` are a
  lockstep pair. Decide and record how they stay in step — a comment naming the
  other site is the minimum; a mechanical `tools/check_*.py` lint is preferred
  per the repo's greppable-pattern rule.

## Acceptance criteria

- [x] A recorded command beginning with a trigger character downloads as inert
      text from the debug UI's Unsupported CSV button.
      `test_debug_ui_csv_cell_neutralizes_formula_triggers` runs the payload
      `<trigger>cmd|' /C calc'!A0` for every trigger through the *served*
      `csvCell` under node and asserts each renders apostrophe-first;
      `downloadCSV` routes every header and body cell through `csvCell`, so
      the button has no unguarded path. Not driven through a real browser —
      the function the button's code path calls is what is exercised.
- [x] Test coverage for the JS guard, in whatever form the debug-UI surface
      supports (the repo has no JS test runner today — resolving that is part of
      the design, and a Python-side assertion over the served script is an
      acceptable fallback). `tests/test_debug_ui_javascript.py` slices `csvCell`
      out of the served script and evaluates it under node, with
      `test_debug_ui_csv_cell_guard_is_present_without_node` as the
      node-independent floor.
- [x] The trigger set is not duplicated silently: either a lint or an explicit
      cross-reference comment ties it to `trace_bundle._CSV_FORMULA_TRIGGERS`.
      Both: reciprocal marker comments at each site, and
      `tools/check_csv_formula_trigger_lockstep.py` compares the two
      mechanically in pre-commit and CI.
- [x] `SECURITY.md` drops the "does not yet carry this guard" caveat added by
      the A-018 PR. `git grep -n "does not yet carry this guard"` returns no
      hits outside this task's own planning artifacts.

## Notes

Small and well-bounded, but it needs a design pass on the testing question
above, so it is not PRD-only.

# Split validate_impl.py and take validator cleanups

## Review context

- **Source:** deep-dive architecture + generator-code review, 2026-07-06.
- **Confidence:** CONFIRMED.
- **Severity:** MEDIUM — the decomposition epic's own "<800 lines per new
  module" acceptance criterion was silently waived on its second-largest
  output; plus three concrete cleanups found in the module.
- **Category:** architecture / code quality. Child of
  `07-02-legacy-monolith-decomposition` (the invariant it restores).

## Goal

Resolve the epic-invariant breach on `validate_impl.py` (1,684 lines) —
split it or record an explicit waiver — and take the review's validator
cleanups in the same pass.

## Problem (verified 2026-07-06)

- Step 6 of the decomposition shipped `validate_impl.py` at 2.1× the
  800-line cap the epic's acceptance criteria require; the deviation is
  now recorded in the epic's design.md Invariants note but has no fix.
- The minimum-aligned-rows threshold `100` appears as a bare literal in
  four places ([validate_impl.py:1255](src/anomaly_metric_creator/validate_impl.py:1255),
  :1315, :1535, :1546) — one drifting edit silently desynchronizes the
  aggregate vs per-instance coupling checks.
- Dead helper `_apply_anomaly_exclusion`
  ([validate_impl.py:1043](src/anomaly_metric_creator/validate_impl.py:1043)-1059):
  zero callers in src/ and tests/ (only the legacy re-import stub); its
  own docstring points callers at `_compute_anomaly_keep_mask`, which is
  what both real call sites use.
- Each component CSV is fully re-parsed 4-6× across the check families
  (:386-392, :444-472, :508-607, :653-709, plus uncached aggregate
  topology reads at :1166/:1238 — the per-instance path already caches at
  :1457-1472). Measurable cost on GB-scale 7-day/N=3 artifacts.

## Requirements

- Split by check family (candidates: schema-document checks, artifact
  checks, topology-coupling checks — pick the cohesive cut in a short
  design note), or record an explicit waiver in the epic design.md and
  CLAUDE.md if splitting harms cohesion. Any split must preserve the
  `_configure_validate_runtime` callback wiring and the
  `legacy.validate_output` / `schema.py` facade surface.
- Hoist the literal to `_TOPOLOGY_MIN_ALIGNED_ROWS` beside the two
  existing threshold constants (validate_impl.py:28/:35).
- Delete `_apply_anomaly_exclusion` and its legacy re-import stub.
- Optional, measure first: single-pass cell walk for
  row-count/coverage/cells/derivations; extend the aggregate-path column
  cache to match the per-instance path.

## Acceptance Criteria

- [ ] Either every validator module is < 800 lines, or the waiver is
      recorded in the epic design.md + CLAUDE.md.
- [ ] `validate` subcommand behavior identical
      (`tests/test_validate_output.py` green without behavioral edits).
- [ ] No bare `100` threshold literals remain in the coupling validators.
- [ ] Dead helper removed; facade identity tests stay green.

## Notes

- Linked as a child of the decomposition epic so the epic cannot close
  with its own acceptance criterion silently unmet.

## Added by 2026-07-17 audit (ledger items A-011, A-068)

Two validator-posture findings from the 2026-07-17 repo audit
(`.trellis/audit/ledger.md`) fold into this task's cleanup scope:

- **A-011 (P3·M)** — `validate_output` returns bare prose strings; tests and
  any machine consumer substring-parse sentences (38 `in`-assertions in
  `tests/test_validate_output.py`). Introduce a frozen
  `Violation(component, metric, kind, message)` whose `__str__` reproduces
  today's prose byte-for-byte; CLI output unchanged; migrate tests to field
  assertions incrementally.
- **A-068 (P3·S)** — `_validate_no_unknown_files` hard-fails on foreign files
  (`.DS_Store`, CI sidecars) that generation's pre-clean deliberately
  tolerates. Exempt dotfiles / document a sidecar pattern, or downgrade
  unknown-file to a warning in default mode; document the chosen posture next
  to the pre-clean tolerance note.

Closing this task should flip A-011 and A-068 to `fixed` in the audit ledger.

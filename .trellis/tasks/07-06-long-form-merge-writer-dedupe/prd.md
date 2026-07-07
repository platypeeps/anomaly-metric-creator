# Deduplicate the long-form merge writer into csv_layout

## Review context

- **Source:** deep-dive generator-code review, 2026-07-06.
- **Confidence:** CONFIRMED.
- **Severity:** MEDIUM — ~55 near-verbatim duplicated lines on a locked
  output contract; a tie-break or dimension change must be fixed twice.
- **Category:** DRY / maintainability.

## Goal

Extract the duplicated long-form heapq-merge writer shared by
`write_gauges_csv` and `_write_combined_long_form` into `csv_layout.py`,
guarded by the locked golden hashes.

## Problem (verified 2026-07-06)

[gauges_impl.py:141](src/anomaly_metric_creator/gauges_impl.py:141)-190 and
[combine_impl.py:400](src/anomaly_metric_creator/combine_impl.py:400)-450
duplicate: the `_tagged` source-building closure, the
`_ensure_long_form_fd_capacity` preflight, the
`(component, instance_dims)` sort, the `heapq.merge` loop, the
byte-identical 10-column header tuple (gauges_impl.py:180 ==
combine_impl.py:440), and the row emission (:188 == :448). Only the
missing-file guard differs.

## Requirements

- One shared writer in `csv_layout.py` parameterized by the small real
  differences; both call sites collapse to thin wrappers.
- Golden hashes are the gate: 4-column and 10-column gauges hashes, wide
  and long combine hashes, at 1d and 7d, all unchanged.
- Keep the intentional asymmetry documented in CLAUDE.md (the file
  writers pass raw cell strings verbatim; the OTEL streamer
  float-coerces) — this dedupe covers the two *file* writers only.
- Update the CLAUDE.md gauges/combine sections to name the shared home.

## Acceptance Criteria

- [ ] Single merge implementation; grep shows no duplicated long-form
      header tuple outside `csv_layout.py`.
- [ ] All locked gauge/combine SHA-256 hashes unchanged (full-ci run).
- [ ] CLAUDE.md updated in the same PR.

## Notes

- Natural to schedule after (or alongside) decomposition step 9/10 churn
  settles; independent of them in code terms.

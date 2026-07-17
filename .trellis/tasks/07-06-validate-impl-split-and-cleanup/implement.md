# validate_impl split + validator cleanups — Implementation Plan

## Execution Order

1. Branch from `main`. Trivial cleanups: `_TOPOLOGY_MIN_ALIGNED_ROWS`
   hoist (4 sites); delete `_apply_anomaly_exclusion` + legacy stub;
   suite check.
2. A-068: dotfile exemption in `_validate_no_unknown_files` + tests
   (`.DS_Store` passes; `stray.csv` still fails; `*.tmp` behavior
   unchanged); CLAUDE.md tolerance note.
3. A-011: introduce `Violation` (move the exact format strings into
   `__str__`); flip `validate_output`'s return; mechanical test wraps;
   assert CLI stdout/stderr bytes unchanged via the existing subprocess
   tests.
4. The three-file split (leaves take arguments; seam stays in
   `validate_impl`); `wc -l` cap evidence; facade identity tests.
5. Optional perf: 5-minute timing of the aggregate column cache
   extension; apply only if it shows on the 1-day dataset.
6. Update the epic design.md Invariants note (deviation resolved); flip
   A-011 + A-068 → `fixed` in the ledger; CLAUDE.md module map.
7. Draft PR (`full-ci`) → checklist → ready → merge. (Two PRs —
   cleanups+A-011/A-068, then split — if the single diff reviews
   poorly; the ordering above already supports the cut.)

## Validation Plan

```bash
.venv/bin/pytest tests/test_validate_output.py -n 0
.venv/bin/pytest tests/test_package_facades.py tests/test_schema_file.py -n 0
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
wc -l src/anomaly_metric_creator/validate_*.py
```

## Documentation And Spec Updates

- CLAUDE.md validator section (three-module layout, Violation type,
  dotfile tolerance); epic design.md deviation note; spec index.

## Review Notes

- The byte-fidelity claim (CLI output unchanged) and the
  argument-passing one-way rule are the two review anchors; show the
  moved-not-retyped format strings.

## Follow-Ups

- Field-based test assertions (incremental migration off substring
  checks) — opportunistic, recorded here.
- Single-pass cell walk — only if validator runtime becomes a real
  complaint on GB artifacts.

# Write generated artifacts atomically to end the regeneration read race

## Audit context

- **Source:** first-time staff-engineer audit, 2026-07-02.
- **Confidence:** CONFIRMED (read end to end).
- **Severity:** MEDIUM — data integrity in the server's designed operating mode.
- **Category:** correctness / concurrency.

## Goal

Make regenerated on-disk artifacts observable atomically so an HTTP reader
thread can never see a truncated, half-written, or momentarily-deleted file
while `--continuous-generate` is rewriting the output directory.

## Problem (concrete failure scenario)

`_run_continuous_generation_once` at
[server.py:1377](src/anomaly_metric_creator/server.py:1377) calls
`state.legacy.main(run_argv)` **outside** `state.generation.lock` (the lock is
held only at [server.py:1368](src/anomaly_metric_creator/server.py:1368) to bump
counters, then released before generation runs). `main()` runs
`_pre_clean_output_dir()` — which **deletes** stale files — and then rewrites
each per-component CSV, `anomalies.csv`, `metric_report.log`, etc. **in place**
(confirmed: no `os.replace`/temp-rename anywhere in `legacy.py`).

Concurrently, request-handler threads read those same files with no
coordination:

- `_send_log_file` at [server.py:882](src/anomaly_metric_creator/server.py:882)
  (serving `GET /v1/logs/stream`) checks `log_path.exists()` then opens and
  streams `metric_report.log` line by line.
- `/v1/anomalies`, `/v1/debug/resources`, and the OTEL gauge path read
  per-component CSVs from `output_dir`.

**When** a client polls `/v1/logs/stream` (or reads a component CSV) during a
regeneration cycle, **it observes** a partial/truncated file, or a transient
"metric_report.log is not present for this run" because the file was mid-delete.
The in-memory rows swap is already atomic
(`state.replace_generated_rows(rows)`, [server.py:1379](src/anomaly_metric_creator/server.py:1379)) —
only the on-disk artifacts are not.

## Requirements

- Add an atomic-write helper (write to a sibling `*.tmp` in the same directory,
  `flush` + `os.fsync`, then `os.replace` onto the final path) and route every
  generated artifact write in `legacy.py` through it: per-component CSVs,
  `anomalies.csv`, `metric_report.log`, `metric_traces.jsonl`, `gauges.csv`,
  `combined_metrics_unified.csv`, `schema.json`.
- `os.replace` must target the same filesystem (write the temp inside
  `output_dir`, not the system temp dir) so the rename is atomic on POSIX and
  Windows.
- Preserve the **byte-identical output contract**: file *content* must not
  change — the locked SHA-256 golden hashes across the suite must still pass.
  This is a write-mechanism change only.
- Reconcile `_pre_clean_output_dir()` with this model: a deleted-then-recreated
  file still has a visible gap. Prefer atomic content replacement over
  delete+recreate for files this run will regenerate; keep true deletion only
  for files this run will genuinely not emit.
- Coordinate the log-stream reader: `_send_log_file` should read under
  `state.generation.lock` or operate on an atomically-swapped path so it never
  opens a file mid-rewrite. Keep the SSE loop bounded as it is today.
- Verify the combine/gauge writers (which stream inputs through `heapq.merge`
  and hold input handles) tolerate atomic replacement of their inputs — a
  regeneration must not swap a CSV a combine pass is mid-read on. Document the
  ordering guarantee.

## Acceptance criteria

- [ ] A helper (e.g. `_atomic_write_text` / `_atomic_write_bytes`) is the single
      write path for all generated artifacts; no generator writes a final path
      directly via `open(path, "w")`.
- [ ] All locked SHA-256 golden-hash tests (default, N=3, 7-day, gauges,
      schema, combine) pass unchanged — output bytes are identical.
- [ ] A focused test drives a `--continuous-generate` cycle while concurrently
      reading `metric_report.log` / a component CSV and asserts every observed
      read is a complete previous-or-next file, never a truncation.
- [ ] `_send_log_file` cannot observe a mid-delete `metric_report.log` (by test
      or by construction under the lock).
- [ ] CLAUDE.md "Output directory hygiene" is updated to describe the
      atomic-write contract.

## Notes

- **Recommended first fix** from the audit: low effort, low risk, removes a
  whole class of read/write races.
- Use `os.replace` (atomic, overwrites) — not `os.rename` (may fail if target
  exists on Windows).
- Sequence with `07-02-structured-logging-in-generator` (touches the same
  writers' surrounding code) to avoid churn.

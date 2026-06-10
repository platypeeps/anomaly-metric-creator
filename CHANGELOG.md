# Changelog

## Unreleased

### Fixed

- Fixed a manifest-coherence bug under `--drop-rate > 0`: a shaped span
  anomaly whose first row was dropped wrote its surviving rows into the
  component CSV but recorded no `anomalies.csv` entry. The manifest entry is
  now anchored at the span's first kept row (a span dropped in its entirety
  still records none).
- Fixed `--validate-output` handling of non-finite cells: a NaN/±inf cell in
  a `dtype="int"` column crashed the validator with an uncaught
  `ValueError`/`OverflowError` from `round()`, and a NaN cell in a float
  column (or a NaN flowing through a derivation recomputation) passed every
  range and tolerance check silently. Both now report `non_finite`
  violations.
- Fixed `tools/check_approval_duplicate.py` production mode on PRs with more
  than 100 comments: `gh api --paginate` concatenates JSON arrays
  back-to-back, which the single-document parse rejected with `Extra data`,
  exiting 2 on every long-thread invocation. Pages are now decoded
  individually and flattened.

### Added

- Added a `gpu_inference` component whose default CSV columns match the
  reference observability telemetry shape: batch/model size, GPU memory
  pressure, KV cache usage, memory fragmentation, utilization, throughput,
  p50/p99 latency, and failure label.
- Added the `gpu_inference_fragmentation` scenario to model the reference
  telemetry's sparse incident field: 1,204 failure rows in the default
  50,000-row shape, mostly singleton runs, with imperfect but detectable
  correlated lift from fragmentation, pressure, utilization, throughput, p99
  latency, and KV cache occupancy.
- Added `--otel-gauges-only` to stream only OTLP Gauge metric payloads to
  `--otel-metrics-endpoint`, skipping the anomaly counter, log, and trace OTEL
  signal stream.
- Added HTTP failure diagnostics to `otel-activity.log` `RETRY` and `FAIL`
  records, including response headers, `cf_ray` when present, and the original
  JSON request body for JSON OTEL requests.

### Changed

- The raw `request_body` diagnostic in `otel-activity.log` `RETRY`/`FAIL`
  records is now emitted only under `--otel-verbose`, matching the
  documented verbose contract. Non-verbose error records keep the always-on
  `response_headers` (redacted) and `cf_ray` diagnostics.
- Changed the CLI defaults to match the reference CSV shape: 50,000 rows at a
  60-second interval, using a fractional default `--duration-days` value of
  about 34.72 days and a default `--drop-rate 0`.
- Changed `gpu_inference_fragmentation` to span the full default window and
  drive GPU serving metrics from a shared stress signal plus deterministic
  incident core, so fragmentation, memory pressure, KV cache occupancy,
  utilization, throughput, tail latency, and sparse failures cross bad
  thresholds together as coherent rolling-window degradation instead of only
  independent marginal lifts.
- Changed gradual cache, database, and LLM capacity scenarios to use correlated
  span-stress generators across related metrics, giving analyzers coherent
  multivariate degradation windows instead of isolated single-row anchors.
- Changed older same-day point scenarios and partial-outage scenarios to emit
  short detector-visible primary spans, so rolling-window and compound-signal
  analysis can distinguish sustained incidents from isolated single-row
  breadcrumbs.
- Kept OTEL HTTP failure stderr output compact while moving the larger
  response-header and payload diagnostics into the activity log.
- In gauge-only mode, the gauge streamer starts a fresh activity log instead
  of appending to stale records from a prior signal-stream run.

### Tests

- Added parser and end-to-end coverage for `--otel-gauges-only`.
- Added HTTP failure coverage for anomaly metrics and gauge metrics to verify
  Cloudflare IDs, response headers, and JSON request payloads are logged only
  in the activity log.
- Added scenario-registry coverage that requires each catalog scenario to keep
  at least one detector-visible primary span, and re-locked generated CSV,
  gauge, combine, schema, and multi-instance hashes after the span changes.

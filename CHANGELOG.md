# Changelog

## Unreleased

### Fixed

- Fixed a latent saturation-composition bug: a metric listed in both the
  latency-family and error-family tuples of a `_TOPOLOGY_SATURATION_TARGETS`
  entry would lose its latency multiplier (the error pass rebuilt the spec
  from the pristine list). No v1 registry entry overlaps, so shipped output
  is unchanged; the fix keeps the shared path aligned with the per-instance
  path for the first entry that does.
- `tools/check_role_name_leaks.py` now exits 2 with a diagnostic when a path
  argument does not exist, so a typo'd body filename in the documented
  `&& gh pr comment` pre-flight chain blocks the post instead of exiting 0
  and letting an unchecked body through.

### Changed

- Topology metric registries (`_TOPOLOGY_LOAD_METRICS`,
  `_TOPOLOGY_SATURATION_TARGETS`) are now validated at import time: unknown
  components, typo'd metric names, and edges whose source/target lacks a
  required registry entry raise a clear `ValueError` instead of silently
  generating decoupled output.
- `generate_component()` skips the fixed-3 CSV string formatting (historically
  ~80% of generation runtime) when the run's `--emit-selection` omits
  `metrics`; CSV bytes for runs that do emit metrics are unchanged.

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

- Consolidated eleven per-file `_sha256` helpers into a single streaming
  `conftest.sha256_path` (two copies read whole multi-hundred-MB CSVs into
  RAM via `read_bytes()`), and added non-emptiness guards to four
  registry-derived `expected` assertions that could otherwise pass
  vacuously if a catalog change emptied the filter.
- Added regression tests for the topology-registry import validation, the
  saturation overlap-target composition, the emit-selection formatting
  skip, and the role-name lint's nonexistent-path exit code.
- Added parser and end-to-end coverage for `--otel-gauges-only`.
- Added HTTP failure coverage for anomaly metrics and gauge metrics to verify
  Cloudflare IDs, response headers, and JSON request payloads are logged only
  in the activity log.
- Added scenario-registry coverage that requires each catalog scenario to keep
  at least one detector-visible primary span, and re-locked generated CSV,
  gauge, combine, schema, and multi-instance hashes after the span changes.

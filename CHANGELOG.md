# Changelog

## Unreleased

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
  JSON request body for JSON OTEL requests. Sensitive response-header values
  are masked before reaching disk — see the Security section below. (#90)

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
- Improved the missing-dependency guidance printed when optional imports
  (numpy, PyYAML, protobuf) are unavailable. (#85)

### Security

- `otel-activity.log` HTTP-error `RETRY`/`FAIL` records now redact sensitive
  response-header values before they reach disk: `Authorization` /
  `Proxy-Authorization` keep their scheme prefix with the credential masked
  (`Bearer ***`), and `Cookie` / `Set-Cookie` / `X-Api-Key` are masked in
  full. An intermediary that echoed those headers on a 4xx/5xx previously
  leaked credential material into the on-disk log. (#90)

### Tooling

- Added `tools/check_role_name_leaks.py` (pre-commit hook + stdin pre-flight
  for `gh` comment bodies) to block internal role names from reaching
  external PR threads. (#89)
- Added `tools/check_branch_name.py` as a pre-push hook rejecting `ver-NNN`
  ticket literals in branch names (install with
  `pre-commit install --hook-type pre-push`). (#91)
- Added `tools/check_approval_duplicate.py` to gate duplicate
  `APPROVED`-shaped PR comments by `(author, commit OID)` and refuse
  self-correction bodies that should be in-place edits. (#92)

### Docs

- Added `.github/PULL_REQUEST_TEMPLATE.md` prefilling the 13 pre-PR
  checklist headings from CLAUDE.md. (#93)
- Added Copilot path-specific instructions pointing at CLAUDE.md and synced
  checklist headings. (#87, #88)
- Refreshed the application-flow and topology mermaid diagrams. (#86)

### Tests

- Added parser and end-to-end coverage for `--otel-gauges-only`.
- Added HTTP failure coverage for anomaly metrics and gauge metrics to verify
  Cloudflare IDs, response headers, and JSON request payloads are logged only
  in the activity log.
- Added scenario-registry coverage that requires each catalog scenario to keep
  at least one detector-visible primary span, and re-locked generated CSV,
  gauge, combine, schema, and multi-instance hashes after the span changes.

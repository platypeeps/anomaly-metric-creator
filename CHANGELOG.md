# Changelog

## Unreleased

### Added

- Consolidated the CLI around the common use cases (41 flat flags -> ~18
  visible, grouped):
  - `generate` / `combine DIR` / `validate DIR [--warn]` subcommands
    (`generate` is the default, so every historic bare invocation is
    unchanged; the subcommands replace `--combine-only` and
    `--validate-output [--validate-warn]`).
  - `--emit ARTIFACTS` replaces `--emit-selection` + `--combine`, adding a
    `combined` token for the unified-CSV join.
  - `--otel-send SIGNALS` (subset of logs/metrics/traces/gauges, or
    `all`/`none`) replaces the five OTEL toggles; `--otel-send gauges`
    alone is the old gauges-only mode and the selection is authoritative
    over env-var endpoint defaults.
  - `--otel-endpoint BASE` + `--otel-auth-token` replace the per-signal
    endpoint/token sextet, deriving `BASE/v1/<signal>` URLs for the
    selected signals (explicit per-signal flags beat the derivation,
    which beats the per-signal env vars; the env vars supply defaults
    when no base is given).
  - Two-tier help: `-h` shows the grouped common surface; `--help-all`
    additionally lists the advanced knobs and the deprecated aliases,
    each annotated with its canonical replacement.

### Deprecated

- All 16 replaced flag spellings (`--emit-selection`, `--combine`,
  `--combine-only`, `--validate-output`, `--validate-warn`, the five OTEL
  toggles, and the six per-signal endpoint/token flags) keep working but
  emit one `DEPRECATION:` stderr line each; mixing a canonical flag with
  the aliases it replaces is a parse error. Removal is scheduled for a
  post-phase-9 CLI flag day.

## 0.2.0 - 2026-06-10

First cut release. Everything below shipped between the initial 0.1.0
state and this tag — including the full-codebase-review program
(PRs #94-#99: four verified bug fixes, test-isolation and fixture-cost
work, a documentation-drift sweep, structural hardening of the topology
registries and generator dispatch, latent unit-mixing fixes, and the
validator/merge-writer performance pass).

### Changed

- Validator and merge-writer hot paths trimmed: `_validate_component_cells`
  hoists the per-column schema constants out of the row loop (previously ~4
  dict lookups per cell), `_validate_component_derivations` stops scanning
  once every derived metric has recorded its one-per-file violation, the
  per-instance topology-coupling check caches the long-form column reads
  per validation run (one parse per (component, metric) instead of one per
  edge) and hoists the loop-invariant anomaly-window filter out of the
  per-pod loop, and `_parse_csv_timestamp` gains a small LRU so the
  `heapq.merge` writers stop re-parsing the shared timestamp grid once per
  source.
- `stream_otel_gauges` sorts its component iterators internally (matching
  `write_gauges_csv`) so the equal-timestamp tie-break holds for direct
  callers; the live path already passed a sorted mapping.
- `metric_report.log` escapes embedded double quotes in the `msg="…"`
  field so the key=value line stays parseable if a future scenario
  description carries one (current descriptions are quote-free, so emitted
  bytes are unchanged).

### Fixed

- Fixed two latent unit-mixing paths in the topology composers: the
  saturation logistic is now driven only by the upstream's *canonical* load
  metric (it used to fall back to the first supplementary column, whose
  units `midpoint` was never tuned for), and a callable-weight edge's
  contribution — which is in canonical-metric units — is now applied only to
  the canonical load metric instead of every coupled metric. Shipped output
  is unchanged (no v1 registry shape reaches either path); the callable
  evaluation is also hoisted out of the per-metric loop.
- Fixed the uninspectable-generator dispatch fallback to retry only on a
  call-*binding* `TypeError`: a `TypeError` raised inside the generator body
  now propagates instead of being masked by a second call with fewer
  arguments (which could also double-advance the RNG stream).
- The topology-coupling zero-variance violation now names exactly the
  constant side(s) instead of the ambiguous "source or target" form.
- `parse_args` now validates the OTEL stream scalars (`--otel-stream-speedup`
  etc.) unconditionally rather than only when an endpoint is configured, and
  rejects `--seed` values outside numpy's `[0, 2**32)` range with a clean
  usage error instead of a later raw traceback.

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

- `_validate_derivations_registry` now enforces the
  `MetricSpec.derivation` <-> `DERIVATIONS` consistency in both directions
  at import time (a declared-but-unregistered derivation used to surface
  only as a runtime `KeyError` at `--validate-output` time).
- All text-mode artifact writers (per-component CSVs, `anomalies.csv`,
  `metric_report.log`, `metric_traces.jsonl`, the combine reader/writer)
  now pin `encoding="utf-8"`, matching the readers, so a non-UTF-8 locale
  cannot produce artifacts the rest of the pipeline mis-decodes.

- The raw `request_body` diagnostic in `otel-activity.log` `RETRY`/`FAIL`
  records is now emitted only under `--otel-verbose`, matching the
  documented verbose contract. Non-verbose error records keep the always-on
  `response_headers` (redacted) and `cf_ray` diagnostics.
- Topology metric registries (`_TOPOLOGY_LOAD_METRICS`,
  `_TOPOLOGY_SATURATION_TARGETS`) are now validated at import time: unknown
  components, typo'd metric names, and edges whose source/target lacks a
  required registry entry raise a clear `ValueError` instead of silently
  generating decoupled output.
- `generate_component()` skips the fixed-3 CSV string formatting (historically
  ~80% of generation runtime) when the run's `--emit-selection` omits
  `metrics`; CSV bytes for runs that do emit metrics are unchanged.
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

- Renamed the module-load lint's exemption marker from `# noqa: amc-load`
  to `# amc-load: allow`, matching the sibling `# role-name-lint: allow`
  convention: the marker is consumed by `tools/check_amc_module_load.py`,
  not by ruff, and the `noqa:` spelling made ruff warn
  "invalid `# noqa` directive" on every annotated line.
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

- Routed the four remaining hand-rolled `main()` drivers
  (`test_correctness.py`'s interval-5 fixture, `test_scenario_deviation.py`'s
  `_run_scenario`, `test_instance_config.py`'s `_run`, and `test_shapes.py`'s
  inline DST run) through the canonical `conftest.run_capture`, which now
  scopes its stderr capture with `contextlib.redirect_stderr` instead of a
  global `sys.stderr` swap.
- Replaced every mutate-in-place registry save/restore block with the new
  `conftest.registry_overlay` context manager, which rebinds the module
  registries to patched copies — the originals are never touched, so a
  mid-test failure cannot leave synthetic entries behind.
- Applied `@pytest.mark.full_resolution` to every directly-invoking
  test function that opts into 1s rows (fixtures document the rationale in
  their docstrings; markers cannot attach to fixtures, and the two cheap
  marker meta-tests deliberately carry no static marker), and
  `test_determinism.py` streams its byte-identity comparison via
  `filecmp.cmp` instead of whole-file `read_bytes()`.

- Fixed OTEL streaming subprocess tests leaking `otel-activity.log` into the
  CWD pytest was launched from (typically the repo root): every streaming
  invocation now passes an explicit `--otel-activity-log` under `tmp_path`,
  and an autouse session fixture fails the run if anything touches a CWD
  `otel-activity.log` again.
- Consolidated the full-resolution 7-day `--instances-per-component 3`
  generation (the suite's single most expensive pass, ~9 GB) into one shared
  session-scoped conftest fixture consumed by both
  `test_instances_per_component.py` and `test_schema_file.py`; the suite
  previously generated it three times. The 7-day N=3 byte-stability check now
  runs both of its passes at the cheap 60-second interval.
- Replaced seven module-scoped fixtures that regenerated byte-identical
  copies of session-scoped conftest runs (`--topology-mode realistic`
  duplicates of the default run in `test_topology_fanout.py` /
  `test_topology_saturation.py` / `test_topology_llm.py`, independent-mode
  and full-metrics duplicates, and `default_1d` in
  `test_instances_per_component.py`) with the conftest session fixtures; the
  explicit-flag byte-identity pins keep their own function-scoped runs. The
  shared 1-day N=3 dataset now emits `schema.json` too, so the N=3 schema
  assertions reuse it instead of regenerating.
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

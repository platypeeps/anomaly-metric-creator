# Changelog

This file captures notable user-facing changes. Between tagged releases the
authoritative history is the GitHub release notes and the git commit log; the
**Unreleased** section below is a best-effort summary of what has landed on
`main` since the last release and is updated when notable behavior changes.

## Unreleased

- `anomaly_metric_creator.server` now republishes its `server_ops` compatibility
  surface through a module `__getattr__` instead of 227 hand-written alias
  assignments. **No published name changed** — every previously importable
  attribute still resolves, to the same object — and `server` still has no
  `__all__`, so `import *` behaves exactly as before. Internal refactor with no
  CLI, HTTP, or output change; listed only because it touches an import surface.
- Trace-export hardening (audit A-018, A-019, A-070). **Behavior change:**
  `amc serve --cors-allow-origin '*'` is now refused unless `--auth-token` is
  also set — an unauthenticated wildcard origin lets any website the operator
  visits read the server cross-origin, loopback binds included. Pass an
  explicit origin or a token; `--allow-remote-without-auth` does not unlock it.
  **Behavior change:** `amc trace-bundle export-csv` now apostrophe-prefixes
  any cell beginning with a spreadsheet formula trigger (`=`, `+`, `-`, `@`,
  tab, or CR) across every column, so attacker-recorded commands open as inert
  text; stored traces are unchanged. The trace-bundle version policy is now
  documented: bundles are read by the tool version that wrote them, and a
  `schema_version` mismatch tells the operator to re-export.
- `amc serve` simulator clock and command-mutation correctness fixes (audit
  A-012..A-017). **Behavior change:** a command-mode `kubectl delete`, `scale`,
  or `patch` naming a resource absent from the (overlay-aware) snapshot now
  exits nonzero with an `Error from server (NotFound)` message and leaves the
  overlay untouched — matching the REST facade, which already 404'd — instead
  of succeeding and recording a phantom mutation. A nameless `kubectl scale
  deployment --replicas=N` (no resource name) is now a usage error
  (`kubectl.scale.usage`) rather than silently scaling `apigateway`.
  `SimulationClock.resume()` on a clock that is already running is now a no-op
  instead of rewinding simulated time. `/v1/state`'s `otel` block is copied
  under a lock so a concurrent background writer can no longer trip a transient
  500. A continuous-generation pass that fails after atomically publishing a
  new `anomalies.csv` now reloads the on-disk rows so `/v1/anomalies` and the
  MCP tools stop serving a stale in-memory copy. A negative `?limit=` on trace
  listing is clamped to `0` on both the memory and SQLite backends (it used to
  invert the memory slice and mean "unbounded" in SQLite). A zero-byte
  per-component CSV now warns and yields no rows instead of raising. Generated
  artifact bytes and locked hashes are unchanged.
- `amc serve` error plane is now observable by default (audit
  A-071/A-072/A-073/A-076). Every unhandled 500 (`do_GET` / `do_POST` and now
  PUT/PATCH/DELETE via `_handle_mutating_method`, Status-shaped for Kubernetes
  API paths) plus the MCP internal-error path and the background
  continuous-generation / OTEL failure arms route the exception type, message,
  and a capped traceback tail to one operator sink — the structured error log
  with `--structured-log`, otherwise stderr — so a default-flags failure is no
  longer silent. Client response bodies stay generic (detail never leaks into a
  body). A raising mutating handler now returns a 500 instead of resetting the
  connection. **Behavior change:** `/readyz` is now a real readiness check —
  it returns `503 {"ready": false, "reason": "artifacts"|"generation"}` when a
  declared-emit artifact is missing (e.g. `--no-generate` over an empty dir) or
  the continuous-generation thread failed, instead of an unconditional
  `{"ready": true}`. Harness scripts gating on `/readyz` will now see the real
  not-ready condition. Generated artifact bytes and locked hashes are unchanged.
- `amc serve` now makes load-shedding and cross-sink incident reconstruction
  observable by default (audit A-075/A-077). DoS-bound refusals — the
  worker-thread-cap `503`, the SSE-ceiling `503`s, and the rate-limit `429` —
  are counted per kind and surfaced as `refusals` on `/v1/state`, with a
  one-per-kind `[serve-refusal]` stderr line on first trip so saturation is
  visible even without `--structured-log`. Every request also carries a
  `request_id` (a `uuid4` prefix minted once per request) that lands in the
  structured request/error records and on every `CommandTrace` (`request_id`,
  a payload-only field — no SQLite schema change), so a request record and its
  trace share a join key. No new flags; generated artifact bytes and locked
  hashes are unchanged.
- Internal: `amc serve` MCP/trace hot paths made flat instead of
  history-linear (audit A-039/A-040/A-041/A-042). The MCP window tools
  (`get_metric_histogram`, `group_metrics_by_field`, `get_correlated_timeline`)
  now gate CSV rows on a lexicographic timestamp-window string before
  `strptime` and break past the window on the monotonic wide layout;
  `/v1/state` reports the unsupported-command count via
  `COUNT(DISTINCT fingerprint)` and the debug-UI unsupported summary is memoized
  on a trace-store generation, so repeated polls at an unchanged head are O(1);
  the trace store now holds one long-lived SQLite connection and one long-lived
  JSONL append handle instead of reopening both per insert; and
  `resource_snapshot()` hoists per-component-invariant lists above the
  per-replica loop. All changes are output-identical — no artifact bytes, no
  command/trace responses, and no locked hashes change.
- Packaging: raised the declared dependency floors (`numpy`,
  `opentelemetry-proto`, `protobuf`, `pyyaml`) to the oldest combination
  actually exercised under the supported interpreter (`requires-python >=
  3.14`), pinned to the versions `uv.lock` resolves so the manifest no longer
  advertises a lower bound with no cp314 wheels. No resolved-version change
  (`uv lock --check` clean); runtime behavior and output bytes are unchanged.
- Internal: extracted the client-command parse cluster (`ParsedCommand`, the
  flag/alias tables, `parse_command` with its `_split_flags` tokenizer helpers,
  the `_parse_kubectl` / `_parse_helm` family sub-parsers, and the
  `command_fingerprint` / `guess_intent` / `_redact_*` fingerprint/redaction
  helpers) out of `server_ops.py` into a new stdlib-only leaf
  `server_ops_parse.py`, re-imported at the original position. Import-only
  refactor; no behavior, output, or public-import change.
- Internal: extracted the ops scenario-profile registry
  (`OPS_SCENARIO_PROFILES`, its `OpsComponentImpact` / `OpsScenarioProfile`
  dataclasses, `_impact` / `_profile` builders, and `validate_ops_profiles`)
  out of `server_ops.py` into a new pure-data leaf `server_ops_profiles.py`,
  re-imported at the original position. Import-only refactor with object
  identity preserved; no behavior, output, or public-import change.
- `amc serve` now supports **bounded Kubernetes watch streams**. A real-client
  `kubectl get pods|deployments --watch` (API `?watch=true`) streams
  newline-delimited `ADDED`/`MODIFIED`/`DELETED` events backed by the same
  overlay-aware snapshot the list path uses, closing on the client's
  `timeoutSeconds` or a 300-second ceiling and consuming one bounded SSE slot
  (over the ceiling it refuses with a Kubernetes `Status` 503). There is no
  `resourceVersion=` resume — kubectl re-lists on reconnect. Over the one-shot
  `POST /v1/commands` API, `kubectl get --watch` returns the current table plus
  a note pointing at real kubectl and is traced as `partial`. Each watch records
  one `kubernetes-api` trace with its event count.
- `POST /v1/mutations/reset` now returns an additive `"scope":
  "mutation-overlay"` field alongside the existing `mutations` summary, making
  the reset's overlay-only contract explicit. Reset restores the selected
  scenario baseline for every mutation-overlay family (workloads, deleted pods,
  created/deleted resources, extra events, Helm release) and intentionally
  leaves generated artifacts, command traces, and the simulated clock untouched.
  The README serve section and the operations spec document the exact does/
  does-not scope; no existing caller of the `mutations` summary is affected.
- `amc serve` now prints a copyable inspection banner after the startup URL
  lines: a kubeconfig fetch, namespaced `kubectl`/`helm` examples, a
  `POST /v1/mutations/reset` hint, and an `Active scenarios:` line. The banner
  embeds the real `--auth-token` value only on a loopback bind (a non-loopback
  bind prints a `$AMC_TOKEN` placeholder instead), and suppresses the scenario
  line under `--mcp-eval-mode`. Documents the interactive failure-mode recipe
  (`amc serve --scenarios <slug>`) in the README; serve security defaults are
  unchanged.

## 0.4.0 - 2026-07-21

**Breaking release.** AMC now follows its latest-stable-CPython-only policy:
the supported floor moved from Python 3.11 in v0.3.0 to Python 3.14. This
release also ships the installable package, server/MCP/eval surfaces, trace
bundles, atomic artifact publication, and the completed legacy-module
decomposition accumulated since v0.3.0.

### Added

- **Server mode (`amc serve`).** A stdlib HTTP server that turns a generated
  run into an interactive incident simulator: a `kubectl`/Helm-compatible
  Kubernetes REST facade for real clients, a `POST /v1/commands` command
  simulator (deterministic stdout/stderr/exit codes, never shells out),
  command-trace persistence (ring buffer + optional JSONL/SQLite), an inline
  debug UI at `/debug`, and continuous regeneration (`--continuous-generate`).
- **MCP endpoint (`POST /mcp`).** A stateless streamable-HTTP JSON-RPC layer
  exposing a read-only tool registry over what the run produced, so `amc
  serve` can be an evaluation target for AI incident-response agents.
- **Evaluation mode (`--mcp-eval-mode`).** A ground-truth wall that hides
  every rubric-bearing surface (the anomaly manifest, scenario registry, and
  active-scenario identifiers) from an agent under evaluation.
- **`trace-bundle` subcommand.** Offline `summary`/`search`/`unsupported`/
  `export-csv` analysis of exported command-trace JSON without starting the
  server.
- **`--start-time`** so generated CSV timestamps and `schema.json` metadata
  can be anchored to a caller-provided whole-second UTC instant, while
  preserving the historical default start time when the flag is omitted.
- **`SECURITY.md`** documenting the trust model, the remote-bind posture
  (discouraged; not a supported production posture), and credential handling.
- **Runtime version discovery.** `amc --version` and
  `anomaly_metric_creator.__version__` report the installed distribution
  version; source trees without installed metadata use `0+unknown`.

### Changed

- **Atomic artifact publication.** Every generated artifact is now staged as a
  sibling `<name>.tmp` and `os.replace`d into place, so a concurrent reader
  (notably the serve-mode HTTP threads under `--continuous-generate`) never
  observes a partial file. Output bytes are unchanged.
- **Remote-bind DoS hardening.** Defaults-on resource bounds for a reachable
  `amc serve` bind — `--max-concurrent-requests` (64), `--max-sse-connections`
  (16), `--socket-timeout-seconds` (30) — each disablable with `0`.
- **Python support policy: latest stable CPython only.** `requires-python` is
  now `>=3.14`; older interpreters are unsupported and untested.

### Security

- **Response-header redaction defaults to mask.** Server response headers are
  masked unless they are explicitly known safe, closing the posture gap fixed
  in PR #213.

### Fixed

- **Combined-artifact input allowlist.** The combine path excludes stale or
  foreign CSVs instead of treating every nearby CSV as a component artifact
  (PR #134).
- **Long-form merge file-descriptor preflight.** Large merges fail clearly
  before exhausting the process file-descriptor limit (PR #128).

### Internal

- The ~13k-line `legacy.py` monolith was decomposed into focused modules
  (`redaction`, `timeutil`, `otlp`, `csv_layout`, `gauges_impl`, `artifacts`,
  `combine_impl`, `schema_impl`, `validate_impl`, `otel_stream`,
  `run_pipeline`, …); the compatibility facade is now 766 lines and output
  remains byte-identical.

## 0.3.0 - 2026-06-11

**Breaking release.** The CLI consolidated around the common use cases
(PR #101) and then completed its phase-9 flag day in the same cycle:
`--topology-mode` (PR #103) and the 16 consolidation alias flags
(PR #104) no longer parse. No released version ever shipped the
aliases — 0.2.0 users migrate directly from the historic flat surface
to the canonical one (`--emit`, the `combine`/`validate` subcommands,
`--otel-send`/`--otel-endpoint`/`--otel-auth-token`; see the README
CLI reference). The phase-9 scenario re-tune (PR #102) also makes
default output validator-violation-free at every duration.

### Removed

- Phase-9 flag day, part 2 (CLI): the 16 deprecated alias flags from the
  CLI consolidation are removed and no longer parse — `--emit-selection`,
  `--combine`, `--combine-only`, `--validate-output`, `--validate-warn`,
  the five OTEL toggles (`--otel-enabled`, `--otel-disabled`,
  `--otel-emit-gauges`, `--otel-no-emit-gauges`, `--otel-gauges-only`),
  and the six per-signal endpoint/token flags. The canonical surface is
  the only surface: `--emit` (with the `combined` token), the `combine`
  and `validate` subcommands, `--otel-send`, `--otel-endpoint`, and
  `--otel-auth-token`. Per-signal endpoint/token overrides remain
  available via the `MEZMO_OTEL_*` env vars (which an explicitly typed
  `--otel-endpoint` base still beats for selected signals). The
  `MEZMO_OTEL_EMIT_GAUGES` env default is removed along with the
  toggles: with `--otel-send` as the only enable path its
  authoritative selection meant the env var could never take effect
  (it could only error or be overridden). The `DEPRECATION:` stderr notice mechanism,
  the `_DEPRECATED_FLAGS` registry, and the canonical/alias mixing
  gates are gone; `--help-all` lists only the advanced knobs. Default
  output bytes are unchanged (the default invocation never used an
  alias).

- Phase-9 flag day, part 1: the deprecated `--topology-mode independent`
  no-topology contrast alias is removed — the flag no longer parses and
  realistic topology is the only generation mode. `generate_component`'s
  `apply_dtype_int_cast` kwarg survives for programmatic callers, and the
  validator still honors `schema.json` documents whose metadata records
  `"independent"` (the writer now only ever emits `"realistic"`, keeping
  schema bytes — and the locked schema hashes — unchanged). The test
  suite's pure-natural statistical baseline (the 8-sigma band checks and
  the realistic-vs-natural contrast tests) now comes from a direct
  `generate_component` fixture in `tests/conftest.py` instead of an
  independent-mode run; the `LEGACY_INDEPENDENT_ONE_DAY_HASHES` byte pins
  retire with the alias. Two latent bugs in the shared `natural_band`
  test helper surfaced during the migration and are fixed: the coarse
  sampling grid stopped short of the final second (under-sampling a
  monotonically trending additive like `database.disk_used_pct`), and
  the band ignored the half-ULP of the 3-decimal CSV rounding.

### Fixed

- Phase 9 scenario re-tune: `llm_weekend_batch` now saturates
  `llm_analytics.context_overflow_rate` toward 0.97 — inside the metric's
  declared `max_value=1` — instead of the historic 8.5 that made every
  default 7-day `validate` run report an `above_max` violation. The span
  stays 3.2–6.7 sigma above the 0.3 natural baseline, so the
  context-window saturation pattern remains clearly detectable, and both
  default runs are now violation-free (pinned by empty expected-violation
  sets in `tests/test_validate_output.py`). The four hash-locked 7-day
  artifacts containing `llm_analytics` values were re-locked — the default
  and `--signal-level high --anomaly-count 100` `llm_analytics.csv`
  hashes, the 7-day `gauges.csv` hash, and the N=3 7-day
  `llm_analytics.csv` hash (no 7-day combined-CSV hash lock exists);
  `anomalies.csv` and every other component are byte-identical (the
  generator draws the same RNG sequence).

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
    selected signals (the derivation beats the per-signal env vars; the
    env vars supply defaults when no base is given).
  - Two-tier help: `-h` shows the grouped common surface; `--help-all`
    additionally lists the advanced knobs.
  - The 16 replaced flag spellings briefly survived as deprecated
    aliases behind `DEPRECATION:` stderr notices, then were removed in
    the same release cycle at the CLI flag day (see the Removed entry
    above) — no released version ever shipped the aliases.

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

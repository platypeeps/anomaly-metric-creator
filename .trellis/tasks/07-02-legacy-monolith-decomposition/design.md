# Design: decomposing legacy.py (12,992 lines)

## Section map (measured 2026-07-02, post-#176)

| Lines | Section | Target module |
|---|---|---|
| 1–54 | imports / module docstring | stays |
| 55–128 | Configuration constants | stays (shared root) |
| 129–209 | `RunContext` | stays (threading spine) |
| 210–312 | `MetricSpec`, `Instance` | `models_impl.py` (late) |
| 313–384 | `Edge`, `SaturationParams` | `topology_impl.py` (late) |
| 385–1168 | Scenario builders + `Scenario` + spec validators | `scenarios_impl.py` (late) |
| 1169–1217 | Atomic artifact publication | `artifacts.py` (mid) |
| 1218–2393 | `generate_component` / `_natural_column` / anomaly dispatch | `generation.py` (**last**, RNG-critical) |
| 2394–2464 | cascade + seasonality helpers | with `scenarios_impl.py` |
| 2465–3010 | `COMPONENTS` catalogs | `catalog.py` (late) |
| 3011–4053 | `TOPOLOGY` + validators + coupling/saturation composition | `topology_impl.py` (**last**, RNG-critical) |
| 4054–4515 | per-instance topology | `topology_impl.py` (**last**) |
| 4516–7577 | `SCENARIOS` registry (~3,060 lines of catalog data) | `scenario_catalog.py` (late; data-only move) |
| 7578–8436 | CLI (`parse_args`, `_reconcile_cli_surface`, subcommand parsers) | `cli_args.py` (mid) |
| 8437–10519 | combine writers, reporting artifacts, OTLP builders, gauges, redaction | `combine_impl.py`, `otlp.py`, `gauges_impl.py`, `redaction.py` (early) |
| 10520–~12992 | schema writer, validator, OTEL streamers, `main()` | `schema_impl.py`, `validate_impl.py`, `otel_stream.py` (mid); `main()` stays |

## Extraction pattern (every PR follows this exactly)

1. Create the new module with the moved code **verbatim** (whole functions,
   unchanged bodies; only the imports the moved code itself needs).
2. In `legacy.py`, delete the moved block and add a re-import at the same
   conceptual location: `from .redaction import (...)` with `as`-aliases so
   every name still resolves as `legacy.<name>` — the shim, the facades,
   `conftest._load_amc()`, `state.legacy.<name>` lookups, and all tests keep
   working unchanged.
3. Dependency direction is **one-way**: new modules never import `legacy`.
   If moved code needs a helper that stays behind, either the helper moves
   too (when it belongs) or it is passed as an argument — never a circular
   import.
4. Run the full suite (all locked SHA-256 golden hashes) before the PR; the
   PR gets the `full-ci` label.
5. Update CLAUDE.md's module map in the same PR.

## Monkeypatch inventory (binding hazard)

Tests monkeypatch these on the `amc` module: `COMPONENTS`, `DERIVATIONS`,
`INSTANCES`, `SCENARIOS`, `TOPOLOGY`, `_TOPOLOGY_LOAD_METRICS`,
`_TOPOLOGY_SATURATION_TARGETS`, `_format_fixed3`,
`_wide_component_rows_are_monotonic`. A moved function that *other moved
code* calls through its new home would no longer see a monkeypatch applied
to the `legacy` namespace. Rule: any symbol on this list — or called by a
function on this list's call path — must either stay in `legacy.py` or move
*together with all its intra-module callers* in the same PR. None of the
early extractions touch this list.

## Sequencing (leaf-first; one PR each)

1. **`redaction.py`** (~85 lines) — `_SENSITIVE_HEADER_NAMES`,
   `_SCHEMED_SENSITIVE_HEADERS`, `_mask_sensitive_value`, `_masked_headers`,
   `_redact_sensitive_headers`. Stdlib-only, no RNG, no callers outside
   legacy. Proves the pattern.
2. **`timeutil.py` + `otlp.py`** (~60 + ~450 lines) — time helpers
   (`_parse_csv_timestamp`, `_UNIX_EPOCH_UTC`, `_dt_to_unix_nanos`,
   `_to_unix_nanos`) into `timeutil.py`; the eight `_build_otlp_*`
   builders + `_anomaly_event_id` into `otlp.py` (imports `timeutil`).
   `timeutil` is a new leaf not in the PRD candidate list — justified
   because the time helpers are shared by combine, gauges, OTLP, and
   `server_mcp` (via `state.legacy._parse_csv_timestamp`), so they cannot
   live inside any one consumer without cycles.
3. **`gauges_impl.py`** — `write_gauges_csv` + `_iter_component_rows` +
   instance-block scan helpers, EXCEPT `_scan_component_csv_headers` /
   `_classify_component_csv_header`, which are shared with combine and the
   MCP tools → those go to a small `csv_layout.py` leaf in the same PR.
4. **`artifacts.py`** — `_atomic_artifact_open`, `_atomic_write_text`,
   `_ATOMIC_TMP_SUFFIX`.
5. **`combine_impl.py`** — `combine_logs*`, wide/long writers, monotonic
   scan. Caveat: `_wide_component_rows_are_monotonic` is monkeypatched →
   it and its only caller move together; the `combine.py` facade re-points.
6. **`schema_impl.py`** then **`validate_impl.py`** — writer first, then
   the validator (reader). `schema.py` facade re-points.
7. **`otel_stream.py`** — `stream_otel_signals`, `stream_otel_gauges`,
   transport/retry/activity-log helpers (uses `redaction`, `otlp`,
   `timeutil`). `otel.py` facade re-points.
8. **`cli_args.py`** — `parse_args`, `_reconcile_cli_surface`,
   `_ADVANCED_DESTS`, subcommand parsers. `main()` stays in `legacy.py`.
9. **Late (each its own design check-in):** `scenario_catalog.py` (data
   move), `catalog.py` (`COMPONENTS`), `models_impl.py`.
10. **Last:** `generation.py` + `topology_impl.py` — the RNG-order-critical
    core. Only after every hash has survived steps 1–9.

## Import-time validator ordering

Validators that run at module import (`_validate_topology`,
`_validate_scenarios_registry`, `_validate_instances_registry`,
`_validate_topology_metric_registries`) stay in `legacy.py` until their
data moves; when a registry moves, its validator moves in the same PR and
`legacy.py`'s re-import keeps the execution point (import of the submodule)
at the same relative position, preserving the documented order.

## Invariants (checked every PR)

- All locked SHA-256 golden hashes unchanged (full suite).
- `python anomaly-metric-creator.py --help` and `amc --help` work.
- Every new module < 800 lines.
- `conftest._load_amc()` still yields a module exposing the full historic
  namespace.
- mypy finding count does not increase (CI report-only step).

# Design: decomposing legacy.py (12,992 lines at design time)

## Status (2026-07-06 review)

- Steps 1–7 have landed (PRs #178, #181, #183, #185, #198, #203);
  `legacy.py` is now ~9,180 lines (~29% removed). Steps 8–10 remain
  (`planning` child tasks).
- **[2026-07-07] Step 8 (cli_args) ↔ step 9 (catalog-data) dependency
  found.** An AST scan of the CLI cluster shows `parse_args` reads the
  monkeypatched `COMPONENTS` / `SCENARIOS` / `DEFAULT_METRICS_PER_COMPONENT`
  registries (plus ~14 plain config constants). Because the one-way rule
  forbids `cli_args.py` from importing `legacy`, step 8 is **not** a plain
  verbatim move — it needs the `schema_impl`-style callback seam, OR step 9
  must land first so cli_args imports the catalog module directly. Full
  analysis + the recommended approach are in the
  `07-02-decomp-cli-args` PRD. **Decide seam-vs-reorder here before the
  step-8 code.** This corrects the design's implicit "step 8 is a leaf move"
  assumption.
- Corrections applied below, marked **[2026-07-06]**: step 3/4 dependency
  inversion (landed together in PR #183), csv_layout scope expansion,
  `scenarios_impl.py` added to the sequencing (it was in the section map
  but missing from the step list), and the `validate_impl.py` size
  deviation recorded under Invariants.
- **Open end-state decision:** after steps 8–10, `legacy.py` retains
  `main()` (~590 lines), constants/emit-registry/re-import wiring (~350),
  scenario resolution + `_load_instance_config` (~395, destination
  unassigned), and `RunContext` — ~1,300–1,500 lines, still above the
  800-line cap. Decide whether the epic's "thin" target means <800
  (requires scoping the resolution cluster and slimming `main()`) or
  "dispatch + wiring only" (record the accepted size).

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
   **[2026-07-06] As landed (PR #183):** the split went further than
   planned — ALL shared CSV primitives (`_iter_component_rows`,
   `_iter_component_instance_rows`, `_scan_instance_block_layout`,
   `_ensure_long_form_fd_capacity`, `_is_anonymous_instance_list`,
   `_INSTANCE_DIMENSION_COLUMNS`, plus the two originally named helpers)
   live in `csv_layout.py` (388 lines); `gauges_impl.py` holds only
   `write_gauges_csv`. Better cohesion (otel_stream and server_mcp share
   the primitives); recorded here so the map matches reality.
4. **`artifacts.py`** — `_atomic_artifact_open`, `_atomic_write_text`,
   `_ATOMIC_TMP_SUFFIX`. **[2026-07-06]** Landed *with* step 3 in PR #183:
   `gauges_impl` imports `_atomic_artifact_open` and a leaf cannot import
   `legacy`, so this step could never follow step 3 — the original
   ordering inverted the dependency.
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
   move), `catalog.py` (`COMPONENTS`), `models_impl.py`, and
   **`scenarios_impl.py`** **[2026-07-06]** — the section map assigns the
   scenario builders + `Scenario` + spec validators (old lines 385–1168;
   now ~362–1035 + `register_cascade`/seasonality at ~2337–2407 +
   `_validate_scenario_spec` at ~6478–6806, ~745+ lines total) to
   `scenarios_impl.py`, but the original step list never scheduled it.
   It moves with (or immediately after) the `scenario_catalog.py` data
   move, honoring the move-with-registries validator rule. Without this,
   the epic "completes" with the cluster stranded in `legacy.py`.
10. **Last:** `generation.py` + `topology_impl.py` — the RNG-order-critical
    core. Only after every hash has survived steps 1–9.

## SD Work Designs Proposal — 2026-07-17

Two decisions the Status section left open, now proposed (recorded here per
the Status section's own instruction to "decide seam-vs-reorder here"):

### Decision 1: step-8 seam-vs-reorder → **callback seam; keep sequence 8 → 9 → 10**

Reordering step 9 before step 8 does **not** remove the need for a seam, so
the reorder buys nothing:

- Tests patch the registries on the **legacy namespace** (`amc.COMPONENTS`,
  `amc.SCENARIOS`, …) and then call `parse_args`. If `cli_args.py` imported
  a future `catalog.py` directly (`from .catalog import COMPONENTS`), the
  name would bind at import time and a patch on `legacy.COMPONENTS` would be
  invisible — `tests/test_cli_surface.py` would need edits, violating the
  step-8 acceptance criterion ("passes unchanged, no test edits").
- The callback seam (`_configure_cli_runtime(get_components=lambda:
  COMPONENTS, …)` called from `legacy.py` at import) resolves the names in
  legacy's namespace **at call time**, so monkeypatches stay visible with
  zero test edits. This is byte-for-byte the precedent `schema_impl.py` /
  `validate_impl.py` already established (documented in CLAUDE.md).
- The ~14 plain config constants (`DEFAULT_*`, `MAX_*`, `SECONDS_PER_DAY`,
  `START`, `SIGNAL_LEVELS`, `PREFLIGHT_CELL_CAP`, …) are never
  monkeypatched (verify with one grep over `tests/` before relying on it at
  implementation time) — pass them by value at configure time or move them
  to a small shared leaf; either satisfies the one-way rule.

Consequence: step 8 stays before step 9 and the two steps stay decoupled —
step 9 landing later does not require revisiting the seam (the seam keeps
pointing at legacy's namespace, which is where tests patch).

### Decision 2 (recommendation, needs maintainer sign-off at epic close): end-state = **"dispatch + wiring only" with a recorded waiver**

After steps 8–10, `legacy.py` retains `main()` (~590), `RunContext`,
constants + emit registry + re-import wiring, and the resolution cluster
(~395, destination unassigned). Proposal:

- Assign the resolution cluster: scenario-resolution helpers
  (`_resolve_scenarios`, `_apply_scenarios`,
  `_apply_signal_level_and_count`) move to `scenarios_impl.py` in step 9
  (they are scenario machinery; the section map already assigns that family
  there); `_load_instance_config` moves with `models_impl.py` (it constructs
  and validates `Instance` objects).
- Even so, `main()` + `RunContext` + constants + the re-import wiring block
  land around ~1,000 lines. Forcing <800 would require splitting `main()`
  itself — a behavior-risky refactor outside this epic's verbatim-move
  mandate. Recommend: keep the <800 cap for every **new** module, record an
  explicit waiver for `legacy.py` as the dispatch/wiring root, and state the
  accepted size in CLAUDE.md when the epic closes. Revisit only if a later
  epic wants to decompose `main()` behaviorally.

Child-task planning artifacts (design.md + implement.md for
`07-02-decomp-cli-args`, `07-02-decomp-catalog-data`,
`07-02-decomp-generation-topology`, `07-06-validate-impl-split-and-cleanup`)
are being filled by the same 2026-07-17 planning pass; the epic-level
execution order lives in this task's `implement.md`.

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
- Every new module < 800 lines. **[2026-07-06] Known deviation:**
  `validate_impl.py` shipped at 1,684 lines in step 6 (the validator moved
  wholesale). Either a follow-up split restores the invariant or the
  waiver is recorded explicitly — tracked as a proposed follow-up task
  from the 2026-07-06 architecture review.
- `conftest._load_amc()` still yields a module exposing the full historic
  namespace.
- mypy finding count does not increase (CI report-only step).

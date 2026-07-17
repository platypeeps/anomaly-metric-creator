# Extract cli_args.py — Design (SD Work Designs, 2026-07-17)

Epic design.md (2026-07-17 proposal section) fixed the two upstream
decisions this PRD flagged: **callback seam** (not reorder — reordering
would not remove the seam need, because tests patch the registries on the
*legacy* namespace), and the resolution cluster (legacy.py:7118–7513)
**stays put in step 8** — scenario resolution moves to `scenarios_impl.py`
and `_load_instance_config` to `models_impl.py` in step 9.

## Overview

Move the CLI cluster (~legacy.py:7514–8372, ~860 lines): `parse_args`,
`_reconcile_cli_surface`, `_ADVANCED_DESTS`, the four
`_main_*_subcommand` functions, and the CLI-only private helpers
(`_parse_start_time_arg`, `_sig`, `_flag_in_argv` — verify zero non-CLI
callers before moving; move-with-callers rule). `main()` and the argv[0]
dispatch stay in `legacy.py` and call the re-imported names.

## Proposal

- **Seam:** `cli_args._configure_cli_runtime(*, get_components,
  get_scenarios, get_default_metrics_per_component, constants: dict)` —
  called once by `legacy.py` immediately after its `from .cli_args import
  …` re-import block, passing `lambda: COMPONENTS` etc. (call-time
  resolution in legacy's namespace → monkeypatches stay visible) and the
  ~14 plain constants **by value** (`DEFAULT_*`, `MAX_*`,
  `PREFLIGHT_CELL_CAP`, `SECONDS_PER_DAY`, `SIGNAL_LEVELS`, `START`,
  `DEFAULT_OTEL_STREAM_AUTH_SCHEME`). Precondition to verify with one
  grep: none of the constants is monkeypatched anywhere in `tests/`
  (the registries are; the constants should not be — if one is, it gets a
  getter instead of a value).
  Fail-fast: parsing before configuration raises a clear RuntimeError
  naming `_configure_cli_runtime` (mirrors the schema_impl seam posture).
- **Subcommand executors:** `_main_combine_subcommand` /
  `_main_validate_subcommand` / `_main_trace_bundle_subcommand` import
  their impl modules directly (`combine_impl`, `validate_impl` /
  `schema_impl`, `trace_bundle`) — leaf→leaf, one-way rule satisfied.
  `_main_serve_subcommand` keeps its lazy in-function `from . import
  server` and needs the loaded legacy module object without importing
  legacy: thread it through the seam
  (`get_legacy_module=lambda: sys.modules[__name__]` from legacy's side)
  or pass it as an argument from `main()`'s dispatch — pick whichever the
  current call shape makes smaller; both honor one-way imports.
  Audit each executor for reads of legacy-namespace names that tests
  monkeypatch (e.g. if validate dispatch resolves `validate_output` via
  `legacy`); any such read routes through the seam.
- **Verbatim-move invariants:** the two-tier help post-construction
  `p._actions` pass, the `p.set_defaults` `MEZMO_OTEL_*` env seeding
  (stdlib-only — moves untouched), and reconciliation-before-validation
  ordering all move byte-identical; `tests/test_cli_surface.py` and
  `tests/test_args.py` must pass with zero edits.
- **Size cap:** the cluster is ~860 lines — over the 800 cap as one file.
  If the measured result exceeds 800, split the four subcommand executors
  into `cli_subcommands.py` in the same PR (flag surface vs subcommand
  execution is a cohesive cut); both files stay <800. Do not waive.

## Boundaries And Non-Goals

- No flag behavior changes, no help-text changes, no new flags.
- `main()`, argv[0] dispatch, and the resolution cluster stay in
  `legacy.py` (step 9 territory).
- No test edits (acceptance criterion; the seam exists precisely to avoid
  them).

## Affected Files

- New: `src/anomaly_metric_creator/cli_args.py` (+ possibly
  `cli_subcommands.py`); `src/anomaly_metric_creator/legacy.py`
  (delete + re-import + configure call); CLAUDE.md module map;
  `.trellis/spec/amc/backend/index.md` if it names the CLI seam.

## Risks And Edge Cases

- The splice hazard: the cut range must be grepped for `^from \.`
  re-imports of earlier extractions before deletion (CLAUDE.md documented
  failure mode from step 5).
- `parse_args` error paths embed registry-derived strings (catalog listings
  in messages) — the getters must be called inside the error-path code,
  not hoisted, to keep messages identical under patched registries.
- Import order: the configure call must run before any facade/test can
  invoke `parse_args` — placing it directly under the re-import block in
  `legacy.py` guarantees it (package `__init__`/facades import `legacy`
  first).
- `conftest._load_amc()` loads legacy with package context — the new
  module resolves like the other extractions; no conftest change expected.

## Validation

- Full suite (all golden hashes; CLI parsing draws no RNG — hashes are
  structurally safe, the suite proves it).
- `python anomaly-metric-creator.py --help`, `amc --help`, `--help-all`
  rendered and diffed against pre-move output (byte-identical).
- `tests/test_cli_surface.py`, `tests/test_args.py`, `tests/test_cli.py`,
  serve-flag coverage in `tests/test_server.py` — all unchanged and green.

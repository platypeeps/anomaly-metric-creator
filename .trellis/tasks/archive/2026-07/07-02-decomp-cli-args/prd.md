# Extract cli_args.py from legacy.py (decomposition step 8)

## Goal

Move parse_args, _reconcile_cli_surface, _ADVANCED_DESTS, and the subcommand parsers to cli_args.py; main() stays in legacy.py. tests/test_cli_surface.py and the two-tier help contract must be unchanged.

## Dependency finding (2026-07-07 — read before starting; changes the approach)

An AST scan of the CLI cluster (legacy.py:7524–8373) found it references
**~17 legacy module-level constants plus the monkeypatched `COMPONENTS` /
`SCENARIOS` / `DEFAULT_METRICS_PER_COMPONENT` registries**:

- Registries read live at parse time (monkeypatched by tests, so they
  cannot be captured by value): `COMPONENTS`, `SCENARIOS`,
  `DEFAULT_METRICS_PER_COMPONENT`. Used in `--components` / `--scenarios`
  validation, the `--exclude-scenarios` check, help/error strings, and
  `min(args.metrics_per_component, len(COMPONENTS[c]))`.
- Plain config constants (not monkeypatched): `DEFAULT_DROP_RATE`,
  `DEFAULT_DURATION_DAYS`, `DEFAULT_INTERVAL_SECONDS`, `DEFAULT_OUTPUT_DIR`,
  `DEFAULT_ROW_COUNT`, `DEFAULT_SEED`, `DEFAULT_SIGNAL_LEVEL`,
  `DEFAULT_OTEL_STREAM_AUTH_SCHEME`, `MAX_INSTANCES_PER_COMPONENT`,
  `MAX_METRICS_PER_COMPONENT`, `PREFLIGHT_CELL_CAP`, `SECONDS_PER_DAY`,
  `SIGNAL_LEVELS`, `START`.
- Private helpers: `_parse_start_time_arg`, `_sig`, `_flag_in_argv`.

**Implication:** `cli_args.py` cannot be the simple verbatim move the
original requirements imply, because the one-way import rule forbids it from
importing `legacy`, yet it needs the live monkeypatched registries. This is
the same situation `schema_impl.py` / `validate_impl.py` solved with a
**callback seam**. The step-8 extraction therefore requires, in order of
preference:

1. **Callback seam (recommended, matches precedent):** add
   `_configure_cli_runtime(*, get_components, get_scenarios,
   get_default_metrics_per_component, ...)` in `cli_args.py`, called by
   `legacy.py` at import with `lambda: COMPONENTS` etc. so monkeypatching
   `legacy.COMPONENTS` is still honored. The ~14 plain constants can be
   passed by value at config time (they are never monkeypatched — confirm
   with a grep before relying on it) OR moved to a small shared leaf.
2. **Reorder step 9 before step 8:** if `catalog.py` (COMPONENTS /
   DEFAULT_METRICS_PER_COMPONENT) and `scenario_catalog.py` (SCENARIOS) land
   first, `cli_args.py` imports them directly and needs no seam. This is
   arguably cleaner but changes the epic sequence.

Decide between (1) and (2) in the epic design.md Status section before
writing code. Recorded 2026-07-07 by the review-driven implementation pass;
the extraction itself was deliberately deferred to a focused session because
it is the epic's most contract-dense step (byte-identical CLI surface +
two-tier help + import-time validator ordering + the new registry seam).

## Requirements (filled 2026-07-06 from the epic design + review)

- Move `parse_args`
  ([legacy.py:7711](src/anomaly_metric_creator/legacy.py:7711)),
  `_reconcile_cli_surface`
  ([legacy.py:7567](src/anomaly_metric_creator/legacy.py:7567)),
  `_ADVANCED_DESTS`, and the dedicated subcommand parsers
  (`_main_combine_subcommand` / `_main_validate_subcommand` /
  `_main_serve_subcommand` / `_main_trace_bundle_subcommand` — the CLI
  cluster is roughly legacy.py:7514–8372, ~860 lines) into `cli_args.py`,
  following the verbatim-move + re-import pattern.
- `main()` and the subcommand dispatch on `argv[0]` stay in `legacy.py`
  (per design.md).
- Preserve exactly: the two-tier help (`-h` five argument groups /
  `--help-all` unhiding via the post-construction `p._actions` pass), the
  `p.set_defaults` seeding of per-signal `MEZMO_OTEL_*` env defaults, and
  the reconciliation-before-validation ordering.
- **Open scoping decision (record at task start):** the scenario-resolution
  helpers + `_load_instance_config`
  ([legacy.py:7118](src/anomaly_metric_creator/legacy.py:7118)–7513, ~395
  lines) sit between the CLI cluster and the catalogs and have no assigned
  destination in design.md — decide move-here / stay / move-with-catalog
  and record it in the epic design.md Status section.

## Acceptance Criteria

- [ ] All locked SHA-256 golden hashes unchanged (full suite, `full-ci`).
- [x] `tests/test_cli_surface.py` passes unchanged (no test edits).
- [x] `python anomaly-metric-creator.py --help`, `amc --help`, and
      `--help-all` render identically to before the move.
- [x] `serve`'s forward-unrecognized-flags-to-`parse_args` path still works
      (`tests/test_server.py` serve-flag coverage green).
- [x] CLAUDE.md module map updated in the same PR.
- [x] The `_load_instance_config` / resolution-cluster destination decision
      is recorded in design.md.

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
- 2026-07-06: `base_branch` in task.json corrected from the merged/deleted
  `refactor/extract-redaction` stacking branch to `main`.

# Extract cli_args.py from legacy.py (decomposition step 8)

## Goal

Move parse_args, _reconcile_cli_surface, _ADVANCED_DESTS, and the subcommand parsers to cli_args.py; main() stays in legacy.py. tests/test_cli_surface.py and the two-tier help contract must be unchanged.

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
- [ ] `tests/test_cli_surface.py` passes unchanged (no test edits).
- [ ] `python anomaly-metric-creator.py --help`, `amc --help`, and
      `--help-all` render identically to before the move.
- [ ] `serve`'s forward-unrecognized-flags-to-`parse_args` path still works
      (`tests/test_server.py` serve-flag coverage green).
- [ ] CLAUDE.md module map updated in the same PR.
- [ ] The `_load_instance_config` / resolution-cluster destination decision
      is recorded in design.md.

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
- 2026-07-06: `base_branch` in task.json corrected from the merged/deleted
  `refactor/extract-redaction` stacking branch to `main`.

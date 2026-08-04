# Extract server_ops_profiles.py (epic step 1)

## Parent

Child of `07-06-server-ops-decomposition`. Implements **step 1** of that
epic's design.md: the pure-data, data-first leaf that proves the
extraction pattern before the harder renderer/API cuts.

## Goal

Move the ops-scenario-profile data registry and its dataclasses/helpers/
validator out of `server_ops.py` (currently 7,862 lines) into a new leaf
module `server_ops_profiles.py`, with **zero** HTTP/command/MCP behavior
change and the entire compatibility surface unchanged.

## Scope (exact symbols)

Move, verbatim, `server_ops.py:58-832`:

- `OpsComponentImpact` (frozen dataclass)
- `OpsScenarioProfile` (frozen dataclass)
- `_impact(...)` builder
- `_profile(...)` builder
- `OPS_SCENARIO_PROFILES` registry
- `validate_ops_profiles(legacy_module)`

`server_ops.py` re-imports every moved name at the same conceptual
location (leaf-first, one-way import; the new module never imports
`server_ops`). The `validate_ops_profiles(legacy_module)` call site in
`build_state` (`server_ops.py:1049`) stays and resolves through the
re-import.

## Requirements

- New module `src/anomaly_metric_creator/server_ops_profiles.py` holds the
  six symbols verbatim; imports only `annotations`, `dataclass`, and `Any`
  (no `server_ops` import, no reverse dependency).
- `server_ops.py` replaces the moved block with a re-import stub at the
  block's original position (between the `_query_str` helper and
  `SimulationClock`).
- Compatibility surface unchanged and still resolving:
  - `server.py` alias block (`OpsComponentImpact`, `OpsScenarioProfile`,
    `_impact`, `_profile`, `OPS_SCENARIO_PROFILES`, `validate_ops_profiles`
    at `server.py:296-301`).
  - `server_commands.py` / `server_kubernetes.py` / `server_helm.py`
    facades and `server_mcp.py` imports (which reach these names via
    `server_ops`).
- No renderer/parse/API code moves in this PR (that is steps 2-6).

## Acceptance Criteria

- [x] `server_ops_profiles.py` exists with the six symbols moved verbatim
      and no reverse import of `server_ops`.
- [x] `server_ops.py` carries the re-import stub at the original block
      position; the file is smaller by ~775 lines and the moved block is
      gone (no duplicate definition).
- [x] Server-family tests pass unchanged: `tests/test_server.py`,
      `tests/test_server_ops_fuzz.py`, `tests/test_server_mcp.py`,
      `tests/test_server_eval_mode.py`.
- [x] Full suite green (`.venv/bin/pytest`), mypy gate and ruff clean.
      The four server-family test files are the authoritative behavior
      oracle (deterministic fixtures); an import-only extraction cannot
      change render output.
- [x] `server_ops_profiles.py` added to `CLEAN_MODULES` in
      `tools/check_mypy_gate.py` and `tests/test_mypy_gate_lint.py`'s
      expected count bumped 23 → 24 (lockstep).
- [x] The six moved names remain in `server_ops.py`'s `__all__` (resolved
      via the re-import stub).
- [x] Supplementary before/after `run_command` byte-diff, if run, uses a
      frozen/paused clock and timestamp normalization so it is
      deterministic (raw output embeds the live clock for events/helm);
      the empty-diff applies to that normalized form.
- [x] CLAUDE.md and `.trellis/spec/amc/backend/architecture.md` module
      maps name `server_ops_profiles.py` and its contents.
- [x] New module < 800 lines, or the data-registry exemption is recorded
      explicitly (the registry data is the bulk).

## Non-Goals

- Steps 2-6 of the epic (parse, helm, k8s objects/tables/api, renderers).
- The four-parallel-surfaces-per-kind collapse (separate follow-up epic).
- Any `server.py` alias-block or facade edits.

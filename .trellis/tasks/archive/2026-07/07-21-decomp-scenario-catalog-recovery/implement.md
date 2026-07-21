# Extract scenario registry and resolution modules — Implementation Plan

## Execution Order

1. Capture pre-move symbol/line inventory, monkeypatch readers, import-time
   call order, `scenarios.py` identities, CLI help, and locked-hash baseline.
2. Move `Scenario`, `register_cascade`, and scenario-only builders verbatim to
   `scenario_builders.py`; re-import at the historical locations.
3. Move validation to `scenario_validation.py` with explicit inputs. Keep the
   compatibility wrapper and sole import-time call in `legacy.py`; run malformed
   registry/spec tests before continuing.
4. Move the ordered registry verbatim to `scenario_catalog.py`; verify scenario
   key order and nested tuple order against the baseline.
5. Move resolution/filtering to `scenarios_impl.py`; configure a live
   `get_scenarios=lambda: SCENARIOS` seam from `legacy.py` and verify patched
   registry tests.
6. Re-point `scenarios.py`, correct the emit-registry section label, update
   CLAUDE.md/spec ownership, and refresh the repository map.
7. Run full local validation, open a full-CI PR, address review threads, merge,
   then return to the parent epic for the end-state size/waiver decision.

## Validation Plan

```bash
.venv/bin/pytest tests/test_registry.py tests/test_scenarios.py \
  tests/test_package_facades.py tests/test_cli_surface.py -n 0
.venv/bin/pytest
.venv/bin/pre-commit run --all-files
bash scripts/sd-ai-command-pack-full-check.sh
```

Also capture and byte-compare `python anomaly-metric-creator.py --help`, assert
the validator import call count/order, run line counts on every new module, and
label the PR `full-ci`.

## Documentation And Spec Updates

- CLAUDE.md: final scenario ownership, callback seam, import-time validation,
  and declarative catalog cap exception.
- Trellis architecture/testing specs: canonical modules and patch-visible
  validation/runtime contract.
- `docs/repomix-map.md`: refresh after all tree and documentation changes.

## Review Notes

Review ordering, live binding, and import-time side effects before style. Treat
any changed golden hash, warning order, exception text, or help output as a
regression rather than a re-lock candidate.

## Rollback Points

Each module move is a standalone commit. If a focused contract fails, revert
the current move commit while retaining earlier green moves; do not patch around
an unexplained hash or import-order change.

## Follow-Ups

- The parent epic still needs its explicit final `legacy.py` size decision
  after this child lands.
- Behavioral decomposition of `main()` remains outside this verbatim-move task.

# Implement — extract server_ops_profiles.py (epic step 1)

## Ordered checklist

1. **Behavior oracle — tests are authoritative.** The four server-family
   test files (step 6) are the deterministic byte-level regression net:
   their fixtures pin the simulated clock, and an import-only extraction
   that preserves object identity cannot change any render path. Treat
   them as the authoritative behavior oracle.
   **Supplementary byte-diff (determinism caveat).** A before/after
   `run_command` scratch over a command set is only a valid oracle if the
   output is time-invariant. Several selected commands are **not**:
   `kubectl get events` embeds `SimulationClock.now()`
   (`server_ops.py:4653`) and `helm list/status` embed a fresh timestamp
   (`server_ops.py:4158,4172`), so a naive diff false-positives on clock
   drift. If run, the scratch must (a) build state with a fixed
   `--start-time` and immediately `state.clock.pause()` so `now()` is
   frozen, and (b) normalize any residual wall-clock-derived timestamp
   fields (helm) before comparing — otherwise restrict the set to
   time-invariant commands (`kubectl get pods/deployments`, `describe`,
   `logs`). The empty-diff acceptance applies to that normalized/frozen
   form only; the test suite remains the primary gate.
2. **Monkeypatch grep.** `grep -nE "setattr\(server(\._server_ops)?,"
   tests/test_server*.py tests/test_trace_bundle.py` and confirm no moved
   symbol is patched in a way the re-import would break; note the result.
3. **Create leaf.** Write `src/anomaly_metric_creator/server_ops_profiles.py`
   with the module docstring, the three imports (`annotations`,
   `dataclass`, `Any`), and the verbatim `server_ops.py:58-832` block.
4. **Cut + stub.** Delete lines 58-832 from `server_ops.py` and insert the
   `from .server_ops_profiles import (... as ...)` stub at that position.
   **Keep** the six moved names in `server_ops.py`'s `__all__`
   (`server_ops.py:7634+`) — the re-import stub rebinds them as module
   attributes, so `__all__` and `from .server_ops import *` stay valid;
   do not delete those entries.
4b. **mypy gate list.** Add
   `src/anomaly_metric_creator/server_ops_profiles.py` to `CLEAN_MODULES`
   in `tools/check_mypy_gate.py` (alphabetical position — between
   `server_mutations.py` and `timeutil.py`) — per CLAUDE.md "grow the
   list as decomposition extracts clean modules." The leaf is stdlib-only
   typed data + a validator and must pass the gated run; if it does not
   type-check clean, do not add it and record why. **Lockstep:**
   `tests/test_mypy_gate_lint.py:30` asserts `len(modules) == 23` — bump
   to `24`. The `modules[-1] == timeutil.py` and `modules[0] ==
   __init__.py` assertions stay valid (`server_ops_profiles` sorts before
   `timeutil`).
5. **Splice check.** `python -c "import anomaly_metric_creator.server_ops"`
   and grep the neighbourhood for stray `^from \.` / half-cut defs.
6. **Targeted tests.** Run the four server-family test files with `-n 0`.
7. **Full gate.** Full `pytest`, `check_mypy_gate.py`, `ruff check`.
8. **Byte-diff.** Re-run the step-1 scratch, diff before/after digests →
   must be empty.
9. **Docs.** Update the CLAUDE.md server-mode module map and
   `.trellis/spec/amc/backend/architecture.md` to name
   `server_ops_profiles.py` and its contents. Add a CHANGELOG Unreleased
   entry if the surface warrants (internal refactor — likely a one-line
   "Internal" note or N/A).
10. **Size record.** `wc -l server_ops_profiles.py`; if >800 record the
    data-registry exemption in the PR body + CLAUDE.md.

## Validation commands

```bash
.venv/bin/pytest tests/test_server.py tests/test_server_ops_fuzz.py \
  tests/test_server_mcp.py tests/test_server_eval_mode.py -n 0
.venv/bin/pytest
.venv/bin/python tools/check_mypy_gate.py
.venv/bin/ruff check src tests
```

## Review gates / rollback

- Rollback point: the extraction is a single mechanical commit; `git
  revert` restores the monolith with no data change.
- Gate before ship: all four acceptance oracles green (targeted tests,
  full suite, byte-diff empty, docs updated).

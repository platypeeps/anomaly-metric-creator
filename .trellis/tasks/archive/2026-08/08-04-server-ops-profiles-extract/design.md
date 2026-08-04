# Design — extract server_ops_profiles.py (epic step 1)

Inherits the parent epic design
(`../07-06-server-ops-decomposition/design.md`). This file records the
step-1-specific cut, verified against the live file.

## Role-swap rule (from the epic)

`server_ops.py` plays the part `legacy.py` plays in the legacy epic: code
moves *out* into a leaf, `server_ops.py` re-imports every moved name at the
same conceptual location, and the new module never imports `server_ops`.
That keeps `server.py`'s alias block, the three facades, and
`server_mcp.py` imports working with zero edits.

## The cut (measured)

- **Source range:** `server_ops.py:58-832` — the `@dataclass`
  `OpsComponentImpact` decorator (line 58) through the closing `)` of
  `validate_ops_profiles` (line 832). Lines 833-834 are the blank
  separators before `SimulationClock` (line 835) and stay.
- **Symbols moved:** `OpsComponentImpact`, `OpsScenarioProfile`,
  `_impact`, `_profile`, `OPS_SCENARIO_PROFILES`, `validate_ops_profiles`.
- **Leaf imports needed** by the moved code (audited over 58-832):
  `from __future__ import annotations`, `from dataclasses import
  dataclass`, `from typing import Any`. `field` (dataclasses) is **not**
  used in this range — it is used by `SimulationClock` at 843-845, which
  stays in `server_ops.py`.
- **No reverse dependency:** the moved code references only builtins,
  `dataclass`, `Any`, and its own within-block names. `validate_ops_profiles`
  reads `legacy_module.SCENARIOS` / `legacy_module.COMPONENTS` off its
  passed-in argument, not a `server_ops` import.

## Re-import seam

At the vacated block position in `server_ops.py` (immediately after the
`_query_str` helper, before `SimulationClock`), insert:

```python
from .server_ops_profiles import (
    OPS_SCENARIO_PROFILES as OPS_SCENARIO_PROFILES,
    OpsComponentImpact as OpsComponentImpact,
    OpsScenarioProfile as OpsScenarioProfile,
    _impact as _impact,
    _profile as _profile,
    validate_ops_profiles as validate_ops_profiles,
)
```

The `as`-aliased re-export form keeps the names public on `server_ops`
(so `server.py:296-301`'s `NAME = _server_ops.NAME` and the facades keep
resolving) and is ruff-`F401`-clean by intent.

## Call-site / import-position note

`validate_ops_profiles(legacy_module)` is invoked at `server_ops.py:1049`
inside `build_state` — **call-time**, not import-time. The epic design
flagged "preserve import-time execution position" as a general concern;
for this validator the invocation is a runtime call from `build_state`, so
the re-import stub fully satisfies it with no ordering subtlety. The stub
sits at the original definition position regardless, matching the
legacy-epic convention.

## Monkeypatch / identity audit

Grep before cut: `tests/test_server*.py` + `tests/test_trace_bundle.py`
for `setattr` targets naming any moved symbol. Expected: none patch
`OPS_SCENARIO_PROFILES` / `_profile` / `validate_ops_profiles` directly
(they are data + a fail-fast validator). If any test patches
`server.OPS_SCENARIO_PROFILES`, that still bites (server.py alias reads
`_server_ops.OPS_SCENARIO_PROFILES`, which the re-import stub rebinds to
the leaf object — same identity). Record the grep result in implement.md.

## Affected files

- `src/anomaly_metric_creator/server_ops_profiles.py` (new leaf)
- `src/anomaly_metric_creator/server_ops.py` (delete block + re-import stub;
  keep the six names in `__all__`)
- `tools/check_mypy_gate.py` (add the new module to `CLEAN_MODULES`)
- `tests/test_mypy_gate_lint.py` (bump the expected count 23 → 24)
- `CLAUDE.md` server-mode module map
- `.trellis/spec/amc/backend/architecture.md` module map
- `CHANGELOG.md` (Unreleased; internal refactor note)

Never: the three facades, `server.py`'s alias block, `server_mcp.py`
imports.

## Boundaries / non-goals

- Zero behavior change; the fuzz corpus + server tests + a before/after
  `run_command` stdout byte-diff are the oracle (no golden hashes on this
  surface).
- Only `server_ops_profiles.py` (new), `server_ops.py` (delete + stub),
  CLAUDE.md, and `architecture.md` change. Never the facades,
  `server.py`'s alias block, or `server_mcp.py` imports.

## Risks / edge cases

- **Splice hazard:** after the cut, grep the vacated range and its
  neighbours in `server_ops.py` for stray `^from \.` re-imports or a
  half-cut dataclass; confirm the file parses and every prior leaf
  re-import still resolves.
- **Module size:** the moved block is ~775 lines, dominated by the
  `OPS_SCENARIO_PROFILES` data. If `server_ops_profiles.py` lands >800,
  invoke the PRD's data-registry exemption explicitly (as
  `scenario_catalog.py` does) rather than splitting the registry.

## Validation

```bash
.venv/bin/pytest tests/test_server.py tests/test_server_ops_fuzz.py \
  tests/test_server_mcp.py tests/test_server_eval_mode.py -n 0
.venv/bin/pytest        # full suite
.venv/bin/python tools/check_mypy_gate.py   # gated modules clean
.venv/bin/ruff check src tests
```

Plus the before/after `run_command` byte-diff scratch script over the
fixed command set named in the PRD acceptance criteria.

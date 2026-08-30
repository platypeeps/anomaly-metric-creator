# Design — extract command-render primitives + CommandResult (epic 07-06 precursor)

Executes one extraction PR under epic `07-06-server-ops-decomposition`'s
`design.md` per-step process (verbatim move, one-way import, re-import stub at
the conceptual position, splice-hazard grep, render-oracle diff). Grounded in
`research/precursor-closure-audit.md` (verified against `main`, server_ops.py
5,590 lines).

## Decision 1 — NEW leaf `server_command_render.py` (not extend server_ops_support)

The task slug (`…-support-render-primitives`) hinted at extending
`server_ops_support.py`. **Rejected.** `_unsupported` constructs
`CommandResult`, forcing `CommandResult` to co-move; `_is_dry_run`/`_unsupported`
need `ParsedCommand` from `server_ops_parse`. `server_ops_support.py`'s docstring
charter is verbatim *"stdlib + `server_mutations`-only … snapshot-row / timestamp
/ string-coercion accessors"*, and it is consumed by the two k8s leaves that
never touch `CommandResult`. Parking a command dataclass + a `server_ops_parse`
import there breaks the honest-single-purpose-description gate CLAUDE.md frames
as the extraction test. A purpose-named new leaf keeps every leaf description
honest and gives the future `server_helm_impl.py` one clean import source. Both
options are acyclic; single-purpose is the deciding factor. (Naming tension with
the task slug is intentional and recorded here — the task title stays; the module
is `server_command_render.py`.)

## Decision 2 — `_format_dt` is re-imported from `server_mutations`, not moved

`server_ops.py:3589–3590` `_format_dt` is **byte-identical** to
`server_mutations.py:16–17`. `server_mutations` still needs its own copy (uses it
internally). Closure: **delete the server_ops local copy, re-import
`from .server_mutations import _format_dt as _format_dt`** at the conceptual
position. Removes a real duplicate; the new render leaf also re-imports it from
`server_mutations` rather than adding a third body. `_format_dt` therefore does
**not** live in `server_command_render.py`.

## Move-set (verbatim, from server_ops.py on main)

| Symbol | Span | Destination | Runtime dep |
|--------|------|-------------|-------------|
| `CommandResult` (frozen dataclass) | 156–162 | `server_command_render.py` | none |
| `_is_dry_run` | 491–497 | `server_command_render.py` | `ParsedCommand` (server_ops_parse) |
| `_unsupported` | 686–693 | `server_command_render.py` | `CommandResult` (co-moved) + `ParsedCommand` |
| `_exposed_active_scenarios` | 3379–3393 | `server_command_render.py` | `SimulationState` — **TYPE_CHECKING annotation-only** |
| `_table` | 3571–3579 | `server_command_render.py` | none |
| `_format_dt` | 3589–3590 | **stays in server_mutations**; server_ops re-imports it | none |

`_exposed_component_scenarios` / `_component_scenarios` do **NOT** move — no moved
symbol calls them (`_exposed_active_scenarios` body is
`return () if state.eval_mode else state.active_scenarios`, no sibling call).

## New leaf shape

```python
# server_command_render.py
"""Command-render primitives + the CommandResult dataclass.

Pure leaf below server_ops: stdlib + server_ops_parse (ParsedCommand) +
server_mutations (_format_dt re-export). Holds the CommandResult return type
and the general render/command helpers (_table, _is_dry_run, _unsupported,
_exposed_active_scenarios) that the command renderers and the future
server_helm_impl leaf share. Never imports server_ops (one-way rule)."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING
from .server_ops_parse import ParsedCommand
from .server_mutations import _format_dt as _format_dt   # re-export; byte-identical
if TYPE_CHECKING:
    from .server_ops import SimulationState
# CommandResult, _table, _is_dry_run, _unsupported, _exposed_active_scenarios (verbatim)
```

DAG additions (both acyclic — parse/mutations never import the render leaf):
`server_mutations → server_command_render`, `server_ops_parse →
server_command_render`. Peer of `server_ops_support` / `server_ops_parse`, below
`server_ops`. Updated DAG line for docs:
`server_mutations → {server_ops_support, server_ops_parse → server_command_render} → server_k8s_objects → server_k8s_tables`.

## server_ops.py re-import stub (at line 156, anchor exactly)

**Splice hazard:** the `from .server_ops_parse import (…)` re-import block sits
at **133–153**, only blank lines above the `@dataclass` at 156. Cut the
CommandResult removal starting at **156**, never earlier, or the parse re-import
is swept up. Replace the CommandResult def with:

```python
from .server_command_render import (
    CommandResult as CommandResult,
    _table as _table,
    _is_dry_run as _is_dry_run,
    _unsupported as _unsupported,
    _exposed_active_scenarios as _exposed_active_scenarios,
)
from .server_mutations import _format_dt as _format_dt   # drop local copy at 3589-3590
```

The `_table` / `_exposed_active_scenarios` / `_format_dt` original defs at
3379–3393 / 3571–3579 / 3589–3590 and `_is_dry_run` / `_unsupported` at
491–497 / 686–693 are deleted (their names now resolve via the stub at 156).

## Compatibility surface — resolves unchanged (audit §4/§5)

- `server.py:304` `CommandResult = _server_ops.CommandResult`, `server_commands.py:7`
  `from .server_ops import (… CommandResult …)`, and the `test_server.py:2648`
  identity assertion (`server.CommandResult is commands.CommandResult`) all resolve
  through the single re-import to the one leaf-defined class — identity preserved.
- All ~90 intra-`server_ops` `CommandResult(...)` constructions + every intra-module
  call of the 5 helpers resolve in the `server_ops` namespace via the stub
  (CLAUDE.md intra-module rule).
- `__all__` unchanged: `CommandResult`/`_unsupported`/`_exposed_active_scenarios`/
  `_table`/`_format_dt` stay listed (re-import binds them); `_is_dry_run` is not in
  `__all__` (intra-module only) — no `__all__` edit.
- **No monkeypatch/setattr** targets any of the 6 symbols (audit §5) — no
  patch-namespace hazard.

## mypy gate

Add `src/anomaly_metric_creator/server_command_render.py` to
`tools/check_mypy_gate.py` `CLEAN_MODULES`. No `var-annotate`/type gap in the six
symbols (audit §7). `--follow-imports=silent` means the TYPE_CHECKING
`server_ops.SimulationState` import does not leak server_ops's baseline errors.

## Behavior identity — render oracle

Verbatim move → `render_command` output byte-identical. Build a scratch oracle
(epic step 0): run the audit §8 command list through `run_command` on `main`,
capture the `CommandResult` tuple per command, re-assert byte-identical after.
Command list covers every moved helper incl. the eval-mode
`_exposed_active_scenarios` empty path. Existing `tests/test_server.py` +
`tests/test_server_eval_mode.py` already pin most; the scratch check is the
explicit before/after net.

## Boundaries / non-goals

- No `server_helm_impl.py` extraction (next task).
- No `SimulationState`/`CommandResult`-sibling moves beyond the named set.
- No renderer/dispatch reorg (epic step 6). No `server.py`/facade edits.

## Validation

```bash
.venv/bin/pytest tests/test_server.py tests/test_server_ops_fuzz.py \
  tests/test_server_mcp.py tests/test_server_eval_mode.py -n 0
.venv/bin/pytest                       # full suite
.venv/bin/python3 tools/check_mypy_gate.py
.venv/bin/ruff check src/anomaly_metric_creator/server_command_render.py \
  src/anomaly_metric_creator/server_ops.py
python3 <scratch>/render_oracle.py --check   # before==after over §8 list
```

## Rollback

Single-PR, additive leaf + re-import stub. Revert = restore the six defs in
server_ops.py, delete the leaf, drop the gate line. No data/schema/format
change; all locked hashes untouched (server surface has none).

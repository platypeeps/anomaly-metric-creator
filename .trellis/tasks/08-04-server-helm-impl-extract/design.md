# server_helm_impl.py extraction — Design (epic step 3)

Executes step 3 of `07-06-server-ops-decomposition` under that epic's
`design.md` rules (verbatim move, one-way import, re-import stub at the
conceptual position, splice-hazard grep, render-oracle diff).

## Blocker resolved — Option A realized (2026-08-04)

The planning-phase closure audit (below) found helm could not be a clean
one-way leaf while it called **7 non-helm primitives + 2 constants** still
defined in `server_ops.py`. The recommended **Option A (resequence)** has now
shipped: every one of those symbols lives in a lower leaf, verified against
`server_ops.py` @ 5,540 lines:

| blocker symbol | now defined in |
|---|---|
| `_table`, `_is_dry_run`, `_unsupported`, `_exposed_active_scenarios`, `CommandResult` | `server_command_render.py` (precursor PR #331) |
| `_format_dt` | `server_mutations.py` |
| `_k8s_metadata`, `_k8s_timestamp` | `server_k8s_objects.py` (step 4, PR #327) |
| `DEFAULT_RELEASE`, `DEFAULT_CHART` | `server_ops_support.py` (step 4, PR #327) |

Helm is now the natural top leaf: it imports these one-way and nothing imports
helm except `server_ops` (which re-imports every moved name). No synthetic
module, no callback seam.

## Move-set (verified against server_ops.py @ 5,540 lines)

20 helm symbols in **4 contiguous blocks** (no interleaved non-helm code):

- **Block 1** `625–675` — `_render_helm` (dispatcher).
- **Block 2** `2918–3160` — `_render_helm_list`, `_render_helm_status`,
  `_render_helm_history`, `_render_helm_env`, `_render_helm_get`,
  `_render_helm_test`, `_render_helm_install`, `_render_helm_upgrade`,
  `_helm_value_overrides`, `_helm_operation_note`, `_render_helm_rollback`.
- **Block 3** `3465–3496` — `_helm_release`, `_helm_notes`,
  `_helm_current_description`.
- **Block 4** `5000–5104` — `_helm_secret_objects`,
  `_helm_release_revisions`, `_helm_secret_object`,
  `_helm_encoded_release_data`, `_helm_release_payload`.

Combined body ~510 lines — comfortably under the 800-line cap.

## Closure (external references, all one-way)

Every helm symbol references only: other move-set symbols, stdlib
(`gzip`/`json`/`base64`), or lower-leaf names:

- `server_command_render`: `CommandResult`, `_unsupported`, `_table`,
  `_exposed_active_scenarios`, `_is_dry_run`.
- `server_mutations`: `_format_dt`.
- `server_k8s_objects`: `_k8s_metadata`, `_k8s_timestamp`.
- `server_ops_support`: `DEFAULT_RELEASE`, `DEFAULT_CHART`.
- `server_ops_parse`: `_flag_values`, `_first_flag_value`.

`SimulationState` appears only in signatures; with `from __future__ import
annotations` it is stringized, so a `TYPE_CHECKING`-only import (the epic's
sanctioned k8s-leaf pattern) suffices — no runtime reverse import.

Import verdict: **clean one-way, 0 blockers.**

## Leaf import block (`server_helm_impl.py`)

```python
from __future__ import annotations
import base64, gzip, json
from typing import TYPE_CHECKING, Any
from .server_command_render import (
    CommandResult, _exposed_active_scenarios, _is_dry_run, _table, _unsupported,
)
from .server_k8s_objects import _k8s_metadata, _k8s_timestamp
from .server_mutations import _format_dt
from .server_ops_parse import ParsedCommand, _first_flag_value, _flag_values
from .server_ops_support import DEFAULT_CHART, DEFAULT_RELEASE
if TYPE_CHECKING:
    from .server_ops import SimulationState
```

The exact stdlib/`typing`/`ParsedCommand` set is confirmed at implement time by
grepping the moved bodies; unused imports are dropped (ruff F401).

## Re-import stub (in `server_ops.py`)

`server_ops.py` re-imports every moved name at each block's original position
so `server_ops.<name>` and `server_ops.__all__` stay stable. Four non-helm
callers keep resolving through the re-import:

| caller (stays in server_ops) | moved name it calls |
|---|---|
| `render_command` @ ~498 | `_render_helm` |
| `resource_snapshot` @ ~918 | `_helm_release_revisions` |
| `resource_snapshot` @ ~936 | `_helm_release` |
| `_k8s_objects_for_resource` @ ~4907 | `_helm_secret_objects` |

The Helm Secret REST-object path (`_k8s_objects_for_resource`) and the
`server_helm.py` / `server_commands.py` facades read the re-imported names —
no facade or `server.py` alias-block edit.

## DAG

```
server_mutations ─┬─ server_ops_support ─ server_k8s_objects ─┐
                  ├─ server_ops_parse ─────────────────────────┤
                  └─ server_command_render ────────────────────┴─ server_helm_impl ─ server_ops
```

`server_helm_impl` imports the five leaves above; only `server_ops` imports
`server_helm_impl` (the allowed direction).

## Splice hazard

Prior extractions left `from .` re-import stubs at block boundaries. After each
of the 4 cuts, grep the removed range for a swept-up `^from \.` re-import and
confirm every leaf re-import still resolves (the epic's documented rule). Cut
from the highest line block downward (Block 4 → 1) so earlier line numbers
stay valid during the edit.

## Behavior identity (oracle)

`helm_oracle.py` (scratchpad) drives `run_command` over an 18-command helm
corpus (list/status/history/get values/get notes/get manifest/test/show/
install/upgrade/rollback + unsupported verb, reads and real mutations) in
**normal and `--mcp-eval-mode`** states, plus a direct dump of
`_helm_secret_objects` (the double-base64 gzip release payload bytes) pre- and
post-mutation. `helm_before.json` captured on this branch tip pre-move;
`helm_after.json` must be byte-identical (`cmp`). This covers AC "render-oracle
byte-identical" and "Secret payload bytes byte-identical".

## Validation

- One-way runtime grep on the leaf → only the `TYPE_CHECKING` `SimulationState`
  line.
- `server_ops.<name> is server_helm_impl.<name>` identity for all 20.
- Targeted suites `-n 0`: `test_server`, `test_server_ops_fuzz`,
  `test_server_mcp`, `test_server_eval_mode`; then full suite.
- mypy clean gate: add `server_helm_impl.py` if it lands clean (close any
  verbatim-inherited `var-annotate` gap first, per the tables precedent).
- Docs: CLAUDE.md server-module map + `architecture.md` DAG; epic
  `implement.md` step-3 done + measured `server_ops.py` size.

## Non-goals

Unchanged from `prd.md`: no non-helm renderer/snapshot/k8s-object moves, no
behavior/byte change, no new dependency, no facade / `server.py` alias-block /
`server_mcp.py` edits.

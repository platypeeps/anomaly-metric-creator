# Extract server_ops_explain.py + server_ops_payloads.py — Design

## Overview

Two verbatim extractions out of `server_ops.py` (4,687 lines) into new pure
leaf modules, under the epic's role-swap rule: code moves *out*,
`server_ops.py` re-imports every moved name at the original block position,
and the new modules never import `server_ops`.

Both clusters were selected by a read-only AST closure audit rather than by
reading names, because the epic's planned step 6 boundary turned out to be
unreachable (see `prd.md` § Why step 6 cannot be taken as designed). The
audit computed, for each seed, the transitive set of `server_ops` module
globals it reaches and the set of outside definitions that reference the
closure. Only clusters whose closure contains **no** `SimulationState` and
**no** `resource_snapshot` are movable without a seam.

Measured closures:

| Seed set | Defs | Lines | Module data | Reaches state? |
| --- | --- | --- | --- | --- |
| explain formatters (4 seeds) | 10 | ~140 | none | no |
| `_apply_json_patch` | 4 | ~49 | none | no |
| `_load_manifest_documents` | 2 | ~90 | none | no |
| `resource_snapshot` (rejected) | 22 | 783 | 4 | yes — is the state |

The `Lines` column counts **definitions only**. The rejected closure is 22
definitions totalling 783 lines, plus at least 4 module-level constants —
`_REFUSAL_KINDS` (`server_ops.py:304`), `_NAMESPACED_SNAPSHOT_KINDS`
(`:591`), `_DEPLOYMENT_STATUS_PRIORITY` (`:3168`), and
`_POD_STATUS_PRIORITY` (`:3239`) — whose own constant-to-constant
dependencies (e.g. `_NAMESPACED_SNAPSHOT_KINDS` is derived from
`_SNAPSHOT_KINDS` and `_CLUSTER_SCOPED_SNAPSHOT_KINDS`) the audit does not
walk. The constant count is therefore a lower bound; the rejection does not
depend on its exact value.

## Proposal

### Leaf 1 — `server_ops_explain.py`

One contiguous source block, `server_ops.py:1944-2101`, moved unchanged:

```
_openapi_schema_from_value        1944-1975
_explain_field_description        1978-1982
_explain_title                    1985-1991
_explain_schema_at_path           1994-2005
_format_explain                   2008-2038
_format_recursive_explain_fields  2041-2064
_explain_properties               2067-2070
_explain_display_schema           2073-2078
_explain_type_label               2081-2087
_explain_type_name                2090-2101
```

Imports in the leaf: `from __future__ import annotations` and
`from typing import Any`. **No** intra-package import — this is the first
fully package-independent leaf in the epic.

`server_ops.py` keeps `_render_explain` (:1843) and
`_explain_schema_for_kind` (:1905), which bind `SimulationState` and reach
`resource_snapshot` through `_minimal_k8s_object`. They continue to call
`_explain_schema_at_path`, `_format_explain`, and
`_openapi_schema_from_value` through the re-import stub placed where the
moved block sat (immediately after `_minimal_k8s_object`).

`_EXPLAIN_RESOURCE_DESCRIPTIONS` (`server_ops.py:594`) stays, and
`_EXPLAIN_RESOURCE_TARGETS` is already re-imported from
`server_ops_profiles` (`server_ops.py:137`) — neither moves. The audit
shows the moved 10 never read either one, and `tests/test_server.py:586`
does `monkeypatch.setitem(server._server_ops._EXPLAIN_RESOURCE_TARGETS,
…)`, so that name must keep resolving in `server_ops`'s namespace.

### Leaf 2 — `server_ops_payloads.py`

Two contiguous source blocks, moved unchanged, in source order:

```
_apply_json_patch                 2516-2532
_json_pointer_parts               2535-2540
_set_json_pointer                 2543-2554
_remove_json_pointer              2557-2570

_load_manifest_documents          2705-2758
_normalize_manifest_documents     2761-2796
```

Imports in the leaf: `from __future__ import annotations`, `import json`,
`from pathlib import Path`, `from typing import Any`, and
`from .server_command_render import CommandResult`. The `yaml` import stays
lazy and in-function exactly as today (it is an optional dependency whose
`ImportError` is a rendered `CommandResult`, not a crash).

`server_ops.py` gets **two** re-import stubs, one at each block's original
position, so `_patch_payload` (:2442) and `_manifest_apply_targets` (:2693)
keep resolving their callees in `server_ops`'s namespace.

### Resulting DAG

Arrows read **"is imported by"** — they point from a lower leaf up to its
consumer, i.e. the direction dependency flows *into*:

```
server_command_render ──> server_ops_payloads ──┐
                                                 ├─> server_ops (re-imports)
server_ops_explain (no package imports) ────────┘
```

Equivalently, as import statements: `server_ops` imports both new leaves,
and `server_ops_payloads` imports `CommandResult` from
`server_command_render`. Neither new leaf imports `server_ops` — the
one-way rule holds. No existing leaf imports either new module, so no
other leaf's import block changes.

## Boundaries And Non-Goals

- Zero behavior change: no renames, no signature edits, no reordering
  inside a moved block, no type-annotation "improvements" during the move.
- `server_ops.__all__` is not edited (byte-unchanged, verified by diff).
- The compatibility surface is not touched: `server.py`'s alias block, the
  three facades, `server_mcp.py`.
- The `resource_snapshot` provider seam is **not** designed here.
- No test is rewritten to accommodate the move; if a test would need
  editing, that is evidence the cut is wrong.

## Data And Command Contracts

No command, HTTP, or MCP contract changes. The affected user-visible
surfaces are `kubectl explain`, `kubectl patch`, and `kubectl apply -f`
output bytes, which the render oracle pins.

## Risks And Edge Cases

- **Splice hazard.** A prior extraction's re-import stub can sit inside a
  cut range. Both ranges must be grepped for `^from \.` before deletion,
  and every leaf re-import re-verified afterward (CLAUDE.md invariant).
- **Monkeypatch bite.** Only two `server_ops` names are patched by tests
  (`resource_snapshot`, `_EXPLAIN_RESOURCE_TARGETS`); neither is in either
  moved set, and neither moved function is itself patched. Re-verify
  before cutting by grepping `tests/` for the 16 **moved symbol names**,
  not for `setattr(` lines — a patch target can sit on the line after the
  call (`tests/test_server.py:585-586`), so a call-site grep cannot prove
  absence. Exact command in `implement.md` step 1.
- **Orphaned imports.** The manifest block is the only in-module user of
  some `json`/`Path` paths; `server_ops.py` uses both elsewhere, so
  neither import should be removed — verify with `ruff` rather than by
  eye, and remove only what `ruff` reports unused.
- **Optional `yaml`.** `_load_manifest_documents` renders a specific
  `CommandResult` when PyYAML is absent (`server_ops.py:2722`). PyYAML is
  a dev dependency (`pyproject.toml:50`), so that `ModuleNotFoundError`
  branch is **not reachable** in the dev environment and the oracle cannot
  cover it — the `.yaml` corpus entries exercise successful parse and
  malformed-YAML instead. The branch is moved verbatim and no test
  currently references it; recorded as a follow-up rather than claimed as
  covered.
- **Cap compliance.** Both leaves land far under 800 lines; no split
  contingency needed.

## Validation

1. Frozen-clock render oracle over an explain/patch/apply corpus, captured
   on `main` before the cut and re-run after, diffed byte-for-byte, in
   both normal and `--mcp-eval-mode` states.
2. `.venv/bin/pytest tests/test_server.py tests/test_server_ops_fuzz.py
   tests/test_server_mcp.py tests/test_server_eval_mode.py -n 0`.
3. Full `.venv/bin/pytest`; `.venv/bin/pre-commit run --all-files`.
4. `python3 tools/check_mypy_gate.py` with both leaves added to
   `CLEAN_MODULES` (34 modules).
5. Both `git diff --stat main...HEAD` (committed work) **and**
   `git diff --stat HEAD` (staged + unstaged) show no change to
   `server.py`, `server_commands.py`, `server_kubernetes.py`,
   `server_helm.py`, `server_mcp.py`. Three-dot alone misses the index;
   `git diff` alone misses commits. Exact commands in `implement.md`
   § Validation Plan.

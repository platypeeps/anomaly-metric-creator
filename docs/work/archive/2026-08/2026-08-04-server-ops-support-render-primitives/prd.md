---
title: Extract render-primitives + CommandResult into server_command_render leaf (epic 07-06 precursor to helm)
status: done
created: 2026-08-04
branch: sdelmas/server-command-render-extract
---
# Extract render-primitives + CommandResult (epic 07-06 precursor to helm)

## Parent

Precursor extraction under epic `07-06-server-ops-decomposition`, unblocking
the parked **step 3** (`server_helm_impl.py`). Follows shipped steps 1
(`08-04-server-ops-profiles-extract`, PR #321), 2
(`08-04-server-ops-parse-extract`, PR #323), and 4/5
(`08-04-server-k8s-objects-tables-extract` / `08-04-server-k8s-tables-mypy-gate`,
PRs #327/#328). Executes one extraction PR against the epic's `design.md`
per-step process (verbatim move, one-way import, re-import stub,
splice-hazard grep, render-oracle diff).

## Problem

The parked helm closure audit (`08-04-server-helm-impl-extract`, on branch
`sdelmas/helm-extract-closure-audit`) found the helm cluster cannot become a
one-way leaf while it constructs `CommandResult` and calls five general
render/command primitives that all still live in `server_ops.py`. A re-audit
against current `main` (server_ops.py 5,590 lines) confirms steps 4/5 already
moved four of the original nine blocker symbols
(`_k8s_metadata`/`_k8s_timestamp` -> `server_k8s_objects.py`,
`DEFAULT_RELEASE`/`DEFAULT_CHART` -> `server_ops_support.py`). The **only**
residual runtime coupling is:

- `CommandResult` dataclass — constructed at runtime by helm renderers and by
  `_unsupported`, so it needs a real (not `TYPE_CHECKING`) import from below.
- `_table`, `_format_dt`, `_is_dry_run`, `_unsupported`,
  `_exposed_active_scenarios` — general primitives helm calls.

Moving these into a lower leaf lets helm extract one-way with no epic
resequence.

## Goal

Move `CommandResult` and the five primitives out of `server_ops.py` into a
pure lower leaf (leaf choice fixed by `design.md` from the closure audit —
either extend `server_ops_support.py` or a new sibling leaf), **verbatim**,
with `server_ops.py` re-importing every moved name at its conceptual position.
**Zero** HTTP/command/MCP/Kubernetes-API behavior change; renderer output
bytes identical.

## Non-Goals

- Do NOT extract `server_helm_impl.py` in this task (the next task does that).
- Do NOT move `SimulationState`, or `CommandResult` siblings beyond the named
  set. `_exposed_component_scenarios` / `_component_scenarios` move only if the
  closure audit proves they are required by the moved set and are themselves
  clean one-way.
- No renderer/dispatch reorganization (epic step 6 owns that).
- No `server.py` alias-block or facade edits.

## Acceptance Criteria

- [x] `CommandResult` + `_table` + `_format_dt` + `_is_dry_run` +
      `_unsupported` + `_exposed_active_scenarios` are defined in the chosen
      lower leaf (`server_command_render.py`); the leaf imports **nothing at
      runtime** from `server_ops` (one-way rule holds). The sole
      `from .server_ops import SimulationState` sits under `if TYPE_CHECKING:`
      with `from __future__ import annotations`, so it is stringized and never
      evaluated at runtime — the epic's sanctioned k8s-leaf annotation pattern.
- [x] `server_ops.py` re-imports every moved name at its original conceptual
      position; grep of the moved ranges confirms no swept-up `^from \.`
      re-import block (splice-hazard rule).
- [x] Every existing compatibility consumer still resolves unchanged:
      `server.py` alias block, `server_commands.py` / `server_kubernetes.py` /
      `server_helm.py` facades, `server_mcp.py` imports, and every
      `from .server_ops import CommandResult` / `server_ops.CommandResult`
      site.
- [x] `_format_dt` duplication resolved per the audit (import the existing
      `server_mutations` copy or move the canonical one — no third copy).
- [x] Targeted suites pass `-n 0`:
      `tests/test_server.py tests/test_server_ops_fuzz.py
      tests/test_server_mcp.py tests/test_server_eval_mode.py`.
- [x] Full suite green: `.venv/bin/pytest`.
- [x] Render-oracle byte-identical before/after over the fixed command list
      (`kubectl get pods/deployments/events`, `describe`, `logs`,
      `helm list/status/history`) captured via `run_command`.
- [x] The chosen leaf is in the mypy clean gate (`tools/check_mypy_gate.py`)
      with no new `var-annotate`/type gap; a verbatim-inherited gap is closed
      with an explicit annotation before the leaf joins the gate (mirrors the
      tables `_k8s_node_cells` fix).
- [x] Docs updated in the same PR: CLAUDE.md server module map and
      `.trellis/spec/amc/backend/architecture.md`; the DAG line updated for the
      new/extended leaf's position.
- [x] Measured `server_ops.py` end line count recorded in the PR body and the
      epic `implement.md` step status.
- [x] Pre-PR checklist (all 15 headings) worked before the PR leaves draft.

## Verification (falsifiable)

- One-way runtime import: the only `from .server_ops` line in the leaf must sit
  under `if TYPE_CHECKING:` (the `SimulationState` annotation). Verify with
  `grep -nE 'from \.server_ops|import server_ops' server_command_render.py`
  -> exactly one hit, and it is the `TYPE_CHECKING`-guarded `SimulationState`
  import; any **runtime** (unguarded) hit = fail.
- Compat resolve: import `anomaly_metric_creator.server` and `.server_mcp`
  cleanly, and assert `server_ops.CommandResult is <leaf>.CommandResult`
  (identity preserved through the re-import stub).
- Behavior identity: render-oracle diff over the command list = empty; any
  byte diff = fail.
- Suite: `.venv/bin/pytest` exit 0, 0 new failures vs the pre-change baseline.

## References

Research notes that lived beside this item's Trellis record and were not carried
into docs/work. Recover the bodies from git history under `.trellis/tasks/archive/2026-08/08-04-server-ops-support-render-primitives`:

- research/precursor-closure-audit.md

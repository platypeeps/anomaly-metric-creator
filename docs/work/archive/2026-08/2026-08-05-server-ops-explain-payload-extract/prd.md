---
title: Extract pure explain and payload leaves from server_ops
status: done
created: 2026-08-05
branch: sdelmas/08-05-server-ops-explain-payload-extract
---
# Extract server_ops_explain.py + server_ops_payloads.py (epic step 6a)

## Review context

- **Parent epic:** `07-06-server-ops-decomposition` (decompose the
  originally-7.7k-line `server_ops.py` into focused one-way leaf modules
  using the legacy.py role-swap pattern). This is a **new step 6a**,
  inserted ahead of the epic's planned step 6.
- **Prior steps shipped:** `server_ops_profiles.py` (1),
  `server_ops_parse.py` (2), `server_command_render.py` (3-precursor),
  `server_helm_impl.py` (3), `server_k8s_objects.py` +
  `server_k8s_tables.py` + `server_ops_support.py` (4),
  `server_k8s_api.py` + `server_k8s_api_trace.py` (5).
  `server_ops.py` measures **4,687 lines** at this task's creation.
- **Confidence:** CONFIRMED. A read-only AST closure audit (2026-08-06)
  measured every candidate cluster's transitive module-global closure and
  its external callers before this PRD was written.

## Why step 6 cannot be taken as designed

The epic `design.md` step 6 is "`server_ops_render.py` +
`server_ops_render_workloads.py` (~830 + ~1,000) — render dispatch +
`_render_get`/`_render_describe` in the first; logs/rollout/mutation
renderers in the second."

The closure audit falsifies that shape:

- `resource_snapshot`'s own transitive closure is **22 definitions
  totalling 783 lines, plus at least 4 module-level constants**
  (`_REFUSAL_KINDS`, `_NAMESPACED_SNAPSHOT_KINDS`,
  `_DEPLOYMENT_STATUS_PRIORITY`, `_POD_STATUS_PRIORITY`, whose own
  constant-to-constant dependencies were not walked, so 4 is a lower
  bound). The 783 figure counts definitions only. It reaches
  `SimulationState`, `SimulationClock`,
  `RefusalCounters`, `ContinuousGenerationStatus`, and `_ErrorSink` — the
  runtime dataclasses the epic's own step 7 end state says must **stay**
  in `server_ops.py`. So `resource_snapshot` cannot move down.
- Every renderer the epic named for step 6 calls `resource_snapshot`
  directly: `_render_get` (:1171), `_render_get_all` (:1290),
  `_render_describe` (:1358), `_logs_target_pods` (:1700), `_render_top`
  (:1780), `_render_scale` (:2273), `_render_delete` (:2304),
  `_render_patch` (:2365), `_patch_base_payload` (:2473), `_render_diff`
  (:2586).
- Moving them into a leaf would require `from .server_ops import
  resource_snapshot` — a reverse import, forbidden by the CLAUDE.md
  extraction invariant and by this epic's central role-swap rule.

Resolving that needs a **live provider seam** (the legacy-epic
"named, weak-referenceable live callback" pattern) on a hot render path,
which also changes where `resource_snapshot` monkeypatching bites. That
is a design decision with maintainer-visible tradeoffs and is explicitly
**out of scope here**. It is to be recorded as step 6b in the epic
tracker by this task's `implement.md` step 10; the epic tracker still
carries the original step 6 wording until that lands.

## Goal

Extract the two clusters the audit proved are entirely free of
`SimulationState` and `resource_snapshot` into two new one-way pure leaf
modules, with **zero** command/HTTP/MCP behavior change and **zero** edits
to the compatibility surface (`server.py` alias block, the three facades,
`server_mcp.py`):

1. **`server_ops_explain.py`** — the 10 pure explain / OpenAPI-schema
   formatters occupying the contiguous block `server_ops.py:1944-2101`:
   `_openapi_schema_from_value`, `_explain_field_description`,
   `_explain_title`, `_explain_schema_at_path`, `_format_explain`,
   `_format_recursive_explain_fields`, `_explain_properties`,
   `_explain_display_schema`, `_explain_type_label`, `_explain_type_name`.
   Closure: 10 defs, ~140 lines, **zero** module-level data, stdlib +
   `typing.Any` only.
2. **`server_ops_payloads.py`** — the declarative payload-handling pair of
   blocks: the JSON-pointer patch ops `server_ops.py:2516-2570`
   (`_apply_json_patch`, `_json_pointer_parts`, `_set_json_pointer`,
   `_remove_json_pointer`; 4 defs, ~49 lines) and the manifest document
   reader `server_ops.py:2705-2796` (`_load_manifest_documents`,
   `_normalize_manifest_documents`; 2 defs, ~90 lines).

## Requirements

1. Code moves **verbatim**. No renames, no signature changes, no
   reordering within a moved block, no opportunistic cleanups.
2. `server_ops.py` re-imports every moved name at that block's original
   conceptual position, so `server_ops.<name>` keeps resolving.
3. New modules never import `server_ops`. `server_ops_explain.py` imports
   nothing from the package. `server_ops_payloads.py` imports only
   `CommandResult` from `server_command_render` (a lower leaf).
4. `server_ops.__all__` stays byte-unchanged.
5. Both leaves join the `tools/check_mypy_gate.py` clean-module list.
6. Both leaves stay under the 800-line behavior-module cap (they will:
   ~140 and ~150).
7. Any import orphaned by the cut is removed from `server_ops.py`; the
   splice-hazard grep (`^from \.` inside every deleted range) runs before
   the cut is accepted.
8. CLAUDE.md module-ownership map and
   `.trellis/spec/amc/backend/architecture.md` § Module Boundaries record
   both new leaves and the updated DAG in the same PR.
9. The epic `implement.md` records this step, its measured end size, and
   the step 6b seam decision it defers.

## Acceptance criteria

- [x] `server_ops.py` shrinks from 4,687 to ~4,400 lines; both new leaves
      measured and recorded in the PR body. **Measured:** `server_ops.py`
      4,414; `server_ops_explain.py` 178; `server_ops_payloads.py` 172.
      Recorded in PR #344.
- [x] A frozen-clock render-oracle diff over a fixed command corpus that
      exercises `kubectl explain` (plain, `--recursive`, field-path
      forms), `kubectl patch` (JSON-patch and merge-patch), and
      `kubectl apply -f` (`.json`, `.yaml`, missing file, malformed
      document) is **byte-identical** before and after, in both normal and
      `--mcp-eval-mode` states. **Byte-identical over a 72-record corpus.**
- [x] `.venv/bin/pytest tests/test_server.py tests/test_server_ops_fuzz.py
      tests/test_server_mcp.py tests/test_server_eval_mode.py -n 0` passes.
      **178 passed, 2 skipped.**
- [x] Full `.venv/bin/pytest` and `.venv/bin/pre-commit run --all-files`
      pass. **1797 passed, 2 skipped; pre-commit 13/13.**
- [x] `python3 tools/check_mypy_gate.py` passes with both leaves listed.
      **`Success: no issues found in 34 source files`.**
- [x] No diff to `server.py`, `server_commands.py`, `server_kubernetes.py`,
      `server_helm.py`, or `server_mcp.py`. **`git diff origin/main...HEAD`
      over those five paths is empty.**

## Non-goals

- The `resource_snapshot` provider seam and the render-dispatch split
  (epic step 6b) — design decision deferred, tracked in the epic.
- `_render_explain` and `_explain_schema_for_kind` themselves: both bind
  `SimulationState`/`resource_snapshot` and **stay** in `server_ops.py`,
  calling the moved formatters through the re-import stub.
- `_manifest_apply_target` / `_manifest_apply_targets`: both bind
  `SimulationState` and stay.
- Any per-kind descriptor collapse or `server.py` decomposition.

## Known decisions

- **D1.** Two leaves in one PR rather than two PRs: each is ~150 lines,
  both are pure verbatim moves with no shared seam, and one render-oracle
  run covers both corpora. Mirrors the step-4 precedent
  (`server_k8s_objects` + `server_k8s_tables`, one PR).
- **D2.** `server_ops_payloads.py` groups JSON-pointer patch ops with the
  manifest document reader because both are declarative request-payload
  handling with no simulation-state binding. Named for the shared
  concern, not for either call site.

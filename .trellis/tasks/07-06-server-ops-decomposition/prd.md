# Decompose the oversized server_ops.py

## Review context

- **Source:** deep-dive architecture review, 2026-07-06.
- **Confidence:** CONFIRMED (structural fact; cluster map measured).
- **Severity:** HIGH as long-term maintainability debt — with the legacy.py
  epic landed (`legacy.py` is now 766 lines, under the cap), this is the
  repo's largest file.
- **Category:** design / architecture. **Epic** — same shape as
  `07-02-legacy-monolith-decomposition`.

## Goal

Break `server_ops.py` into focused modules using the extraction pattern
proven by the legacy.py epic, without changing any HTTP/command/MCP
behavior.

**Progress.** At the 2026-07-06 review the file was 7,662 lines, 9.6× the
800-line cap. Eight child extractions have since landed and it is **4,414
lines, 5.5× the cap** — 42% paid down, six leaves published
(`server_ops_profiles.py` 791, `server_ops_parse.py` 566, `server_ops_explain.py`
178, `server_ops_payloads.py` 172, `server_ops_support.py` 87, plus the
`server_k8s_*` / `server_helm_impl` / `server_command_render` leaves). The
epic stays open until the file is under the cap.

## Problem

**The original cluster map below is historical.** It described the file as
it stood on 2026-07-06 and its line ranges no longer resolve — roughly 3.2k
lines have moved out from under them. Do not plan a cut from it. It is kept
because it records what the epic set out to move and in what proportions.

> The `OPS_SCENARIO_PROFILES` data registry (~660 lines, :132-792), runtime
> dataclasses + `SimulationState` (~250), command parsing (~390, :1298-1687),
> render dispatch + `_render_get`/`_render_describe` (~830), logs/rollout/
> mutation renderers (~1,000), Helm renderers (~240), the Kubernetes REST
> facade + OpenAPI + kubeconfig (~710, :4886-5600), k8s Table rendering
> (~450), k8s object builders (~500), Helm Secret encoding + metrics (~130),
> and shared helpers (~460).

**Current map, measured 2026-08-07** by walking the module AST (108
top-level defs/classes spanning 3,473 of the 4,414 lines; the first
definition starts at `:71`, everything above it is imports and the leaf
re-import block). Re-measure before planning a cut rather than trusting
these ranges — that is the mistake this section is correcting:

| Lines | Cluster | Largest members |
| ---: | --- | --- |
| ~1,134 | render dispatch + renderers | `_render_describe` :1356-1636 (281), `_render_get` :1170-1286 (117), `_render_kubectl` :721-825 (105) |
| ~277 | manifest / JSON-Patch, state-bound | `_render_patch` :2198-2255, `_patch_payload` :2258-2310 |
| ~269 | classes / dataclasses | `SimulationState` :358-485 (128), `SimulationClock` :71-123 (53) |
| ~51 | explain / schema, state-bound | `_render_explain` :1843-1902 |
| ~1,742 | everything else | `resource_snapshot` :831-1100 (270), `kubernetes_api_mutating_response` :3491-3682 (192), `_generic_resource_row` :2636-2747 (112), `_k8s_group_resource_response` :3789-3863 (75), `_openapi_paths` :3380-3447 (68) |

The two structural facts the epic still has to answer are visible in that
table: render dispatch is the largest surviving cluster and step 6b was
already found not implementable as designed, and `resource_snapshot` (270
lines) is the state-bound spine that every extracted leaf reads through, so
it anchors what can leave.

The same resource families remain hand-maintained in four parallel places
(command-mode table, REST table, REST object, snapshot rows) — the lockstep
burden CLAUDE.md already documents. The only prior task naming this file
(`07-02-audit-server-ops-rendering`, completed) was a robustness audit,
explicitly not a split.

## Requirements

- Write a `design.md` first (mirroring the legacy epic): boundaries,
  sequencing (data-first — `server_ops_profiles.py` is a pure-data leaf
  and the natural step 1), and a compatibility inventory: the
  `server_commands.py` / `server_kubernetes.py` / `server_helm.py`
  facades, `server.py`'s `NAME = _server_ops.NAME` alias block — **227
  names** as of 2026-08-07 (31 public, 196 private), at
  [server.py:309](src/anomaly_metric_creator/server.py:309)-535, up from the
  40+ recorded at review time — and `server_mcp.py`'s imports must all keep
  resolving. That the block is 86% private names is itself a design signal:
  most of what crosses this seam is not public API.
- Candidate boundaries (validate in design): `server_ops_profiles.py`
  (OPS_SCENARIO_PROFILES + validate_ops_profiles), `server_ops_parse.py`
  (parse_command/_split_flags/flag tables), `server_ops_render.py`
  (command renderers), `server_k8s_api.py` (REST facade + OpenAPI +
  kubeconfig), `server_k8s_objects.py` (table/object builders),
  `server_helm_impl.py` (helm renderers + Secret encoding).
- One module per PR; behavior tests (`tests/test_server.py`,
  `tests/test_server_ops_fuzz.py`, `tests/test_server_mcp.py`) must pass
  unchanged apart from design-sanctioned import-path retargets.
- Non-goal for this epic (record as candidate follow-up): collapsing the
  four parallel per-kind rendering surfaces into a single per-kind
  descriptor — a behavior-affecting refactor to design separately once
  the split has made the duplication visible.
- Adjacent seam to settle in design (in or out of scope): `server.py`
  (**2,190 lines** as of 2026-08-07 — it has *grown* by ~400 since the
  review while `server_ops.py` shrank) mixes bounded-server infrastructure,
  HTTP dispatch, and `serve_main` CLI, and its manual alias block must be
  extended for every new ops name. That block has scaled with the epic —
  40+ names at review time, 227 now — so the case for an explicit re-export
  module or `__getattr__` delegation is stronger than when this was first
  written, not weaker. Step 1 has passed; settle it before the next
  extraction.

## Acceptance Criteria

- [ ] `design.md` with boundaries/sequencing/compatibility inventory lands
      before any extraction PR.
- [ ] Each extraction PR keeps all server-family tests green.
- [ ] Facades and the `server.py` re-export surface unchanged (identity
      tests keep passing).
- [ ] Each new module < 800 lines, or a recorded data-registry exemption
      (profiles data).
- [ ] CLAUDE.md and `.trellis/spec/amc/backend/architecture.md` module
      maps updated in each PR.

## Child Tasks

- `08-04-server-ops-profiles-extract` — step 1: extract the ops
  scenario-profile registry and its dataclasses/builders/validator into
  the pure-data leaf `server_ops_profiles.py`.
- `08-04-server-ops-parse-extract` — step 2: extract the client-command
  parse cluster (`ParsedCommand`, flag/alias tables, `parse_command` +
  `_parse_kubectl`/`_parse_helm`, fingerprint/redaction helpers) into the
  stdlib-only leaf `server_ops_parse.py`.
- `08-04-server-k8s-objects-tables-extract` — step 4 (resequenced ahead of
  step 3 helm, with maintainer consent): extract the per-kind Kubernetes
  object builders into `server_k8s_objects.py` and the `meta.k8s.io/v1` Table
  surface into `server_k8s_tables.py`, plus the shared lower leaf
  `server_ops_support.py` that both consume downward (maintainer Option A).
- `08-04-server-k8s-tables-mypy-gate` — follow-up to the step-4 task: annotate
  the one verbatim-moved `var-annotate` gap in `server_k8s_tables.py` and add
  the module to the mypy clean-module gate.
- `08-04-server-ops-support-render-primitives` — step-3 precursor: extract
  `CommandResult` and the general render/command primitives
  (`_table`/`_is_dry_run`/`_unsupported`/`_exposed_active_scenarios`) into the
  new pure leaf `server_command_render.py` and dedupe `_format_dt` onto
  `server_mutations`, so the later helm extraction imports them one-way without
  an epic resequence.
- `08-04-server-helm-impl-extract` — step 3: extract the 20 helm symbols
  (renderers, release/notes model, and the double-base64 gzip Secret encoders)
  into the top helm leaf `server_helm_impl.py`, importing one-way from the five
  lower leaves and imported only by `server_ops`.
- `08-05-server-k8s-api-extract` — step 5: extract the pure Kubernetes
  REST-facade builder/filter/format layer into `server_k8s_api.py` (with the
  `_api_*` trace/redaction sink carved into `server_k8s_api_trace.py` for the
  800-line cap); the `resource_snapshot`-bound dispatch spine stays in
  `server_ops.py`.
- `08-05-server-ops-explain-payload-extract` — step 6a, inserted ahead of the
  planned step 6 after a closure audit showed the render-dispatch split is not
  implementable as designed: extract the ten pure `kubectl explain` / OpenAPI
  schema formatters into `server_ops_explain.py` (the epic's first leaf with no
  intra-package import) and the RFC 6902 JSON Patch ops (RFC 6901 pointer
  paths) plus the manifest document reader into `server_ops_payloads.py`. The state-bound
  `_render_explain` / `_explain_schema_for_kind` and `_manifest_apply_target(s)`
  stay in `server_ops.py`; the blocked render-dispatch split becomes step 6b.

## Notes

- Epic — break into child tasks once design.md fixes boundaries; do not
  attempt in one session.
- Reuse the legacy epic's hard-won rules: verbatim moves, one-way imports,
  re-import stubs at the same conceptual location, splice-hazard grep
  after every cut, move-with-callers for monkeypatched names.

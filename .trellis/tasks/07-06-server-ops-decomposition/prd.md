# Decompose the 7.7k-line server_ops.py

## Review context

- **Source:** deep-dive architecture review, 2026-07-06.
- **Confidence:** CONFIRMED (structural fact; cluster map measured).
- **Severity:** HIGH as long-term maintainability debt — once the legacy.py
  epic completes, this is the repo's largest file with zero paydown story.
- **Category:** design / architecture. **Epic** — same shape as
  `07-02-legacy-monolith-decomposition`.

## Goal

Break `server_ops.py` (7,662 lines, 9.6× the 800-line cap) into focused
modules using the extraction pattern proven by the legacy.py epic, without
changing any HTTP/command/MCP behavior.

## Problem

`server_ops.py` holds, in one file (approximate cluster map from the
review): the `OPS_SCENARIO_PROFILES` data registry (~660 lines, :132-792),
runtime dataclasses + `SimulationState` (~250), command parsing (~390,
:1298-1687), render dispatch + `_render_get`/`_render_describe` (~830),
logs/rollout/mutation renderers (~1,000), Helm renderers (~240), the
Kubernetes REST facade + OpenAPI + kubeconfig (~710, :4886-5600), k8s
Table rendering (~450), k8s object builders (~500), Helm Secret encoding +
metrics (~130), and shared helpers (~460). The same resource families are
hand-maintained in four parallel places (command-mode table, REST table,
REST object, snapshot rows) — the lockstep burden CLAUDE.md already
documents. The only prior task naming this file
(`07-02-audit-server-ops-rendering`, completed) was a robustness audit,
explicitly not a split.

## Requirements

- Write a `design.md` first (mirroring the legacy epic): boundaries,
  sequencing (data-first — `server_ops_profiles.py` is a pure-data leaf
  and the natural step 1), and a compatibility inventory: the
  `server_commands.py` / `server_kubernetes.py` / `server_helm.py`
  facades, `server.py`'s 40+-name `NAME = _server_ops.NAME` alias block
  ([server.py:282](src/anomaly_metric_creator/server.py:282)+), and
  `server_mcp.py`'s imports must all keep resolving.
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
  (1,781 lines) mixes bounded-server infrastructure, HTTP dispatch, and
  `serve_main` CLI, and its manual alias block must be extended for every
  new public ops name — consider an explicit re-export module or
  `__getattr__` delegation during step 1.

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

## Notes

- Epic — break into child tasks once design.md fixes boundaries; do not
  attempt in one session.
- Reuse the legacy epic's hard-won rules: verbatim moves, one-way imports,
  re-import stubs at the same conceptual location, splice-hazard grep
  after every cut, move-with-callers for monkeypatched names.

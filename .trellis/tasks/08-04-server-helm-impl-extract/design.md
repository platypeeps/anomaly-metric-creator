# server_helm_impl.py extraction — Design (epic step 3)

Executes step 3 of `07-06-server-ops-decomposition` under that epic's
`design.md` rules (verbatim move, one-way import, re-import stub at the
conceptual position, splice-hazard grep, render-oracle diff). This file
records the closure audit that fixes the exact move set — and the
**blocker the audit surfaced**.

## Closure audit (verified against server_ops.py @ 6,589 lines)

Move-set candidate (20 helm symbols): `_render_helm` (dispatcher, 630),
`_render_helm_list/_status/_history/_env/_get/_test/_install/_upgrade`
(2955–3126), `_helm_value_overrides` (3128), `_helm_operation_note`
(3143), `_render_helm_rollback` (3170), `_helm_release` (3519),
`_helm_notes` (3533), `_helm_current_description` (3542),
`_helm_secret_objects` (5911), `_helm_release_revisions` (5918),
`_helm_secret_object` (5939), `_helm_encoded_release_data` (5969),
`_helm_release_payload` (5976).

### BLOCKER — helm is not a clean one-way leaf yet

The moved helm functions call **seven helpers that are DEFINED in
`server_ops.py` and are NOT helm-owned** (they are general
render/k8s primitives used by many non-helm renderers):

| helper | defined @ | helm caller(s) | nature |
|--------|-----------|----------------|--------|
| `_table` | 3589 | list/history/test renderers | general table formatter |
| `_format_dt` | 3607 | history/test/install/upgrade/rollback/`_helm_release` | general datetime fmt |
| `_is_dry_run` | 488 | install/upgrade | reads `ParsedCommand` |
| `_unsupported` | 683 | `_render_helm` dispatcher | `CommandResult` builder |
| `_exposed_active_scenarios` | 3397 | `_render_helm_get`, `_helm_release_payload` | eval-mode scenario gate |
| `_k8s_metadata` | 6017 | `_helm_secret_object` | k8s object metadata builder |
| `_k8s_timestamp` | 6238 | `_helm_release_payload` | k8s RFC3339 timestamp |

Plus constants `DEFAULT_RELEASE` (41), `DEFAULT_CHART` (42) — defined in
server_ops, also read by the k8s `resource_snapshot` path.

The epic's one-way rule forbids a leaf importing `server_ops`. A moved
helm function calling any of these seven is a **reverse import**. There is
no helm sub-slice that avoids them: the render helpers
(`_table`/`_format_dt`/`_is_dry_run`) are woven through the output
renderers, and the Secret-encoding path is a **k8s-object producer** that
depends on `_k8s_metadata` / `_k8s_timestamp`.

The epic design's Risks section anticipated the shape ("shared helpers
move down into leaves (or stay in server_ops.py), never sideways — audit
each step's closure before cutting") but did not resolve *which* leaf the
helm-required primitives land in, nor the ordering consequence.

### Dependency-order inversion

`_k8s_metadata` / `_k8s_timestamp` are **step 4/5** material (k8s object
model). `_table` / `_format_dt` / `_is_dry_run` / `_unsupported` are
**step 6** material (render dispatch). So step 3 (helm) sits *on top of*
primitives the epic schedules to extract *after* it. Helm — especially its
`helm.sh/release.v1` Secret encoding — is naturally a **late** step, once
the k8s-object and render-primitive leaves exist and can be imported
one-way.

## Resolution options

- **A — Resequence (recommended).** Extract the k8s-object leaf (epic
  step 4) and the render-primitive/dispatch leaf (epic step 6) first, then
  extract helm last. Helm then imports `_k8s_metadata`/`_k8s_timestamp`
  from the k8s leaf and `_table`/`_format_dt`/`_is_dry_run`/`_unsupported`
  from the render leaf — all strictly one-way. No synthetic module, no
  contradiction with the "server_ops retains shared helpers" end-state
  beyond what the epic already implies for those steps. Cost: helm ships
  near the end of the epic instead of third.
- **B — Shared-primitives leaf first.** Insert a new
  `server_render_primitives.py` (step 3a) holding the seven helpers +
  constants (+ `CommandResult` only if `_unsupported` moves), which
  server_ops re-imports and helm/k8s/render all import. Cost: a
  primitives leaf whose members overlap the planned step-4/6 boundaries —
  risks two extractions fighting over the same symbols; `_k8s_metadata` is
  central to the whole k8s object model, so moving it "for helm" is a
  step-4-scale move done early and out of context.
- **C — Callback seam.** Wire the seven helpers through named callbacks
  (the `legacy.py` registry pattern). Rejected: that pattern is for
  monkeypatchable registries/state, not seven pure utility functions;
  heavy and unidiomatic here.

## Recommendation

Resequence the epic (Option A): do k8s objects/tables (step 4) and render
dispatch/primitives (step 6) before helm; helm becomes the final leaf.
This keeps every extraction a clean verbatim one-way move and matches the
real dependency DAG (helm → {k8s-object primitives, render primitives}).
The change is an epic-ordering decision, so it is surfaced for the
maintainer rather than executed under the autonomous loop.

## Status

**Parked** pending the resequencing decision. This closure audit is the
reusable input for whichever order is chosen — the helm move-set and its
exact primitive dependencies are pinned above. The helm render-oracle
harness (`helm_oracle.py`, baseline captured against
`0844f59`) is ready to gate the eventual move.

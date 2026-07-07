# Close eval-mode scenario-slug leaks and unify rubric-404 ordering

## Review context

- **Source:** deep-dive architecture/code review, 2026-07-06.
- **Confidence:** CONFIRMED (every leak path read and cited).
- **Severity:** HIGH — the eval-mode ground-truth wall is the feature the
  MCP surface exists for, and it is currently defeated by side channels.
- **Category:** correctness / eval integrity.

## Goal

Ensure an agent connected to an eval-mode server cannot read or fingerprint
the active scenario slugs (the eval answer key) through any surface, and
make the rubric-404 behavior consistent across HTTP methods.

## Problem (verified 2026-07-06)

`--mcp-eval-mode` walls off `/v1/scenarios`, `/v1/state`, `/v1/anomalies`,
`/v1/debug/*`, and the log surfaces — but the identical information (the
active scenario slugs) leaks through three surfaces that stay open:

1. **ConfigMap:** `resource_snapshot()` embeds
   `"SCENARIOS": ",".join(state.active_scenarios)` into the
   `simulated-saas-config` ConfigMap
   ([server_ops.py:1914](src/anomaly_metric_creator/server_ops.py:1914)).
   Exposed via the MCP tool `kubectl_get`
   ([server_mcp.py:724](src/anomaly_metric_creator/server_mcp.py:724)
   returns snapshot rows verbatim; only the two log tools check
   `_eval_mode`) and via the Kubernetes REST facade (`_k8s_configmap`,
   [server_ops.py:6588](src/anomaly_metric_creator/server_ops.py:6588) —
   the configmaps GET path is not a rubric path).
2. **`kubectl exec <pod> -- env`** returns `SCENARIOS=<slugs>`
   ([server_ops.py:4100](src/anomaly_metric_creator/server_ops.py:4100))
   through `POST /v1/commands` (investigation-classified).
3. **`helm get values`** returns `scenarios: [...]`
   ([server_ops.py:4188](src/anomaly_metric_creator/server_ops.py:4188)).

Separately, the rubric 404 is applied **before auth only on GET**
([server.py:553](src/anomaly_metric_creator/server.py:553) vs :561).
`do_POST` runs auth (:656) and rate-limit (:659) before the rubric check
(:661), so an unauthenticated `POST /v1/debug/commands/import` in eval
mode returns `401` + `WWW-Authenticate` instead of `404` — revealing the
endpoint exists. `_handle_mutating_method` (PUT/PATCH/DELETE,
[server.py:746](src/anomaly_metric_creator/server.py:746)-792) has no
eval-mode check at all. The completeness test
(`test_every_dispatched_route_is_classified`) checks classification, not
ordering, so neither gap is caught today.

## Requirements

- In eval mode, scrub or omit scenario-identifying values from every
  investigation-open surface: the ConfigMap `SCENARIOS` key (fix at the
  `resource_snapshot()` / render layer keyed off `state.eval_mode` so
  command mode, MCP, and REST all inherit it — do not fork a second
  resource model), exec `env`/`printenv` output, and `helm get values`.
- Sweep for other slug carriers before closing: grep every renderer for
  `active_scenarios` and scenario-name/description strings (describe
  output, events, helm notes/status, pod annotations, OpenAPI docs).
- Make the rubric-404 ordering uniform: rubric check before auth for POST
  and PUT/PATCH/DELETE, matching the GET path's documented
  fingerprint-resistance contract.
- Extend the eval-mode tests: (a) an ordering assertion (rubric endpoints
  return 404, not 401, when unauthenticated in eval mode, per method);
  (b) a leak-sweep test that runs a representative command/MCP/REST pass
  and asserts no active slug appears in any response body.
- Update CLAUDE.md's eval-mode section and the Trellis spec (with
  `07-06-trellis-spec-server-era-backfill`) to state the extended wall:
  no active-scenario identifiers on any surface, only observable symptoms.

## Acceptance Criteria

- [x] In eval mode, no response from `/mcp` tools, `POST /v1/commands`,
      or the Kubernetes REST facade contains any active scenario slug
      (regression test with a seeded run and a response-body sweep).
- [x] Rubric endpoints return 404 before auth for GET, POST, PUT, PATCH,
      DELETE in eval mode (parametrized test).
- [x] Non-eval-mode output is byte-unchanged (existing server tests pass
      untouched).
- [x] `tests/test_server_eval_mode.py` completeness coverage extended per
      the requirements.

## Resolution (2026-07-07)

Fixed via a single `state.eval_mode` gate at each emit site (no second
resource model). Redaction helpers `_exposed_active_scenarios` /
`_exposed_component_scenarios` in `server_ops.py` return empty in eval mode
— collapsing to a legitimate zero-scenario run, so the redaction is itself
fingerprint-resistant — and wrap the enumerated leak sites: ConfigMap
`SCENARIOS`, pod `scenario_ids` (at the `resource_snapshot()` source, so
MCP `kubectl_get` / REST `_k8s_configmap` / command mode all inherit it),
`kubectl exec … env`, `helm get values`, and the Helm release
`config.scenarios`. The behavioral `_component_scenarios` (drives the
`ScenarioInfluenced` health signal) is intentionally left ungated so
symptoms stay visible.

**Seventh vector found by the sweep (not in the original enumeration):**
the `/v1/commands` response echoes the `CommandTrace` via
`{"trace": trace.to_dict()}`, whose `active_scenarios` field leaked the
full active list on *every* command regardless of what was run.
`run_command` now scrubs that field from the echo in eval mode while the
*stored* trace keeps the real slugs (the walled `/v1/debug/*` +
`/v1/debug/commands/export` surfaces are the harness's scoring data). This
is exactly why the PRD demanded a live response-body sweep rather than
trusting the enumerated sites — the pre-read enumeration missed it.

Ordering: the rubric-`404`-before-auth check now runs for all methods —
moved above auth in `do_POST` and added to `_handle_mutating_method`
(PUT/PATCH/DELETE), matching `do_GET`.

Tests (`tests/test_server_eval_mode.py`):
`test_eval_mode_ops_surfaces_have_no_scenario_slug_leak` (live multi-surface
sweep with a non-eval positive control asserting the surfaces really carry
the slugs) and `test_eval_mode_rubric_404_before_auth_every_method`
(auth-enabled 404-not-401 per method, with a non-eval 401 control). Full
server suite green (178 passed). CLAUDE.md eval-mode section updated with the
extended-wall rule; the Trellis spec statement is left to
`07-06-trellis-spec-server-era-backfill` per the split.

## Notes

- Highest-priority finding of the 2026-07-06 review. CLAUDE.md's letter
  ("no MCP tool may read the SCENARIOS registry") is technically honored —
  the leak is `state.active_scenarios`, the resolved slugs — but the
  wall's intent is broken.
- `06-29-kubectl-exec-outputs` now carries a note that any new exec
  outputs must stay inside this wall.

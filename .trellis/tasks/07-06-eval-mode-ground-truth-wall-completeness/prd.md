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

- [ ] In eval mode, no response from `/mcp` tools, `POST /v1/commands`,
      or the Kubernetes REST facade contains any active scenario slug
      (regression test with a seeded run and a response-body sweep).
- [ ] Rubric endpoints return 404 before auth for GET, POST, PUT, PATCH,
      DELETE in eval mode (parametrized test).
- [ ] Non-eval-mode output is byte-unchanged (existing server tests pass
      untouched).
- [ ] `tests/test_server_eval_mode.py` completeness coverage extended per
      the requirements.

## Notes

- Highest-priority finding of the 2026-07-06 review. CLAUDE.md's letter
  ("no MCP tool may read the SCENARIOS registry") is technically honored —
  the leak is `state.active_scenarios`, the resolved slugs — but the
  wall's intent is broken.
- `06-29-kubectl-exec-outputs` now carries a note that any new exec
  outputs must stay inside this wall.

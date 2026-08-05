# Extract server_k8s_api.py — Design (epic step 5)

## Overview

Role-swap extraction (legacy.py pattern): the pure k8s REST builder/filter/
format layer moves verbatim into a new one-way leaf `server_k8s_api.py`;
`server_ops.py` re-imports every moved name at its original position; the
leaf never imports `server_ops` at runtime. The `resource_snapshot`-bound
dispatch spine stays in `server_ops.py`.

Grounded in the 2026-08-05 closure audit. `server_ops.py` is 5,440 lines.

## Proposal — the move set (verbatim, one leaf)

### Companion prerequisite (into an EXISTING leaf, first commit)

- `_preview` (server_ops.py:3532–3537) → `server_ops_support.py`
  (pure, stdlib-only). `server_ops` re-imports it; `run_command` (:687)
  and the moved cluster callers resolve it one-way from support.

### Into `server_k8s_api.py`

**Response builders + dataclass**
- `KubernetesApiResponse` (:172–179), `_k8s_json_response` (:4309–4317),
  `_k8s_text_response` (:4319–4327), `_k8s_status_response` (:4329–4351),
  `_k8s_read_only_response` (:4353–4355),
  `_k8s_read_only_status_args` (:4357–4365).

**Discovery / data builders**
- `_k8s_api_group_list` (:4367–4378), `_k8s_api_group` (:4380–4386),
  `_k8s_api_resource_list` (:4511–4614), `_k8s_resource_meta` (:4809–4835).

**OpenAPI structural helpers (no snapshot)**
- `_k8s_openapi_v3_discovery` (:3680–3689), `_openapi_operation`
  (:3808–3834), `_openapi_list_schema` (:3836–3866),
  `_openapi_schema_name` (:3868–3873),
  `_openapi_list_schema_name` (:3875–3877),
  `_openapi_group_versions` (:3879–3888),
  `_openapi_group_version_from_path` (:3890–3897).

**Filters / selectors**
- `_filter_k8s_objects` (:5007–5017),
  `_filter_k8s_objects_by_namespace` (:4667–4677),
  `_matches_label_selector` (:5020–5047),
  `_matches_field_selector` (:5049–5063),
  `_selector_set_requirement` (:5065–5071),
  `_split_selector` (:5073–5087), `_nested_field` (:5089–5097),
  `_query_str` (:63–76), `_query_int` (:56–62).

**Watch pure helpers**
- `_WATCHABLE_LIST_RESOURCES` (:4685–4689), `_watch_requested` (:4691–4694),
  `k8s_watch_plan` (:4696–4741), `k8s_watch_object_key` (:4759–4766),
  `k8s_watch_trace_response` (:4768–4807).

**Mutation-parse helpers (no snapshot)**
- `_k8s_mutation_target` (:4124–4147),
  `_k8s_subresource_mutation_allowed` (:4149–4153),
  `_payload_replicas` (:4182–4192), `_k8s_scale` (:4194–4213).

**API trace / fingerprint / redaction**
- `_api_trace_body` (:5099–5104), `_redact_large_secret_data` (:5106–5118),
  `_api_namespace` (:5120–5126), `_api_resource_kind` (:5128–5138),
  `_api_resource_name` (:5140–5150), `_api_fingerprint` (:5152–5189),
  `_api_guess_intent` (:5191–5195), `_is_kubernetes_api_path` (:5197–5199),
  `_rate_limit_bucket` (:5201–5210), `_redact_query` (:4293–4298),
  `_is_sensitive_query_key` (:4300–4307), `_SENSITIVE_QUERY_KEYS` (:567–581).

**Body-read helpers + constants + kubeconfig**
- `RequestBodyTooLarge` (:3539–3540), `_read_json_body` (:3543–3559),
  `_read_optional_json_body` (:3562–3576), `_content_length` (:3579–3589),
  `DEFAULT_MAX_BODY_BYTES` (:50), `_K8S_ADVERTISED_VERSION` / `_TAG` /
  `_GIT_VERSION` (:51–53), `render_kubeconfig` (:4215–4242).

Estimated moved: ~950 lines. Target `server_ops.py` end: ~4,490.

## Boundaries And Non-Goals — STAYS in server_ops.py

The `resource_snapshot`-bound / command-render-bound spine:
`kubernetes_api_response`, `kubernetes_api_post_response`,
`kubernetes_api_mutating_response`, `_k8s_mutated_object`,
`_k8s_group_resource_response`, `_k8s_core_resource_response`,
`_k8s_resource_response`, `_k8s_objects_for_resource`, `_k8s_endpointslice`,
`k8s_watch_objects`, `record_kubernetes_api_call`, and the OpenAPI
**document** builders (`_k8s_openapi_response`, `_k8s_openapi_v2_document`,
`_k8s_openapi_v3_document`, `_openapi_schema_definitions`, `_openapi_paths`).
`_openapi_paths` needs `_snapshot_kind_namespaced` + the snapshot-kind
constants (which have staying consumers) — moving it needs a second
companion move, deferred.

## Leaf import surface (`server_k8s_api.py`)

One-way from existing leaves only:
- `server_mutations`: `DEFAULT_NAMESPACE`, `_format_dt`.
- `server_traces`: `CommandTrace`.
- `server_ops_parse`: `ParsedCommand`, `_SENSITIVE_FLAG_TOKENS`,
  `_EXPLAIN_RESOURCE_TARGETS`.
- `server_ops_support`: `_snapshot_row_labels`, `_k8s_list_resource_version`,
  `_preview` (after the companion move).
- `server_k8s_objects`: the `_k8s_scale` helper deps live here —
  `_k8s_metadata_for_row` (server_k8s_objects.py:492),
  `_row_selector` (:521), `_selector_string` (:539) — plus `_k8s_timestamp`
  and any object-builder/metadata helper a moved member needs. Import each
  one-way from `server_k8s_objects`.
- `server_k8s_tables`: `_accepts_table`, `_k8s_table` (only if a moved member
  needs them — `k8s_watch_trace_response` uses `_k8s_status_response` which
  is itself moving, so recheck).
- `TYPE_CHECKING`: `from .server_ops import SimulationState` (stringized
  annotation via `from __future__ import annotations`; never evaluated).

`record_kubernetes_api_call` STAYS but calls moved names (`_redact_query`,
`_api_*`, `_preview`); it resolves them via the `server_ops` re-import stubs
— no reverse import.

## Affected Files

- New: `server_k8s_api.py`.
- Edit: `server_ops.py` (delete moved bodies; add
  `from .server_k8s_api import (...)` re-import block(s) at original
  positions; keep `__all__` membership), `server_ops_support.py` (add
  `_preview`), `tools/check_mypy_gate.py` (add `server_k8s_api`), CLAUDE.md
  server module map, `.trellis/spec/amc/backend/architecture.md` map (if
  present).
- **Never edit:** `server.py` alias block, `server_kubernetes.py`,
  `server_commands.py`, `server_helm.py`, `server_mcp.py`.

## Data And Command Contracts

No wire/behavior change. `KubernetesApiResponse` shape unchanged. Every
external caller reaches moved names via the `server_ops` re-import + the
`server.py` alias block, unchanged.

## Risks And Edge Cases

- **`resource_snapshot` monkeypatch pin** (`test_server.py:563`): all
  `resource_snapshot`-touching functions MUST stay in `server_ops`. The move
  set is exactly the audited pure layer — do not add a spine function.
- **Splice hazard:** the moved ranges interleave with existing leaf
  re-import stubs at :4837 (`server_k8s_tables`), :4930
  (`server_k8s_objects`), :4998 (`server_helm_impl`). After each cut, grep
  the moved range for `^from \.` and confirm every existing leaf re-import
  still resolves. Do not sweep those stubs into the new leaf.
- **Scattered members:** several members live in the :50–644 header region
  (constants, `KubernetesApiResponse`, `_query_int/_str`,
  `_SENSITIVE_QUERY_KEYS`), not the contiguous tail — move each individually
  and re-import at its original position.
- **`_preview` staying caller:** `run_command` (:687) must resolve
  `_preview` after the move — the `server_ops` re-import from
  `server_ops_support` covers it (intra-module reference resolves in
  `server_ops` namespace).
- **`_k8s_scale` helper homes:** confirm `_k8s_metadata_for_row` /
  `_row_selector` / `_selector_string` / `_snapshot_row_labels` canonical
  leaf before importing (audit says server_k8s_objects / support — grep).
- **No import-time execution** in the move set → re-import stub has no
  position-validation constraint (place at original conceptual position for
  readability).

## Validation

```bash
.venv/bin/pytest tests/test_server.py tests/test_server_ops_fuzz.py \
  tests/test_server_mcp.py tests/test_server_eval_mode.py \
  tests/test_server_watch.py -n 0
.venv/bin/pytest            # full suite
.venv/bin/pre-commit run --all-files
.venv/bin/python tools/check_mypy_gate.py
```

Plus a before/after render-oracle byte-diff over a fixed k8s-API corpus:
`kubectl get pods/deployments/events` (Table + JSON), `describe`, discovery
(`/version`, `/api`, `/apis`, `/apis/apps/v1`), OpenAPI (`/openapi/v2`,
`/openapi/v3`), a `?watch=true` list, and `/v1/kubeconfig` — captured via
`kubernetes_api_response` / `run_command` in a scratch script on a
frozen clock. Byte-identical is the pass condition (no golden hashes on this
surface).

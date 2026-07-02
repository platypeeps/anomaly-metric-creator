"""HTTP simulator server for Kubernetes/Helm incident debugging.

The module is intentionally stdlib-only. Server mode should be useful in a
fresh editable install without adding a web framework dependency to the core
generator.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import hmac
import ipaddress
import json
import sys
import tempfile
import threading
import time
import urllib.parse
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import server_mcp
from .server_debug_ui import DEBUG_HTML
from .server_mutations import (
    HelmReleaseMutation as HelmReleaseMutation,  # noqa: F401
    SimulationMutations as SimulationMutations,  # noqa: F401
    WorkloadMutation as WorkloadMutation,  # noqa: F401
)
from .server_traces import (
    COMMAND_TRACE_DB_SCHEMA_VERSION as COMMAND_TRACE_DB_SCHEMA_VERSION,
    COMMAND_TRACE_EXPORT_VERSION as COMMAND_TRACE_EXPORT_VERSION,
    DEFAULT_TRACE_LIMIT,
    CommandTrace as CommandTrace,  # noqa: F401
    CommandTraceStore as CommandTraceStore,  # noqa: F401
)

DEFAULT_RELEASE = "simulated-saas"
DEFAULT_CHART = "simulated-saas-0.3.0"
DEFAULT_MAX_BODY_BYTES = 1024 * 1024
CORS_ALLOW_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
CORS_ALLOW_HEADERS = "authorization, content-type, accept"


@dataclass(frozen=True)
class ServerSecurityConfig:
    """HTTP boundary controls for server mode."""

    auth_token: str = ""
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    allow_remote_without_auth: bool = False
    cors_allow_origin: str = ""
    rate_limit_per_minute: int = 0


class StructuredRequestLogger:
    """Thread-safe JSONL request/error logger for serve mode."""

    def __init__(self, path: str | Path | None = None, *, stream: Any | None = None):
        self.path = Path(path) if path is not None else None
        self.stream = stream
        self._lock = threading.Lock()
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def log_request(self, record: dict[str, Any]) -> None:
        self._write({"event": "request", **record})

    def log_error(self, record: dict[str, Any]) -> None:
        self._write({"event": "error", **record})

    def _write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, sort_keys=True) + "\n"
        with self._lock:
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line)
                return
            target = self.stream if self.stream is not None else sys.stderr
            target.write(line)
            target.flush()


@dataclass(frozen=True)
class _RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


class _RateLimiter:
    """Small per-client fixed-window limiter for optional lab hardening."""

    def __init__(self, limit_per_minute: int, *, window_seconds: float = 60.0) -> None:
        self._limit = max(0, int(limit_per_minute))
        self._window_seconds = window_seconds
        self._lock = threading.RLock()
        self._calls: dict[tuple[str, str], deque[float]] = {}

    def check(self, client: str, bucket: str) -> _RateLimitDecision:
        if self._limit <= 0:
            return _RateLimitDecision(True)
        now = time.monotonic()
        key = (client, bucket)
        with self._lock:
            calls = self._calls.setdefault(key, deque())
            cutoff = now - self._window_seconds
            while calls and calls[0] <= cutoff:
                calls.popleft()
            if len(calls) >= self._limit:
                retry_after = self._window_seconds - (now - calls[0])
                return _RateLimitDecision(False, max(1, int(retry_after + 0.999)))
            calls.append(now)
        return _RateLimitDecision(True)


from . import server_ops as _server_ops

# Compatibility facade: keep the historic anomaly_metric_creator.server import
# surface while the ops implementation lives in server_ops.py.
DEFAULT_RELEASE = _server_ops.DEFAULT_RELEASE
DEFAULT_CHART = _server_ops.DEFAULT_CHART
DEFAULT_NAMESPACE = _server_ops.DEFAULT_NAMESPACE
OpsComponentImpact = _server_ops.OpsComponentImpact
OpsScenarioProfile = _server_ops.OpsScenarioProfile
_impact = _server_ops._impact
_profile = _server_ops._profile
OPS_SCENARIO_PROFILES = _server_ops.OPS_SCENARIO_PROFILES
validate_ops_profiles = _server_ops.validate_ops_profiles
SimulationClock = _server_ops.SimulationClock
ParsedCommand = _server_ops.ParsedCommand
CommandResult = _server_ops.CommandResult
KubernetesApiResponse = _server_ops.KubernetesApiResponse
ContinuousGenerationStatus = _server_ops.ContinuousGenerationStatus
SimulationState = _server_ops.SimulationState
build_state = _server_ops.build_state
load_anomaly_rows = _server_ops.load_anomaly_rows
_snapshot_row_namespace = _server_ops._snapshot_row_namespace
_snapshot_row_key = _server_ops._snapshot_row_key
_snapshot_kind_namespaced = _server_ops._snapshot_kind_namespaced
run_command = _server_ops.run_command
parse_command = _server_ops.parse_command
_split_flags = _server_ops._split_flags
_parse_kubectl = _server_ops._parse_kubectl
_parse_helm = _server_ops._parse_helm
_split_resource_token = _server_ops._split_resource_token
_normalize_kind = _server_ops._normalize_kind
render_command = _server_ops.render_command
_with_flag_support = _server_ops._with_flag_support
_render_kubectl = _server_ops._render_kubectl
_render_helm = _server_ops._render_helm
_unsupported = _server_ops._unsupported
resource_snapshot = _server_ops.resource_snapshot
_apply_default_namespaces = _server_ops._apply_default_namespaces
_apply_mutation_rows = _server_ops._apply_mutation_rows
_render_get = _server_ops._render_get
_render_get_all = _server_ops._render_get_all
_filter_snapshot_rows = _server_ops._filter_snapshot_rows
_snapshot_row_matches_namespace = _server_ops._snapshot_row_matches_namespace
_snapshot_row_labels = _server_ops._snapshot_row_labels
_snapshot_row_matches_field_selector = _server_ops._snapshot_row_matches_field_selector
_normalized_resource_prefix = _server_ops._normalized_resource_prefix
_render_describe = _server_ops._render_describe
_logs_uses_selector = _server_ops._logs_uses_selector
_render_logs_command = _server_ops._render_logs_command
_logs_target_pods = _server_ops._logs_target_pods
_logs_container_name = _server_ops._logs_container_name
_logs_has_container_flag = _server_ops._logs_has_container_flag
_logs_since_time = _server_ops._logs_since_time
_logs_tail_limit = _server_ops._logs_tail_limit
_render_logs = _server_ops._render_logs
_render_pod_logs = _server_ops._render_pod_logs
_render_top = _server_ops._render_top
_render_kubectl_version = _server_ops._render_kubectl_version
_render_kubectl_api_versions = _server_ops._render_kubectl_api_versions
_render_kubectl_api_resources = _server_ops._render_kubectl_api_resources
_render_kubectl_cluster_info = _server_ops._render_kubectl_cluster_info
_render_rollout_status = _server_ops._render_rollout_status
_render_rollout_history = _server_ops._render_rollout_history
_render_rollout_restart = _server_ops._render_rollout_restart
_render_scale = _server_ops._render_scale
_render_delete = _server_ops._render_delete
_render_apply = _server_ops._render_apply
_resource_from_manifest_name = _server_ops._resource_from_manifest_name
_mutation_snapshot_kind = _server_ops._mutation_snapshot_kind
_record_continuous_generation_failure = _server_ops._record_continuous_generation_failure
_generic_resource_row = _server_ops._generic_resource_row
_generic_resource_metadata = _server_ops._generic_resource_metadata
_string_dict = _server_ops._string_dict
_configmap_keys_from_flags = _server_ops._configmap_keys_from_flags
_parsed_replicas = _server_ops._parsed_replicas
_render_wait = _server_ops._render_wait
_render_exec = _server_ops._render_exec
_render_port_forward = _server_ops._render_port_forward
_render_helm_list = _server_ops._render_helm_list
_render_helm_status = _server_ops._render_helm_status
_render_helm_history = _server_ops._render_helm_history
_render_helm_env = _server_ops._render_helm_env
_render_helm_get = _server_ops._render_helm_get
_render_helm_test = _server_ops._render_helm_test
_render_helm_install = _server_ops._render_helm_install
_render_helm_upgrade = _server_ops._render_helm_upgrade
_helm_value_overrides = _server_ops._helm_value_overrides
_render_helm_rollback = _server_ops._render_helm_rollback
_not_found = _server_ops._not_found
_component_health = _server_ops._component_health
_component_impacts = _server_ops._component_impacts
_apply_component_impact = _server_ops._apply_component_impact
_status_priority = _server_ops._status_priority
_component_scenarios = _server_ops._component_scenarios
_component_events = _server_ops._component_events
_component_rollout_notes = _server_ops._component_rollout_notes
_event_rows = _server_ops._event_rows
_node_rows = _server_ops._node_rows
_helm_release = _server_ops._helm_release
_helm_notes = _server_ops._helm_notes
_helm_current_description = _server_ops._helm_current_description
_replica_count = _server_ops._replica_count
_pod_name = _server_ops._pod_name
_component_from_name = _server_ops._component_from_name
_stable_cluster_ip = _server_ops._stable_cluster_ip
_find_named = _server_ops._find_named
_table = _server_ops._table
command_fingerprint = _server_ops.command_fingerprint
guess_intent = _server_ops.guess_intent
_preview = _server_ops._preview
_redact_command_for_trace = _server_ops._redact_command_for_trace
_redact_argv = _server_ops._redact_argv
_redact_parsed_flags = _server_ops._redact_parsed_flags
_is_sensitive_flag_name = _server_ops._is_sensitive_flag_name
_format_dt = _server_ops._format_dt
_parse_user_timestamp = _server_ops._parse_user_timestamp
_parse_optional_timestamp = _server_ops._parse_optional_timestamp
RequestBodyTooLarge = _server_ops.RequestBodyTooLarge
_read_json_body = _server_ops._read_json_body
_read_optional_json_body = _server_ops._read_optional_json_body
_content_length = _server_ops._content_length
kubernetes_api_response = _server_ops.kubernetes_api_response
kubernetes_api_post_response = _server_ops.kubernetes_api_post_response
kubernetes_api_mutating_response = _server_ops.kubernetes_api_mutating_response
_k8s_mutation_target = _server_ops._k8s_mutation_target
_k8s_subresource_mutation_allowed = _server_ops._k8s_subresource_mutation_allowed
_k8s_mutated_object = _server_ops._k8s_mutated_object
_payload_replicas = _server_ops._payload_replicas
_k8s_scale = _server_ops._k8s_scale
render_kubeconfig = _server_ops.render_kubeconfig
record_kubernetes_api_call = _server_ops.record_kubernetes_api_call
_redact_query = _server_ops._redact_query
_is_sensitive_query_key = _server_ops._is_sensitive_query_key
_k8s_json_response = _server_ops._k8s_json_response
_k8s_text_response = _server_ops._k8s_text_response
_k8s_status_response = _server_ops._k8s_status_response
_k8s_read_only_response = _server_ops._k8s_read_only_response
_k8s_read_only_status_args = _server_ops._k8s_read_only_status_args
_k8s_api_group_list = _server_ops._k8s_api_group_list
_k8s_api_group = _server_ops._k8s_api_group
_k8s_group_resource_response = _server_ops._k8s_group_resource_response
_k8s_core_resource_response = _server_ops._k8s_core_resource_response
_k8s_api_resource_list = _server_ops._k8s_api_resource_list
_k8s_resource_response = _server_ops._k8s_resource_response
_filter_k8s_objects_by_namespace = _server_ops._filter_k8s_objects_by_namespace
_k8s_list_resource_version = _server_ops._k8s_list_resource_version
_k8s_resource_meta = _server_ops._k8s_resource_meta
_accepts_table = _server_ops._accepts_table
_k8s_table = _server_ops._k8s_table
_k8s_column = _server_ops._k8s_column
_k8s_table_schema = _server_ops._k8s_table_schema
_k8s_pod_cells = _server_ops._k8s_pod_cells
_k8s_pod_display_status = _server_ops._k8s_pod_display_status
_k8s_deployment_cells = _server_ops._k8s_deployment_cells
_k8s_service_cells = _server_ops._k8s_service_cells
_k8s_endpoints_cells = _server_ops._k8s_endpoints_cells
_k8s_endpointslice_cells = _server_ops._k8s_endpointslice_cells
_k8s_event_cells = _server_ops._k8s_event_cells
_k8s_hpa_cells = _server_ops._k8s_hpa_cells
_k8s_node_cells = _server_ops._k8s_node_cells
_k8s_replicaset_cells = _server_ops._k8s_replicaset_cells
_k8s_daemonset_cells = _server_ops._k8s_daemonset_cells
_k8s_pvc_cells = _server_ops._k8s_pvc_cells
_k8s_statefulset_cells = _server_ops._k8s_statefulset_cells
_k8s_ingress_cells = _server_ops._k8s_ingress_cells
_k8s_secret_cells = _server_ops._k8s_secret_cells
_k8s_configmap_cells = _server_ops._k8s_configmap_cells
_k8s_serviceaccount_cells = _server_ops._k8s_serviceaccount_cells
_k8s_job_cells = _server_ops._k8s_job_cells
_k8s_cronjob_cells = _server_ops._k8s_cronjob_cells
_k8s_namespace_cells = _server_ops._k8s_namespace_cells
_k8s_default_cells = _server_ops._k8s_default_cells
_k8s_objects_for_resource = _server_ops._k8s_objects_for_resource
_k8s_namespace = _server_ops._k8s_namespace
_k8s_pod = _server_ops._k8s_pod
_k8s_configmap = _server_ops._k8s_configmap
_k8s_secret = _server_ops._k8s_secret
_k8s_serviceaccount = _server_ops._k8s_serviceaccount
_k8s_deployment = _server_ops._k8s_deployment
_k8s_replicaset = _server_ops._k8s_replicaset
_k8s_daemonset = _server_ops._k8s_daemonset
_k8s_statefulset = _server_ops._k8s_statefulset
_k8s_service = _server_ops._k8s_service
_k8s_endpoints = _server_ops._k8s_endpoints
_k8s_event = _server_ops._k8s_event
_k8s_hpa = _server_ops._k8s_hpa
_k8s_job = _server_ops._k8s_job
_k8s_cronjob = _server_ops._k8s_cronjob
_k8s_pvc = _server_ops._k8s_pvc
_k8s_ingress = _server_ops._k8s_ingress
_k8s_endpointslice = _server_ops._k8s_endpointslice
_k8s_node = _server_ops._k8s_node
_k8s_pod_metrics = _server_ops._k8s_pod_metrics
_k8s_node_metrics = _server_ops._k8s_node_metrics
_helm_secret_objects = _server_ops._helm_secret_objects
_helm_release_revisions = _server_ops._helm_release_revisions
_helm_secret_object = _server_ops._helm_secret_object
_helm_encoded_release_data = _server_ops._helm_encoded_release_data
_helm_release_payload = _server_ops._helm_release_payload
_k8s_metadata = _server_ops._k8s_metadata
_k8s_metadata_for_row = _server_ops._k8s_metadata_for_row
_row_selector = _server_ops._row_selector
_row_template_labels = _server_ops._row_template_labels
_selector_string = _server_ops._selector_string
_k8s_owner_reference = _server_ops._k8s_owner_reference
_k8s_workload_labels = _server_ops._k8s_workload_labels
_k8s_container_state = _server_ops._k8s_container_state
_filter_k8s_objects = _server_ops._filter_k8s_objects
_matches_label_selector = _server_ops._matches_label_selector
_matches_field_selector = _server_ops._matches_field_selector
_selector_set_requirement = _server_ops._selector_set_requirement
_split_selector = _server_ops._split_selector
_nested_field = _server_ops._nested_field
_k8s_timestamp = _server_ops._k8s_timestamp
_stable_pod_ip = _server_ops._stable_pod_ip
_api_trace_body = _server_ops._api_trace_body
_redact_large_secret_data = _server_ops._redact_large_secret_data
_api_namespace = _server_ops._api_namespace
_api_resource_kind = _server_ops._api_resource_kind
_api_resource_name = _server_ops._api_resource_name
_api_fingerprint = _server_ops._api_fingerprint
_api_guess_intent = _server_ops._api_guess_intent
_is_kubernetes_api_path = _server_ops._is_kubernetes_api_path
_rate_limit_bucket = _server_ops._rate_limit_bucket

def make_handler(
    state: SimulationState,
    security: ServerSecurityConfig | None = None,
    request_logger: StructuredRequestLogger | None = None,
):
    security = security or ServerSecurityConfig()
    rate_limiter = _RateLimiter(security.rate_limit_per_minute)

    class _Handler(BaseHTTPRequestHandler):
        server_version = "AMCServer/0.1"

        def handle_one_request(self) -> None:
            self._request_started_at = time.perf_counter()
            self._response_status = 0
            self._response_bytes = 0
            self._structured_error: dict[str, str] | None = None
            try:
                super().handle_one_request()
            finally:
                self._write_structured_logs()

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def send_response(self, code: int, message: str | None = None) -> None:
            self._response_status = code
            super().send_response(code, message)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            try:
                if path == "/healthz":
                    self._send_text(200, "ok\n")
                    return
                if path == "/readyz":
                    self._send_json(200, {"ready": True})
                    return
                if path == "/" or path == "/debug":
                    self._send_html(200, DEBUG_HTML)
                    return
                if not self._is_authorized():
                    self._send_unauthorized(path)
                    return
                if self._send_rate_limited(path):
                    return
                if path == "/mcp":
                    # Streamable HTTP only: a GET here is the legacy SSE
                    # transport probe, refused like the reference mock.
                    self._send_json(405, server_mcp.sse_not_supported_response())
                    return
                api_started = time.perf_counter()
                api_response = kubernetes_api_response(
                    state,
                    "GET",
                    path,
                    query,
                    self.headers.get("accept", ""),
                )
                if api_response is not None:
                    record_kubernetes_api_call(
                        state,
                        method="GET",
                        path=path,
                        query=query,
                        response=api_response,
                        client=self.client_address[0],
                        user_agent=self.headers.get("user-agent", ""),
                        latency_ms=(time.perf_counter() - api_started) * 1000.0,
                    )
                    self._send_kubernetes_api_response(api_response)
                    return
                if path == "/v1/kubeconfig":
                    self._send_text(
                        200,
                        render_kubeconfig(
                            self._server_url(),
                            state.namespace,
                            token=security.auth_token,
                        ),
                    )
                elif path in {"/v1/state", "/v1/debug/state"}:
                    self._send_json(200, state.summary())
                elif path == "/v1/scenarios":
                    self._send_json(200, _scenario_payload(state))
                elif path == "/v1/anomalies":
                    limit = _query_int(query, "limit", 100)
                    self._send_json(200, {"items": state.generated_rows_slice(limit)})
                elif path == "/v1/debug/resources":
                    self._send_json(200, resource_snapshot(state))
                elif path == "/v1/debug/commands":
                    limit = _query_int(query, "limit", 100)
                    self._send_json(200, {"items": state.traces.list(limit=limit)})
                elif path == "/v1/debug/commands/export":
                    self._send_json(200, state.traces.export_payload())
                elif path.startswith("/v1/debug/commands/"):
                    raw_trace_id = path.rsplit("/", 1)[1]
                    try:
                        trace_id = int(raw_trace_id)
                    except ValueError:
                        self._send_json(400, {"error": "trace id must be an integer"})
                        return
                    item = state.traces.get(trace_id)
                    if item is None:
                        self._send_json(404, {"error": "not found"})
                        return
                    self._send_json(200, item)
                elif path == "/v1/debug/unsupported":
                    self._send_json(200, {"items": state.traces.unsupported_summary()})
                elif path == "/v1/debug/search":
                    self._send_json(200, state.traces.search(
                        query=_query_str(query, "q", ""),
                        support_status=_query_str(query, "status", ""),
                        command_family=_query_str(query, "family", ""),
                        scenario_id=_query_str(query, "scenario", ""),
                        limit=_query_int(query, "limit", 50),
                        offset=_query_int(query, "offset", 0),
                    ))
                elif path == "/v1/debug/events":
                    self._send_debug_events()
                elif path == "/v1/logs/stream":
                    self._send_log_stream()
                else:
                    self._send_json(404, {"error": "not found"})
            except Exception as exc:  # pragma: no cover - defensive HTTP boundary
                self._remember_structured_error(exc)
                self._send_json(500, {"error": str(exc)})

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            path = parsed.path
            try:
                if not self._is_authorized():
                    self._send_unauthorized(path)
                    return
                if self._send_rate_limited(path):
                    return
                if path == "/mcp":
                    self._send_mcp_post()
                    return
                if _is_kubernetes_api_path(path):
                    api_started = time.perf_counter()
                    try:
                        payload = _read_optional_json_body(self, security.max_body_bytes)
                    except RequestBodyTooLarge as exc:
                        api_response = _k8s_status_response(
                            413,
                            str(exc),
                            "RequestEntityTooLarge",
                            "unsupported",
                            "k8s.body.too_large",
                        )
                    else:
                        api_response = kubernetes_api_post_response(state, path, payload)
                    record_kubernetes_api_call(
                        state,
                        method="POST",
                        path=path,
                        query=query,
                        response=api_response,
                        client=self.client_address[0],
                        user_agent=self.headers.get("user-agent", ""),
                        latency_ms=(time.perf_counter() - api_started) * 1000.0,
                    )
                    self._send_kubernetes_api_response(api_response)
                    return
                payload = _read_json_body(self, security.max_body_bytes)
                if path == "/v1/commands":
                    result = run_command(
                        state,
                        command=payload.get("command"),
                        argv=payload.get("argv"),
                        client=self.client_address[0],
                    )
                    self._send_json(200, result)
                elif path == "/v1/debug/commands/import":
                    self._send_json(200, state.traces.import_payload(payload))
                elif path == "/v1/time/pause":
                    self._send_json(200, {"clock": {"simulated_time": _format_dt(state.clock.pause()), "paused": True}})
                elif path == "/v1/time/resume":
                    self._send_json(200, {"clock": {"simulated_time": _format_dt(state.clock.resume()), "paused": False}})
                elif path == "/v1/time/seek":
                    timestamp = str(payload.get("timestamp", ""))
                    self._send_json(200, {"clock": {"simulated_time": _format_dt(state.clock.seek(timestamp))}})
                elif path == "/v1/mutations/reset":
                    state.mutations.reset()
                    self._send_json(200, {"mutations": state.mutations.summary()})
                else:
                    self._send_json(404, {"error": "not found"})
            except RequestBodyTooLarge as exc:
                self._remember_structured_error(exc)
                self._send_json(413, {"error": str(exc)})
            except ValueError as exc:
                self._remember_structured_error(exc)
                self._send_json(400, {"error": str(exc)})
            except Exception as exc:
                self._remember_structured_error(exc)
                self._send_json(500, {"error": str(exc)})

        def do_PUT(self) -> None:
            self._handle_mutating_method("PUT")

        def do_PATCH(self) -> None:
            self._handle_mutating_method("PATCH")

        def do_DELETE(self) -> None:
            self._handle_mutating_method("DELETE")

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self._send_common_headers(
                "text/plain; charset=utf-8",
                0,
                extra_headers=self._cors_preflight_headers(),
            )
            self.end_headers()

        def _handle_mutating_method(self, method: str) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            if not self._is_authorized():
                self._send_unauthorized(path)
                return
            if self._send_rate_limited(path):
                return
            if _is_kubernetes_api_path(path):
                api_started = time.perf_counter()
                try:
                    payload = (
                        _read_json_body(self, security.max_body_bytes)
                        if method in {"PUT", "PATCH"} else {}
                    )
                except RequestBodyTooLarge as exc:
                    api_response = _k8s_status_response(
                        413,
                        str(exc),
                        "RequestEntityTooLarge",
                        "unsupported",
                        "k8s.body.too_large",
                    )
                except ValueError as exc:
                    api_response = _k8s_status_response(
                        400,
                        str(exc),
                        "BadRequest",
                        "unsupported",
                        "k8s.body.invalid",
                    )
                else:
                    api_response = kubernetes_api_mutating_response(state, method, path, payload)
                record_kubernetes_api_call(
                    state,
                    method=method,
                    path=path,
                    query=query,
                    response=api_response,
                    client=self.client_address[0],
                    user_agent=self.headers.get("user-agent", ""),
                    latency_ms=(time.perf_counter() - api_started) * 1000.0,
                )
                self._send_kubernetes_api_response(api_response)
                return
            self._send_json(405, {"error": f"{method} is not supported"})

        def _is_authorized(self) -> bool:
            if not security.auth_token:
                return True
            supplied = self.headers.get("authorization", "")
            expected = f"Bearer {security.auth_token}"
            return hmac.compare_digest(supplied, expected)

        def _send_rate_limited(self, path: str) -> bool:
            bucket = _rate_limit_bucket(path)
            if not bucket:
                return False
            decision = rate_limiter.check(self.client_address[0], bucket)
            if decision.allowed:
                return False
            headers = {"retry-after": str(decision.retry_after_seconds)}
            if _is_kubernetes_api_path(path):
                self._send_kubernetes_api_response(
                    _k8s_status_response(
                        429,
                        "rate limit exceeded",
                        "TooManyRequests",
                        "unsupported",
                        "k8s.rate_limited",
                    ),
                    extra_headers=headers,
                )
            else:
                self._send_json(429, {"error": "rate limit exceeded"}, extra_headers=headers)
            return True

        def _send_unauthorized(self, path: str) -> None:
            if _is_kubernetes_api_path(path):
                response = _k8s_status_response(
                    401,
                    "missing or invalid bearer token",
                    "Unauthorized",
                    "unsupported",
                    "k8s.auth.unauthorized",
                )
                self._send_kubernetes_api_response(response)
                return
            self._send_json(
                401,
                {"error": "missing or invalid bearer token"},
                extra_headers={"www-authenticate": "Bearer"},
            )

        def _send_json(
            self,
            status: int,
            payload: Any,
            *,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self._send_common_headers(
                "application/json; charset=utf-8",
                len(body),
                extra_headers=extra_headers,
            )
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, status: int, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self._send_common_headers("text/plain; charset=utf-8", len(body))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, status: int, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self._send_common_headers("text/html; charset=utf-8", len(body))
            self.end_headers()
            self.wfile.write(body)

        def _send_kubernetes_api_response(
            self,
            response: KubernetesApiResponse,
            *,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            if isinstance(response.body, str):
                body = response.body.encode("utf-8")
            else:
                body = json.dumps(response.body, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(response.status)
            headers = dict(extra_headers or {})
            if response.status == 401:
                headers.setdefault("www-authenticate", "Bearer")
            self._send_common_headers(
                response.content_type,
                len(body),
                extra_headers=headers,
            )
            self.end_headers()
            self.wfile.write(body)

        def _send_common_headers(
            self,
            content_type: str,
            content_length: int,
            *,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            self._response_bytes = content_length
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(content_length))
            self.send_header("cache-control", "no-store")
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("referrer-policy", "no-referrer")
            self.send_header("x-frame-options", "DENY")
            self._send_cors_headers()
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)

        def _send_cors_headers(self) -> None:
            for key, value in self._cors_response_headers().items():
                self.send_header(key, value)

        def _cors_response_headers(self) -> dict[str, str]:
            allowed_origin = security.cors_allow_origin.strip()
            if not allowed_origin:
                return {}
            headers = {"vary": "Origin"}
            origin = self.headers.get("origin", "").strip()
            if allowed_origin == "*":
                headers["access-control-allow-origin"] = "*"
            elif origin == allowed_origin:
                headers["access-control-allow-origin"] = origin
            return headers

        def _cors_preflight_headers(self) -> dict[str, str]:
            if not security.cors_allow_origin.strip():
                return {}
            return {
                "access-control-allow-methods": CORS_ALLOW_METHODS,
                "access-control-allow-headers": CORS_ALLOW_HEADERS,
                "access-control-max-age": "600",
            }

        def _server_url(self) -> str:
            host = self.headers.get("host")
            if not host:
                bound_host, bound_port = self.server.server_address[:2]
                host = f"{bound_host}:{bound_port}"
            return f"http://{host}"

        def _send_debug_events(self) -> None:
            self._send_event_stream_headers()
            if state.shutdown_event.is_set():
                self._send_shutdown_event()
                return
            last_version = -1
            for _ in range(300):
                if state.shutdown_event.is_set():
                    self._send_shutdown_event()
                    return
                version = state.traces.version
                if version != last_version:
                    payload = json.dumps({"version": version})
                    if not self._write_event_stream(
                        f"event: commands\ndata: {payload}\n\n".encode("utf-8")
                    ):
                        return
                    if not self._flush_event_stream():
                        return
                    last_version = version
                if state.shutdown_event.wait(1.0):
                    self._send_shutdown_event()
                    return

        def _send_event_stream_headers(self) -> None:
            self.send_response(200)
            self._response_bytes = 0
            self.send_header("content-type", "text/event-stream")
            self.send_header("cache-control", "no-cache")
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("referrer-policy", "no-referrer")
            self._send_cors_headers()
            self.end_headers()

        def _send_shutdown_event(self) -> bool:
            payload = json.dumps({"reason": "server shutdown"})
            if not self._write_event_stream(
                f"event: shutdown\ndata: {payload}\n\n".encode("utf-8")
            ):
                return False
            return self._flush_event_stream()

        def _write_event_stream(self, payload: bytes) -> bool:
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return False
            return True

        def _remember_structured_error(self, exc: BaseException) -> None:
            self._structured_error = {
                "error_type": type(exc).__name__,
                "message": str(exc),
            }

        def _write_structured_logs(self) -> None:
            if request_logger is None:
                return
            raw_path = getattr(self, "path", "")
            parsed = urllib.parse.urlparse(raw_path)
            query = urllib.parse.parse_qs(parsed.query)
            now = _dt.datetime.now(_dt.timezone.utc).isoformat()
            status = int(getattr(self, "_response_status", 0) or 0)
            duration_ms = round(
                (time.perf_counter() - getattr(self, "_request_started_at", time.perf_counter()))
                * 1000.0,
                3,
            )
            base_record = {
                "timestamp": now,
                "method": getattr(self, "command", ""),
                "path": parsed.path,
                "query": _redact_query(query),
                "status": status,
                "client": self.client_address[0] if self.client_address else "",
                "user_agent": self.headers.get("user-agent", "") if hasattr(self, "headers") else "",
                "authorization": (
                    "present"
                    if hasattr(self, "headers") and self.headers.get("authorization")
                    else "absent"
                ),
                "duration_ms": duration_ms,
                "response_bytes": int(getattr(self, "_response_bytes", 0) or 0),
            }
            request_logger.log_request(base_record)
            if self._structured_error is not None:
                request_logger.log_error({**base_record, **self._structured_error})

        def _flush_event_stream(self) -> bool:
            try:
                self.wfile.flush()
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return False
            return True

        def _send_log_stream(self) -> None:
            self._send_event_stream_headers()
            if state.shutdown_event.is_set():
                self._send_shutdown_event()
                return
            last_generation = -1
            last_signature: tuple[int, int] | None = None
            for _ in range(300):
                if state.shutdown_event.is_set():
                    self._send_shutdown_event()
                    return
                with state.generation.lock:
                    generation = state.generation.generation_count
                    last_seed = state.generation.last_seed
                signature = _log_file_signature(state.output_dir / "metric_report.log")
                if generation != last_generation or signature != last_signature:
                    payload = json.dumps({
                        "generation_count": generation,
                        "last_seed": last_seed,
                    })
                    if not self._write_event_stream(
                        f"event: generation\ndata: {payload}\n\n".encode("utf-8")
                    ):
                        return
                    if not self._send_log_file():
                        return
                    if not self._flush_event_stream():
                        return
                    last_generation = generation
                    last_signature = signature
                if state.shutdown_event.wait(1.0):
                    self._send_shutdown_event()
                    return

        def _send_mcp_post(self) -> None:
            """Answer one streamable-HTTP MCP message at POST /mcp.

            The JSON-RPC layer (parse errors, dispatch, tool errors) lives
            in server_mcp; this method only moves bytes and maps the shared
            body cap onto a JSON-RPC-shaped 413 instead of the app-endpoint
            error shape.
            """
            try:
                raw = server_mcp.read_mcp_request_body(self, security.max_body_bytes)
            except RequestBodyTooLarge as exc:
                self._remember_structured_error(exc)
                self._send_json(413, server_mcp.body_too_large_response(str(exc)))
                return
            status, body = server_mcp.handle_mcp_http_post(state, raw)
            if body is None:
                # Notification: 202 Accepted with no content.
                self.send_response(status)
                self._send_common_headers("application/json; charset=utf-8", 0)
                self.end_headers()
                return
            self._send_json(status, body)

        def _send_log_file(self) -> bool:
            # Safe against concurrent regeneration by construction: the
            # generator publishes metric_report.log via legacy's
            # _atomic_artifact_open (temp sibling + os.replace), so this
            # open() only ever sees the complete previous or complete new
            # file, and a continuous-generate rerun with the same emit
            # selection never deletes it. The not-present branch below is
            # for runs that genuinely dropped `logs` from --emit.
            log_path = state.output_dir / "metric_report.log"
            if not log_path.exists():
                payload = json.dumps({"line": "metric_report.log is not present for this run"})
                return self._write_event_stream(f"data: {payload}\n\n".encode("utf-8"))
            with open(log_path, encoding="utf-8") as f:
                for line in f:
                    payload = json.dumps({"line": line.rstrip("\n")})
                    if not self._write_event_stream(f"data: {payload}\n\n".encode("utf-8")):
                        return False
            return True

    return _Handler


def _log_file_signature(path: Path) -> tuple[int, int] | None:
    with contextlib.suppress(OSError):
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    return None


def _query_int(query: dict[str, list[str]], name: str, default: int) -> int:
    try:
        return int(query.get(name, [str(default)])[0])
    except ValueError:
        return default


def _query_str(query: dict[str, list[str]], name: str, default: str) -> str:
    return query.get(name, [default])[0].strip()


def _scenario_payload(state: SimulationState) -> dict[str, Any]:
    return {
        "active": list(state.active_scenarios),
        "known": [
            _scenario_detail_payload(state, slug, scenario)
            for slug, scenario in state.legacy.SCENARIOS.items()
        ],
    }


def _scenario_detail_payload(state: SimulationState, slug: str, scenario: Any) -> dict[str, Any]:
    profile = OPS_SCENARIO_PROFILES.get(slug)
    return {
        "id": slug,
        "name": scenario.name,
        "severity": scenario.severity,
        "days_required": scenario.days_required,
        "category": scenario.category,
        "active": slug in state.active_scenarios,
        "components_touched": list(scenario.components_touched),
        "primary_specs": [
            _scenario_spec_payload(component, spec)
            for component, spec in scenario.primary_specs
        ],
        "cascade_specs": [
            _scenario_spec_payload(component, spec)
            for component, spec in scenario.cascade_specs
        ],
        "ops_profile": profile is not None,
        "ops_profile_detail": _ops_profile_payload(profile) if profile is not None else None,
    }


def _scenario_spec_payload(component: str, spec: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "component": component,
        "metric": str(spec.get("metric", "")),
        "description": str(spec.get("description", "")),
        "time_offset_seconds": spec.get("time_offset"),
        "duration_seconds": spec.get("duration_seconds"),
        "shape": spec.get("shape"),
        "severity": spec.get("severity", ""),
    }
    shape_params = spec.get("shape_params")
    if shape_params is not None:
        payload["shape_params"] = _json_safe_payload(shape_params)
    instance_filter = spec.get("instance_filter")
    if instance_filter is not None:
        payload["instance_filter"] = _json_safe_payload(instance_filter)
    return payload


def _ops_profile_payload(profile: OpsScenarioProfile) -> dict[str, Any]:
    return {
        "summary": profile.summary,
        "affected_components": list(profile.affected_components),
        "events": list(profile.events),
        "logs": list(profile.logs),
        "helm_notes": profile.helm_notes,
        "rollout_note": profile.rollout_note,
        "impacts": [
            {
                "component": impact.component,
                "deployment_status": impact.deployment_status,
                "pod_status": impact.pod_status,
                "ready": impact.ready,
                "ready_replicas": impact.ready_replicas,
                "ready_replicas_delta": impact.ready_replicas_delta,
                "restarts": impact.restarts,
                "cpu_pct": impact.cpu_pct,
                "cpu_m": impact.cpu_m,
                "memory_mi": impact.memory_mi,
                "memory_pct": impact.memory_pct,
                "pvc_used_pct": impact.pvc_used_pct,
            }
            for impact in profile.impacts
        ],
    }


def _json_safe_payload(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if callable(value):
        return "<callable>"
    if isinstance(value, dict):
        return {
            str(key): _json_safe_payload(value[key])
            for key in sorted(value, key=str)
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe_payload(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(
            (_json_safe_payload(item) for item in value),
            key=_json_safe_sort_key,
        )
    return str(value)


def _json_safe_sort_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))





_SERVE_CONFIG_SERVER_KEYS = {
    "host",
    "port",
    "namespace",
    "debug_ring_size",
    "persist_command_log",
    "persist_command_db",
    "persist_command_retention",
    "auth_token",
    "max_request_body_bytes",
    "allow_remote_without_auth",
    "cors_allow_origin",
    "rate_limit_per_minute",
    "structured_log",
    "structured_log_file",
    "no_generate",
    "continuous_generate",
    "continuous_generate_interval_seconds",
}


def _load_serve_config(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    is_yaml = suffix in {".yaml", ".yml"}
    if suffix not in {".json", ".yaml", ".yml"}:
        raise ValueError(
            f"--config must be a .json, .yaml, or .yml file; got {path}"
        )
    if is_yaml:
        try:
            import yaml
        except ImportError as exc:
            raise ValueError(
                f"--config {path}: PyYAML is required to parse YAML files "
                "but is not installed. Install it with 'pip install pyyaml' "
                "or use a .json file instead."
            ) from exc
        parse_exc_types: tuple[type[Exception], ...] = (
            yaml.YAMLError,
            UnicodeDecodeError,
        )
    else:
        parse_exc_types = (json.JSONDecodeError, UnicodeDecodeError)
    try:
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) if is_yaml else json.load(f)
    except OSError as exc:
        raise ValueError(f"--config {path}: failed to read file: {exc}") from exc
    except parse_exc_types as exc:
        label = "YAML" if is_yaml else "JSON"
        raise ValueError(f"--config {path}: failed to parse {label}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("--config must contain a JSON/YAML object")
    unknown_top = set(raw) - {"server", "generate"}
    if unknown_top:
        raise ValueError(
            "--config only accepts top-level 'server' and 'generate' keys; "
            f"got {', '.join(sorted(unknown_top))}"
        )
    server = raw.get("server", {})
    generate = raw.get("generate", {})
    if not isinstance(server, dict):
        raise ValueError("--config server must be an object")
    if not isinstance(generate, dict):
        raise ValueError("--config generate must be an object")
    unknown_server = set(server) - _SERVE_CONFIG_SERVER_KEYS
    if unknown_server:
        raise ValueError(
            "--config server contains unknown key(s): "
            + ", ".join(sorted(unknown_server))
        )
    return {"server": dict(server), "generate": dict(generate)}


def _extract_serve_config_path(
    argv: list[str],
    parser: argparse.ArgumentParser,
) -> Path | None:
    config_parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    config_parser.add_argument("--config", type=Path)
    try:
        config_args, _ = config_parser.parse_known_args(argv)
    except SystemExit:
        parser.error("--config requires a file path")
    return config_args.config


def _strip_serve_config_arg(argv: list[str]) -> list[str]:
    result: list[str] = []
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        if token == "--config":
            skip_next = True
            continue
        if token.startswith("--config="):
            continue
        result.append(token)
    return result


def _config_mapping_to_argv(config: dict[str, Any]) -> list[str]:
    argv: list[str] = []
    for key, value in config.items():
        if value is None:
            continue
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                argv.append(flag)
            continue
        if isinstance(value, (list, tuple)):
            argv.extend([flag, ",".join(str(item) for item in value)])
            continue
        argv.extend([flag, str(value)])
    return argv


def _parse_serve_args(
    argv: list[str],
    parser: argparse.ArgumentParser,
) -> tuple[argparse.Namespace, list[str]]:
    raw_argv = list(argv)
    config_path = _extract_serve_config_path(raw_argv, parser)
    config_server_argv: list[str] = []
    config_generate_argv: list[str] = []
    if config_path is not None:
        try:
            config = _load_serve_config(config_path)
        except ValueError as exc:
            parser.error(str(exc))
        config_server_argv = _config_mapping_to_argv(config["server"])
        config_generate_argv = _config_mapping_to_argv(config["generate"])
    user_argv = _strip_serve_config_arg(raw_argv)
    serve_args, generate_argv = parser.parse_known_args(
        [*config_server_argv, *config_generate_argv, *user_argv]
    )
    serve_args.config = config_path
    return serve_args, generate_argv


def _build_serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anomaly-metric-creator.py serve",
        description="Run the anomaly simulator as an HTTP server with Kubernetes/Helm command responses.",
        epilog=(
            "Any unrecognized options are parsed as normal generate options "
            "(for example --scenarios, --components, --duration-days, "
            "--otel-send, and --otel-endpoint)."
        ),
        allow_abbrev=False,
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8088, help="HTTP bind port (default: 8088; use 0 for ephemeral).")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE, help=f"Simulated Kubernetes namespace (default: {DEFAULT_NAMESPACE}).")
    parser.add_argument("--debug-ring-size", type=int, default=DEFAULT_TRACE_LIMIT, help=f"Command trace ring size (default: {DEFAULT_TRACE_LIMIT}).")
    parser.add_argument("--persist-command-log", type=Path, default=None, help="Optional JSONL path for command traces.")
    parser.add_argument("--persist-command-db", type=Path, default=None, help="Optional SQLite path for durable command traces and search.")
    parser.add_argument("--persist-command-retention", type=int, default=0, help="Maximum SQLite command traces to retain (default: 0, unlimited).")
    parser.add_argument("--config", type=Path, default=None, help="Optional JSON/YAML file with serve-mode server and generate defaults.")
    parser.add_argument("--auth-token", default="", help="Optional bearer token required for simulator HTTP and Kubernetes API requests.")
    parser.add_argument("--max-request-body-bytes", type=int, default=DEFAULT_MAX_BODY_BYTES, help=f"Maximum accepted HTTP request body size (default: {DEFAULT_MAX_BODY_BYTES}).")
    parser.add_argument("--allow-remote-without-auth", action="store_true", help="Allow non-loopback binds without --auth-token for isolated lab use.")
    parser.add_argument("--cors-allow-origin", default="", help="Optional CORS Access-Control-Allow-Origin value for browser clients.")
    parser.add_argument("--rate-limit-per-minute", type=int, default=0, help="Optional per-client command/Kubernetes API request limit per minute (default: 0, off).")
    parser.add_argument("--structured-log", action=argparse.BooleanOptionalAction, default=False, help="Emit structured JSONL request/error logs to stderr or --structured-log-file.")
    parser.add_argument("--structured-log-file", type=Path, default=None, help="Optional JSONL path for structured request/error logs.")
    parser.add_argument("--no-generate", action="store_true", help="Use existing artifacts in --output-dir instead of generating before serving.")
    parser.add_argument("--continuous-generate", action="store_true", help="Continuously regenerate artifacts while the server runs.")
    parser.add_argument("--continuous-generate-interval-seconds", type=float, default=60.0, help="Seconds between continuous generation passes (default: 60).")
    return parser


def _is_loopback_bind_host(host: str) -> bool:
    value = host.strip().strip("[]").lower()
    if value in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def serve_main(argv: list[str] | None = None, *, legacy_module: Any | None = None) -> None:
    if legacy_module is None:
        from . import legacy as legacy_module

    parser = _build_serve_parser()
    serve_args, generate_argv = _parse_serve_args(list(argv or []), parser)
    if serve_args.debug_ring_size < 1:
        parser.error("--debug-ring-size must be >= 1")
    if serve_args.port < 0 or serve_args.port > 65535:
        parser.error("--port must be in [0, 65535]")
    if serve_args.max_request_body_bytes < 1:
        parser.error("--max-request-body-bytes must be >= 1")
    if serve_args.rate_limit_per_minute < 0:
        parser.error("--rate-limit-per-minute must be >= 0")
    if serve_args.continuous_generate_interval_seconds <= 0:
        parser.error("--continuous-generate-interval-seconds must be > 0")
    if (
        not _is_loopback_bind_host(serve_args.host)
        and not serve_args.auth_token
        and not serve_args.allow_remote_without_auth
    ):
        parser.error(
            "--host binds outside loopback; pass --auth-token or "
            "--allow-remote-without-auth"
        )

    args = legacy_module.parse_args(generate_argv)
    if not serve_args.no_generate:
        run_argv = _generation_argv_without_otel(generate_argv)
        legacy_module.main(run_argv)
        args = legacy_module.parse_args(generate_argv)

    state = build_state(
        legacy_module,
        args,
        namespace=serve_args.namespace,
        trace_limit=serve_args.debug_ring_size,
        persist_command_log=serve_args.persist_command_log,
        persist_command_db=serve_args.persist_command_db,
        persist_command_retention=serve_args.persist_command_retention,
    )
    generation_stop = _start_continuous_generation(
        state,
        generate_argv,
        enabled=serve_args.continuous_generate,
        interval_seconds=serve_args.continuous_generate_interval_seconds,
        stream_otel=bool(getattr(args, "otel_enabled", False)),
    )
    if not serve_args.continuous_generate:
        _start_otel_background(state)

    security = ServerSecurityConfig(
        auth_token=serve_args.auth_token,
        max_body_bytes=serve_args.max_request_body_bytes,
        allow_remote_without_auth=serve_args.allow_remote_without_auth,
        cors_allow_origin=serve_args.cors_allow_origin,
        rate_limit_per_minute=serve_args.rate_limit_per_minute,
    )
    request_logger = None
    if serve_args.structured_log or serve_args.structured_log_file is not None:
        request_logger = StructuredRequestLogger(serve_args.structured_log_file)
    httpd = ThreadingHTTPServer(
        (serve_args.host, serve_args.port),
        make_handler(state, security=security, request_logger=request_logger),
    )
    host, port = httpd.server_address
    print(f"AMC simulator server listening on http://{host}:{port}/debug")
    print(f"Command API: POST http://{host}:{port}/v1/commands")
    print(f"Kubeconfig: http://{host}:{port}/v1/kubeconfig")
    if security.auth_token:
        print("Bearer auth: enabled")
    elif not _is_loopback_bind_host(serve_args.host):
        print("WARNING: remote bind is running without bearer auth", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nAMC simulator server stopping", file=sys.stderr)
    finally:
        state.shutdown_event.set()
        _stop_continuous_generation(generation_stop)
        httpd.server_close()


def _generation_argv_without_otel(generate_argv: list[str]) -> list[str]:
    # Server mode owns OTEL streaming separately from one-shot artifact
    # generation. The generation pass still writes metrics/logs/traces, but
    # this override prevents legacy main() from streaming and blocking server
    # startup or the continuous generation loop.
    return [*generate_argv, "--otel-send", "none"]


def _start_continuous_generation(
    state: SimulationState,
    generate_argv: list[str],
    *,
    enabled: bool,
    interval_seconds: float,
    stream_otel: bool = False,
) -> threading.Event | None:
    with state.generation.lock:
        state.generation.enabled = enabled
        state.generation.interval_seconds = interval_seconds
        state.generation.thread = "not_started" if enabled else "disabled"
        state.generation.last_anomaly_count = len(state.anomaly_rows)
    if not enabled:
        return None
    if stream_otel:
        state.otel_status["thread"] = "waiting"
        state.otel_status["continuous"] = True
    else:
        state.otel_status["thread"] = "disabled"
        state.otel_status["continuous"] = False

    stop_event = threading.Event()

    def _run() -> None:
        with state.generation.lock:
            state.generation.thread = "running"
        if stream_otel:
            _stream_current_otel_once(state, idle_thread_state="waiting")
        while not stop_event.wait(interval_seconds):
            _run_continuous_generation_once(state, generate_argv, stream_otel=stream_otel)
        with state.generation.lock:
            state.generation.thread = "stopped"
        if stream_otel:
            state.otel_status["thread"] = "stopped"

    thread = threading.Thread(target=_run, name="amc-continuous-generation", daemon=True)
    stop_event.worker_thread = thread
    thread.start()
    return stop_event


def _stop_continuous_generation(
    stop_event: threading.Event | None,
    *,
    timeout: float = 5.0,
) -> None:
    if stop_event is None:
        return
    stop_event.set()
    worker = getattr(stop_event, "worker_thread", None)
    if worker is None:
        return
    is_alive = getattr(worker, "is_alive", None)
    join = getattr(worker, "join", None)
    if not callable(join):
        return
    if callable(is_alive) and not is_alive():
        return
    join(timeout)


def _run_continuous_generation_once(
    state: SimulationState,
    generate_argv: list[str],
    *,
    stream_otel: bool = False,
) -> None:
    # Artifact visibility during the rerun: every writer inside main()
    # publishes via legacy's _atomic_artifact_open, so HTTP reader threads
    # observe each artifact switch from the previous run's complete content
    # to the new run's complete content with no truncated/missing window.
    # The combine/gauge writers only read CSVs published earlier in the same
    # single-threaded main() call, and reruns are serialized on this one
    # worker thread, so no combine pass can be mid-read on a CSV another
    # rerun is swapping.
    with state.generation.lock:
        next_count = state.generation.generation_count + 1
        seed = int(getattr(state.args, "seed", 42)) + next_count
        state.generation.thread = "running"
        state.generation.last_started_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
        state.generation.last_seed = seed
        state.generation.last_error = ""
    run_argv = [*_generation_argv_without_otel(generate_argv), "--seed", str(seed)]
    try:
        state.legacy.main(run_argv)
        rows = load_anomaly_rows(state.output_dir / "anomalies.csv")
        state.replace_generated_rows(rows)
    except SystemExit as exc:  # pragma: no cover - defensive background boundary
        _record_continuous_generation_failure(state, exc)
        return
    except Exception as exc:  # pragma: no cover - defensive background boundary
        _record_continuous_generation_failure(state, exc)
        return
    with state.generation.lock:
        state.generation.generation_count = next_count
        state.generation.last_completed_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
        state.generation.last_anomaly_count = len(rows)
    if stream_otel:
        _stream_current_otel_once(state, idle_thread_state="waiting")


def _start_otel_background(state: SimulationState) -> None:
    args = state.args
    if not getattr(args, "otel_enabled", False):
        state.otel_status["thread"] = "disabled"
        return

    def _run() -> None:
        _stream_current_otel_once(state, idle_thread_state="completed")

    thread = threading.Thread(target=_run, name="amc-otel-stream", daemon=True)
    thread.start()


def _stream_current_otel_once(state: SimulationState, *, idle_thread_state: str) -> None:
    if not getattr(state.args, "otel_enabled", False):
        state.otel_status["thread"] = "disabled"
        return
    state.otel_status["thread"] = "running"
    state.otel_status["last_started_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    try:
        _run_otel_streams(state)
    except Exception as exc:  # pragma: no cover - defensive thread boundary
        state.otel_status["thread"] = "failed"
        state.otel_status["error"] = str(exc)
        return
    state.otel_status["thread"] = idle_thread_state
    state.otel_status["last_completed_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    state.otel_status["stream_batches"] = int(state.otel_status.get("stream_batches", 0)) + 1


def _run_otel_streams(state: SimulationState) -> None:
    args = state.args
    legacy = state.legacy
    endpoints = {
        "logs": args.otel_logs_endpoint,
        "metrics": args.otel_metrics_endpoint,
        "traces": args.otel_traces_endpoint,
    }
    signal_selection = getattr(args, "otel_signal_selection", None)
    if signal_selection is not None:
        signal_endpoints = {
            sig: (url if sig in signal_selection else None)
            for sig, url in endpoints.items()
        }
    else:
        signal_endpoints = endpoints
    auth_headers = {}
    for signal in ["logs", "metrics", "traces"]:
        token = getattr(args, f"otel_{signal}_auth_token")
        if token:
            auth_headers[signal] = {"Authorization": f"{args.otel_stream_auth_scheme} {token}"}

    if not args.otel_gauges_only and any(signal_endpoints.values()):
        sent = legacy.stream_otel_signals(
            signal_endpoints,
            state.generated_rows(),
            speedup=args.otel_stream_speedup,
            timeout_seconds=args.otel_stream_timeout_seconds,
            max_events=args.otel_stream_max_events,
            auth_headers=auth_headers,
            protocol=args.otel_stream_protocol,
            activity_log_path=args.otel_activity_log,
            verbose=args.otel_verbose,
        )
        state.otel_status["signal_events_sent"] = sent
    if args.otel_emit_gauges:
        component_csv_paths = {
            c: args.output_dir / f"{c}.csv" for c in sorted(args.components)
        }
        sent = legacy.stream_otel_gauges(
            component_csv_paths,
            endpoint=args.otel_metrics_endpoint,
            batch_seconds=args.otel_gauge_batch_seconds,
            metric_prefix=args.otel_gauge_metric_prefix,
            speedup=args.otel_stream_speedup,
            timeout_seconds=args.otel_stream_timeout_seconds,
            max_events=args.otel_stream_max_events,
            max_retries=3,
            auth_headers=auth_headers.get("metrics"),
            protocol=args.otel_stream_protocol,
            activity_log_path=args.otel_activity_log,
            verbose=args.otel_verbose,
            append_activity_log=not args.otel_gauges_only,
        )
        state.otel_status["gauge_requests_sent"] = sent


def start_test_server(
    state: SimulationState,
    security: ServerSecurityConfig | None = None,
    request_logger: StructuredRequestLogger | None = None,
) -> tuple[ThreadingHTTPServer, str]:
    """Start an ephemeral server for tests and return (server, base_url)."""

    httpd = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(state, security=security, request_logger=request_logger),
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    return httpd, f"http://{host}:{port}"


def temp_output_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="amc-server-"))

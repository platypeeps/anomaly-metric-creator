"""Ops simulation surfaces shared by the serve-mode HTTP facade.

This module owns the command entry points (``run_command``,
``render_command`` and the ``_render_kubectl`` dispatcher) and the historic
``server_ops.<name>`` compatibility surface. The runtime state
(``SimulationState``, ``build_state()``, the clock, refusal counters and
error sink) lives in ``server_ops_state.py``, client-command parsing in
``server_ops_parse.py``, the overlay-aware ``resource_snapshot()`` in
``server_ops_snapshot.py``, the read-only kubectl renderers in
``server_ops_render.py``, the manifest/patch renderers in
``server_ops_render_manifest.py``, the workload-operation renderers in
``server_ops_render_workloads.py``, the snapshot-bound explain / OpenAPI /
object builders in ``server_k8s_resources.py``, and the Kubernetes REST
dispatch spine in ``server_k8s_dispatch.py``; all are re-imported below.
``server.py`` imports and re-exports these names for compatibility.
"""

from __future__ import annotations

import datetime as _dt
import time
from typing import Any

from .server_mutations import (
    DEFAULT_NAMESPACE,
    HelmReleaseMutation as HelmReleaseMutation,
    SimulationMutations as SimulationMutations,
    WorkloadMutation as WorkloadMutation,
    _mutation_resource_key as _mutation_resource_key,
    _resource_prefix as _resource_prefix,
    load_persisted_mutations as load_persisted_mutations,
)
from .server_traces import (
    DEFAULT_TRACE_LIMIT as DEFAULT_TRACE_LIMIT,
    CommandTrace,
    CommandTraceStore as CommandTraceStore,
)

from .server_ops_support import (
    DEFAULT_RELEASE as DEFAULT_RELEASE,
    DEFAULT_CHART as DEFAULT_CHART,
    _snapshot_row_namespace as _snapshot_row_namespace,
    _snapshot_row_labels as _snapshot_row_labels,
    _parse_user_timestamp as _parse_user_timestamp,
    _parse_optional_timestamp as _parse_optional_timestamp,
    _string_dict as _string_dict,
    _k8s_list_resource_version as _k8s_list_resource_version,
)
# The pure k8s REST-facade builder/filter/format layer lives in the one-way leaf
# server_k8s_api.py (epic step 5). Re-imported at each member's original
# conceptual position so the historic server_ops.<name> surface and __all__ stay
# stable; the leaf never imports server_ops (TYPE_CHECKING SimulationState only).
from .server_k8s_api import (
    _K8S_ADVERTISED_VERSION as _K8S_ADVERTISED_VERSION,
    _K8S_ADVERTISED_TAG as _K8S_ADVERTISED_TAG,
    _K8S_ADVERTISED_GIT_VERSION as _K8S_ADVERTISED_GIT_VERSION,
)


from .server_ops_profiles import (
    OPS_SCENARIO_PROFILES as OPS_SCENARIO_PROFILES,
    OpsComponentImpact as OpsComponentImpact,
    OpsScenarioProfile as OpsScenarioProfile,
    _impact as _impact,
    _profile as _profile,
    validate_ops_profiles as validate_ops_profiles,
)


from .server_ops_state import (
    SimulationClock as SimulationClock,
)


# Client-command parsing lives in server_ops_parse.py (one-way import; the leaf
# never imports server_ops). Re-imported here at ParsedCommand's original
# position so the historic server_ops.<name> surface and __all__ stay stable.
# Only names the staying renderers use or that __all__ re-exports are re-imported;
# leaf-internal parse helpers (e.g. _split_flags's _store_flag_value, the explain
# token splitters, the raw flag tables) stay solely in the leaf.
from .server_ops_parse import (
    ParsedCommand as ParsedCommand,
    _SENSITIVE_FLAG_TOKENS as _SENSITIVE_FLAG_TOKENS,
    _MODELED_FLAGS as _MODELED_FLAGS,
    _KIND_ALIASES as _KIND_ALIASES,
    _EXPLAIN_RESOURCE_TARGETS as _EXPLAIN_RESOURCE_TARGETS,
    parse_command as parse_command,
    _split_flags as _split_flags,
    _flag_values as _flag_values,
    _first_flag_value as _first_flag_value,
    _parse_kubectl as _parse_kubectl,
    _parse_helm as _parse_helm,
    _split_resource_token as _split_resource_token,
    _normalize_kind as _normalize_kind,
    command_fingerprint as command_fingerprint,
    guess_intent as guess_intent,
    _redact_command_for_trace as _redact_command_for_trace,
    _redact_argv as _redact_argv,
    _redact_parsed_flags as _redact_parsed_flags,
    _is_sensitive_flag_name as _is_sensitive_flag_name,
)


from .server_command_render import (
    CommandResult as CommandResult,
    _exposed_active_scenarios as _exposed_active_scenarios,
    _is_dry_run as _is_dry_run,
    _table as _table,
    _unsupported as _unsupported,
)
from .server_mutations import _format_dt as _format_dt


from .server_k8s_api import KubernetesApiResponse as KubernetesApiResponse


from .server_ops_state import (
    ContinuousGenerationStatus as ContinuousGenerationStatus,
    _ErrorSink as _ErrorSink,
    _ERROR_TRACEBACK_MAX_LINES as _ERROR_TRACEBACK_MAX_LINES,
    _capture_traceback_tail as _capture_traceback_tail,
    _emit_error_record as _emit_error_record,
    _record_server_error as _record_server_error,
    _REFUSAL_KINDS as _REFUSAL_KINDS,
    RefusalCounters as RefusalCounters,
    SimulationState as SimulationState,
    build_state as build_state,
    load_anomaly_rows as load_anomaly_rows,
)


from .server_ops_snapshot import (
    _SNAPSHOT_KINDS as _SNAPSHOT_KINDS,
)
from .server_ops_render_manifest import (
    _MUTATION_SNAPSHOT_KINDS as _MUTATION_SNAPSHOT_KINDS,
)
from .server_ops_snapshot import (
    _CLUSTER_SCOPED_SNAPSHOT_KINDS as _CLUSTER_SCOPED_SNAPSHOT_KINDS,
    _NAMESPACED_SNAPSHOT_KINDS as _NAMESPACED_SNAPSHOT_KINDS,
)


from .server_k8s_resources import (
    _EXPLAIN_RESOURCE_DESCRIPTIONS as _EXPLAIN_RESOURCE_DESCRIPTIONS,
)


from .server_ops_snapshot import (
    _snapshot_row_key as _snapshot_row_key,
    _snapshot_kind_namespaced as _snapshot_kind_namespaced,
)


def run_command(
    state: SimulationState,
    *,
    command: str | None = None,
    argv: list[str] | tuple[str, ...] | None = None,
    client: str = "api",
    request_id: str = "",
) -> dict[str, Any]:
    started = time.perf_counter()
    received = _dt.datetime.now(_dt.timezone.utc).isoformat()
    parsed = parse_command(command=command, argv=argv, default_namespace=state.namespace)
    simulated_time = _format_dt(state.clock.now())
    result = render_command(state, parsed)
    latency_ms = (time.perf_counter() - started) * 1000.0
    fingerprint = command_fingerprint(parsed, result.support_status)
    redacted_raw_input = _redact_command_for_trace(parsed)
    trace = CommandTrace(
        id=state.traces.next_id(),
        received_at_wall_time=received,
        simulated_time=simulated_time,
        raw_input=redacted_raw_input,
        argv=_redact_argv(parsed.argv),
        client=client,
        command_family=parsed.family,
        verb=parsed.verb,
        resource_kind=parsed.resource_kind,
        resource_name=parsed.resource_name,
        namespace=parsed.namespace,
        parsed_flags=_redact_parsed_flags(parsed.flags),
        support_status=result.support_status,
        matched_rule_id=result.matched_rule_id,
        active_scenarios=state.active_scenarios,
        exit_code=result.exit_code,
        stdout_preview=_preview(result.stdout),
        stderr_preview=_preview(result.stderr),
        stdout=result.stdout,
        stderr=result.stderr,
        latency_ms=round(latency_ms, 3),
        fingerprint=fingerprint,
        guessed_intent=guess_intent(parsed),
        request_id=request_id,
    )
    state.traces.record(trace)
    # The stored trace keeps the real active_scenarios: the walled
    # /v1/debug/* and /v1/debug/commands/export surfaces are the eval
    # harness's scoring data. But /v1/commands is investigation-open, so the
    # echoed trace must be scrubbed in eval mode — otherwise every command
    # response carries the full active-scenario list regardless of the
    # command run. stdout/stderr are already render-redacted upstream.
    trace_dict = trace.to_dict()
    trace_dict["active_scenarios"] = list(_exposed_active_scenarios(state))
    return {
        "trace": trace_dict,
        "result": {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "support_status": result.support_status,
            "matched_rule_id": result.matched_rule_id,
        },
    }


def render_command(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    if parsed.parse_error:
        return CommandResult(2, "", parsed.parse_error + "\n", "unsupported", "parse.error")
    if parsed.family == "kubectl":
        return _with_flag_support(parsed, _render_kubectl(state, parsed))
    if parsed.family == "helm":
        return _with_flag_support(parsed, _render_helm(state, parsed))
    return CommandResult(
        127,
        "",
        f"{parsed.family or 'command'}: command not supported by simulator\n",
        "unsupported",
        "family.unsupported",
    )


def _with_flag_support(parsed: ParsedCommand, result: CommandResult) -> CommandResult:
    if result.support_status != "supported":
        return result
    unmodeled = sorted(flag for flag in parsed.flags if flag not in _MODELED_FLAGS)
    if not unmodeled:
        return result
    warning = "warning: flag(s) parsed but not modeled: " + ", ".join(unmodeled) + "\n"
    return CommandResult(
        result.exit_code,
        result.stdout,
        result.stderr + warning,
        "partial",
        result.matched_rule_id + ".partial-flags",
    )


def _render_kubectl(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    kind = parsed.resource_kind
    if parsed.verb == "version":
        return CommandResult(0, _render_kubectl_version(), "", "supported", "kubectl.version")
    if parsed.verb == "api-versions":
        return CommandResult(0, _render_kubectl_api_versions(), "", "supported", "kubectl.api-versions")
    if parsed.verb == "api-resources":
        return CommandResult(0, _render_kubectl_api_resources(), "", "supported", "kubectl.api-resources")
    if parsed.verb == "cluster-info":
        return CommandResult(0, _render_kubectl_cluster_info(), "", "supported", "kubectl.cluster-info")
    if parsed.verb == "explain":
        return _render_explain(state, parsed)
    if parsed.verb == "config current-context":
        return CommandResult(0, "amc-simulator\n", "", "supported", "kubectl.config.current-context")
    if parsed.verb == "config view":
        return CommandResult(
            0,
            render_kubeconfig("http://127.0.0.1:8088", state.namespace),
            "",
            "supported",
            "kubectl.config.view",
        )
    if parsed.verb == "auth can-i":
        return CommandResult(0, "yes\n", "", "supported", "kubectl.auth.can-i")
    if parsed.verb == "get":
        if parsed.flags.get("--watch") or parsed.flags.get("-w"):
            return _render_get_watch(state, kind, parsed)
        if kind in _SNAPSHOT_KINDS or kind == "all":
            return CommandResult(
                0, _render_get(state, kind, parsed), "", "supported", f"kubectl.get.{kind}"
            )
        return _unsupported(parsed, f"kubectl get {kind or '<missing-kind>'}")
    if parsed.verb == "describe":
        if kind in _SNAPSHOT_KINDS:
            return _render_describe(state, kind, parsed)
        return _unsupported(parsed, f"kubectl describe {kind or '<missing-kind>'}")
    if parsed.verb == "logs":
        if parsed.resource_name or _logs_uses_selector(parsed):
            return _render_logs_command(state, parsed)
        return CommandResult(1, "", "error: expected pod name for logs\n", "partial", "kubectl.logs.missing-pod")
    if parsed.verb == "top":
        if kind in {"pods", "nodes"}:
            return CommandResult(0, _render_top(state, kind), "", "supported", f"kubectl.top.{kind}")
        return _unsupported(parsed, f"kubectl top {kind or '<missing-kind>'}")
    if parsed.verb == "rollout status":
        if _is_deployment_rollout_target(parsed):
            return CommandResult(
                0, _render_rollout_status(state, parsed), "", "supported", "kubectl.rollout.status"
            )
        return _unsupported(parsed, "kubectl rollout status")
    if parsed.verb == "rollout history":
        if _is_deployment_rollout_target(parsed):
            return CommandResult(
                0, _render_rollout_history(state, parsed), "", "supported", "kubectl.rollout.history"
            )
        return _unsupported(parsed, "kubectl rollout history")
    if parsed.verb == "rollout restart":
        if _is_deployment_rollout_target(parsed):
            return CommandResult(
                0, _render_rollout_restart(state, parsed), "", "supported", "kubectl.rollout.restart"
            )
        return _unsupported(parsed, "kubectl rollout restart")
    if parsed.verb == "rollout pause":
        if _is_deployment_rollout_target(parsed):
            return CommandResult(
                0, _render_rollout_pause(state, parsed), "", "supported", "kubectl.rollout.pause"
            )
        return _unsupported(parsed, "kubectl rollout pause")
    if parsed.verb == "rollout resume":
        if _is_deployment_rollout_target(parsed):
            return CommandResult(
                0, _render_rollout_resume(state, parsed), "", "supported", "kubectl.rollout.resume"
            )
        return _unsupported(parsed, "kubectl rollout resume")
    if parsed.verb == "rollout undo":
        if _is_deployment_rollout_target(parsed):
            return CommandResult(
                0, _render_rollout_undo(state, parsed), "", "supported", "kubectl.rollout.undo"
            )
        return _unsupported(parsed, "kubectl rollout undo")
    if parsed.verb == "scale":
        scale_result = _render_scale(state, parsed)
        if isinstance(scale_result, CommandResult):
            return scale_result
        return CommandResult(0, scale_result, "", "supported", "kubectl.scale")
    if parsed.verb == "delete":
        delete_result = _render_delete(state, parsed)
        if isinstance(delete_result, CommandResult):
            return delete_result
        return CommandResult(0, delete_result, "", "supported", "kubectl.delete")
    if parsed.verb == "patch":
        return _render_patch(state, parsed)
    if parsed.verb == "diff":
        return _render_diff(state, parsed)
    if parsed.verb in {"apply", "create"}:
        return _render_apply(state, parsed)
    if parsed.verb == "wait":
        return CommandResult(0, _render_wait(state, parsed), "", "supported", "kubectl.wait")
    if parsed.verb == "exec":
        return CommandResult(0, _render_exec(state, parsed), "", "supported", "kubectl.exec")
    if parsed.verb == "port-forward":
        return CommandResult(
            0, _render_port_forward(parsed), "", "supported", "kubectl.port-forward"
        )
    return _unsupported(parsed, f"kubectl {parsed.verb or '<missing-verb>'}")


from .server_helm_impl import _render_helm  # noqa: F401  (re-import at original position)


from .server_ops_snapshot import (
    resource_snapshot as resource_snapshot,
    _apply_default_namespaces as _apply_default_namespaces,
    _apply_mutation_rows as _apply_mutation_rows,
)


from .server_ops_render import (
    _WATCH_COMMAND_NOTE as _WATCH_COMMAND_NOTE,
    _render_get_watch as _render_get_watch,
    _render_get as _render_get,
    _render_get_all as _render_get_all,
    _filter_snapshot_rows as _filter_snapshot_rows,
    _snapshot_row_matches_namespace as _snapshot_row_matches_namespace,
    _snapshot_row_matches_field_selector as _snapshot_row_matches_field_selector,
)


from .server_ops_render_workloads import (
    _normalized_resource_prefix as _normalized_resource_prefix,
)


from .server_ops_render import (
    _render_describe as _render_describe,
    _logs_uses_selector as _logs_uses_selector,
    _render_logs_command as _render_logs_command,
    _logs_target_pods as _logs_target_pods,
    _logs_container_name as _logs_container_name,
    _logs_has_container_flag as _logs_has_container_flag,
    _logs_since_time as _logs_since_time,
    _logs_tail_limit as _logs_tail_limit,
    _render_logs as _render_logs,
    _render_pod_logs as _render_pod_logs,
    _render_top as _render_top,
    _render_kubectl_version as _render_kubectl_version,
    _render_kubectl_api_versions as _render_kubectl_api_versions,
    _render_kubectl_api_resources as _render_kubectl_api_resources,
    _render_kubectl_cluster_info as _render_kubectl_cluster_info,
)


from .server_k8s_resources import (
    _render_explain as _render_explain,
    _explain_schema_for_kind as _explain_schema_for_kind,
    _minimal_k8s_object as _minimal_k8s_object,
)


# The pure explain / OpenAPI schema formatters live in the one-way leaf
# server_ops_explain.py (epic step 6a). Re-imported at the moved block's
# original position so server_ops.<name> and __all__ stay stable; the leaf
# has no intra-package imports and never imports server_ops.
from .server_ops_explain import (
    _openapi_schema_from_value as _openapi_schema_from_value,
    _explain_field_description as _explain_field_description,
    _explain_title as _explain_title,
    _explain_schema_at_path as _explain_schema_at_path,
    _format_explain as _format_explain,
    _format_recursive_explain_fields as _format_recursive_explain_fields,
    _explain_properties as _explain_properties,
    _explain_display_schema as _explain_display_schema,
    _explain_type_label as _explain_type_label,
    _explain_type_name as _explain_type_name,
)


from .server_ops_render_workloads import (
    _render_rollout_status as _render_rollout_status,
    _render_rollout_history as _render_rollout_history,
    _render_rollout_restart as _render_rollout_restart,
    _render_rollout_pause as _render_rollout_pause,
    _render_rollout_resume as _render_rollout_resume,
    _render_rollout_undo as _render_rollout_undo,
    _rollout_undo_revision as _rollout_undo_revision,
    _rollout_revision_label as _rollout_revision_label,
    _rollout_component as _rollout_component,
    _is_deployment_rollout_target as _is_deployment_rollout_target,
    _render_scale as _render_scale,
    _render_delete as _render_delete,
)


from .server_ops_render_manifest import (
    _render_patch as _render_patch,
    _patch_payload as _patch_payload,
    _patch_payload_text as _patch_payload_text,
    _patch_base_payload as _patch_base_payload,
    _deep_merge_patch as _deep_merge_patch,
)


# The RFC 6902 JSON Patch operations (RFC 6901 JSON Pointer paths) live in
# the one-way leaf server_ops_payloads.py (epic step 6a), re-imported at
# their original position; the leaf never imports server_ops.
from .server_ops_payloads import (
    _apply_json_patch as _apply_json_patch,
    _json_pointer_parts as _json_pointer_parts,
    _set_json_pointer as _set_json_pointer,
    _remove_json_pointer as _remove_json_pointer,
)


from .server_ops_render_manifest import (
    _render_diff as _render_diff,
    _render_apply as _render_apply,
    _render_create as _render_create,
    _manifest_apply_targets as _manifest_apply_targets,
)


# The manifest document reader lives in the one-way leaf
# server_ops_payloads.py (epic step 6a), re-imported at its original
# position; the leaf never imports server_ops.
from .server_ops_payloads import (
    _load_manifest_documents as _load_manifest_documents,
    _normalize_manifest_documents as _normalize_manifest_documents,
)


from .server_ops_render_manifest import (
    _manifest_apply_target as _manifest_apply_target,
    _resource_from_manifest_name as _resource_from_manifest_name,
    _mutation_snapshot_kind as _mutation_snapshot_kind,
)


from .server_ops_state import (
    _record_continuous_generation_failure as _record_continuous_generation_failure,
)


from .server_ops_render_manifest import (
    _generic_resource_row as _generic_resource_row,
    _generic_resource_metadata as _generic_resource_metadata,
    _configmap_keys_from_flags as _configmap_keys_from_flags,
)


from .server_ops_render_workloads import (
    _parsed_replicas as _parsed_replicas,
    _render_wait as _render_wait,
    _render_exec as _render_exec,
    _render_port_forward as _render_port_forward,
)

from .server_helm_impl import (  # noqa: F401  (re-import at original position)
    _render_helm_list,
    _render_helm_status,
    _render_helm_history,
    _render_helm_env,
    _render_helm_get,
    _render_helm_test,
    _render_helm_install,
    _render_helm_upgrade,
    _helm_value_overrides,
    _render_helm_rollback,
)


from .server_command_render import (
    _not_found as _not_found,
)


from .server_ops_snapshot import (
    _DEPLOYMENT_STATUS_PRIORITY as _DEPLOYMENT_STATUS_PRIORITY,
    _POD_STATUS_PRIORITY as _POD_STATUS_PRIORITY,
    _component_health as _component_health,
    _component_impacts as _component_impacts,
    _apply_component_impact as _apply_component_impact,
    _status_priority as _status_priority,
    _component_scenarios as _component_scenarios,
    _exposed_component_scenarios as _exposed_component_scenarios,
    _component_events as _component_events,
)


from .server_ops_render_workloads import (
    _component_rollout_notes as _component_rollout_notes,
)


from .server_ops_snapshot import (
    _event_rows as _event_rows,
    _node_rows as _node_rows,
)

from .server_helm_impl import (  # noqa: F401  (re-import at original position)
    _helm_release,
    _helm_notes,
    _helm_current_description,
)


from .server_ops_snapshot import (
    _replica_count as _replica_count,
    _pod_name as _pod_name,
)


from .server_ops_support import (
    _component_from_name as _component_from_name,
)


from .server_ops_snapshot import (
    _stable_cluster_ip as _stable_cluster_ip,
)


from .server_ops_support import (
    _find_named as _find_named,
)


from .server_ops_support import _preview as _preview


from .server_k8s_api import (
    RequestBodyTooLarge as RequestBodyTooLarge,
    _read_json_body as _read_json_body,
    _read_optional_json_body as _read_optional_json_body,
    _content_length as _content_length,
)


from .server_k8s_dispatch import (
    kubernetes_api_response as kubernetes_api_response,
)


from .server_k8s_resources import (
    _k8s_openapi_response as _k8s_openapi_response,
    _k8s_openapi_v2_document as _k8s_openapi_v2_document,
)


from .server_k8s_api import _k8s_openapi_v3_discovery as _k8s_openapi_v3_discovery


from .server_k8s_resources import (
    _k8s_openapi_v3_document as _k8s_openapi_v3_document,
    _openapi_schema_definitions as _openapi_schema_definitions,
    _openapi_paths as _openapi_paths,
)


from .server_k8s_api import (
    _openapi_operation as _openapi_operation,
    _openapi_list_schema as _openapi_list_schema,
    _openapi_schema_name as _openapi_schema_name,
    _openapi_list_schema_name as _openapi_list_schema_name,
    _openapi_group_versions as _openapi_group_versions,
    _openapi_group_version_from_path as _openapi_group_version_from_path,
)


from .server_k8s_dispatch import (
    kubernetes_api_post_response as kubernetes_api_post_response,
    kubernetes_api_mutating_response as kubernetes_api_mutating_response,
)


from .server_k8s_api import (
    _k8s_mutation_target as _k8s_mutation_target,
    _k8s_subresource_mutation_allowed as _k8s_subresource_mutation_allowed,
)


from .server_k8s_dispatch import (
    _k8s_mutated_object as _k8s_mutated_object,
)


from .server_k8s_api import (
    _payload_replicas as _payload_replicas,
    _k8s_scale as _k8s_scale,
    render_kubeconfig as render_kubeconfig,
)


from .server_k8s_dispatch import (
    record_kubernetes_api_call as record_kubernetes_api_call,
)


from .server_k8s_api_trace import (
    _redact_query as _redact_query,
    _is_sensitive_query_key as _is_sensitive_query_key,
)
from .server_k8s_api import (
    _k8s_json_response as _k8s_json_response,
    _k8s_text_response as _k8s_text_response,
    _k8s_status_response as _k8s_status_response,
    _k8s_read_only_response as _k8s_read_only_response,
    _k8s_read_only_status_args as _k8s_read_only_status_args,
    _k8s_api_group_list as _k8s_api_group_list,
    _k8s_api_group as _k8s_api_group,
)


from .server_k8s_dispatch import (
    _k8s_group_resource_response as _k8s_group_resource_response,
    _k8s_core_resource_response as _k8s_core_resource_response,
)


from .server_k8s_api import _k8s_api_resource_list as _k8s_api_resource_list


from .server_k8s_dispatch import (
    _k8s_resource_response as _k8s_resource_response,
)


from .server_k8s_api import (
    _filter_k8s_objects_by_namespace as _filter_k8s_objects_by_namespace,
    k8s_watch_plan as k8s_watch_plan,
)


from .server_k8s_dispatch import (
    k8s_watch_objects as k8s_watch_objects,
)


from .server_k8s_api import (
    k8s_watch_object_key as k8s_watch_object_key,
    k8s_watch_trace_response as k8s_watch_trace_response,
    _k8s_resource_meta as _k8s_resource_meta,
)


from .server_k8s_tables import (
    _accepts_table as _accepts_table,
    _k8s_table as _k8s_table,
    _k8s_column as _k8s_column,
    _k8s_table_schema as _k8s_table_schema,
    _k8s_pod_cells as _k8s_pod_cells,
    _k8s_pod_display_status as _k8s_pod_display_status,
    _k8s_deployment_cells as _k8s_deployment_cells,
    _k8s_service_cells as _k8s_service_cells,
    _k8s_endpoints_cells as _k8s_endpoints_cells,
    _k8s_endpointslice_cells as _k8s_endpointslice_cells,
    _k8s_event_cells as _k8s_event_cells,
    _k8s_hpa_cells as _k8s_hpa_cells,
    _k8s_node_cells as _k8s_node_cells,
    _k8s_replicaset_cells as _k8s_replicaset_cells,
    _k8s_daemonset_cells as _k8s_daemonset_cells,
    _k8s_pvc_cells as _k8s_pvc_cells,
    _k8s_statefulset_cells as _k8s_statefulset_cells,
    _k8s_ingress_cells as _k8s_ingress_cells,
    _k8s_secret_cells as _k8s_secret_cells,
    _k8s_configmap_cells as _k8s_configmap_cells,
    _k8s_serviceaccount_cells as _k8s_serviceaccount_cells,
    _k8s_job_cells as _k8s_job_cells,
    _k8s_cronjob_cells as _k8s_cronjob_cells,
    _k8s_namespace_cells as _k8s_namespace_cells,
    _k8s_default_cells as _k8s_default_cells,
)


from .server_k8s_resources import (
    _k8s_objects_for_resource as _k8s_objects_for_resource,
)


from .server_k8s_objects import (
    _k8s_namespace as _k8s_namespace,
    _k8s_pod as _k8s_pod,
    _k8s_configmap as _k8s_configmap,
    _k8s_secret as _k8s_secret,
    _k8s_serviceaccount as _k8s_serviceaccount,
    _k8s_deployment as _k8s_deployment,
    _k8s_replicaset as _k8s_replicaset,
    _k8s_daemonset as _k8s_daemonset,
    _k8s_statefulset as _k8s_statefulset,
    _k8s_service as _k8s_service,
    _k8s_endpoints as _k8s_endpoints,
    _k8s_event as _k8s_event,
    _k8s_hpa as _k8s_hpa,
    _k8s_job as _k8s_job,
    _k8s_cronjob as _k8s_cronjob,
    _k8s_pvc as _k8s_pvc,
    _k8s_ingress as _k8s_ingress,
    _k8s_node as _k8s_node,
    _k8s_pod_metrics as _k8s_pod_metrics,
    _k8s_node_metrics as _k8s_node_metrics,
    _k8s_metadata as _k8s_metadata,
    _k8s_metadata_for_row as _k8s_metadata_for_row,
    _row_selector as _row_selector,
    _row_template_labels as _row_template_labels,
    _selector_string as _selector_string,
    _k8s_owner_reference as _k8s_owner_reference,
    _k8s_workload_labels as _k8s_workload_labels,
    _k8s_container_state as _k8s_container_state,
    _k8s_timestamp as _k8s_timestamp,
    _stable_pod_ip as _stable_pod_ip,
)


from .server_k8s_resources import (
    _k8s_endpointslice as _k8s_endpointslice,
)

from .server_helm_impl import (  # noqa: F401  (re-import at original position)
    _helm_secret_objects,
    _helm_release_revisions,
    _helm_secret_object,
    _helm_encoded_release_data,
    _helm_release_payload,
)


from .server_k8s_api import (
    _filter_k8s_objects as _filter_k8s_objects,
    _matches_label_selector as _matches_label_selector,
    _matches_field_selector as _matches_field_selector,
    _selector_set_requirement as _selector_set_requirement,
    _split_selector as _split_selector,
    _nested_field as _nested_field,
)
from .server_k8s_api_trace import (
    _api_trace_body as _api_trace_body,
    _redact_large_secret_data as _redact_large_secret_data,
    _api_namespace as _api_namespace,
    _api_resource_kind as _api_resource_kind,
    _api_resource_name as _api_resource_name,
    _api_fingerprint as _api_fingerprint,
    _api_guess_intent as _api_guess_intent,
    _is_kubernetes_api_path as _is_kubernetes_api_path,
    _rate_limit_bucket as _rate_limit_bucket,
)


__all__ = [
    'DEFAULT_RELEASE',
    'DEFAULT_CHART',
    'DEFAULT_NAMESPACE',
    'OpsComponentImpact',
    'OpsScenarioProfile',
    '_impact',
    '_profile',
    'OPS_SCENARIO_PROFILES',
    'validate_ops_profiles',
    'SimulationClock',
    'ParsedCommand',
    'CommandResult',
    'KubernetesApiResponse',
    'ContinuousGenerationStatus',
    'SimulationState',
    'build_state',
    'load_anomaly_rows',
    '_snapshot_row_namespace',
    '_snapshot_row_key',
    '_snapshot_kind_namespaced',
    'run_command',
    'parse_command',
    '_split_flags',
    '_parse_kubectl',
    '_parse_helm',
    '_split_resource_token',
    '_normalize_kind',
    'render_command',
    '_with_flag_support',
    '_render_kubectl',
    '_render_helm',
    '_unsupported',
    'resource_snapshot',
    '_apply_default_namespaces',
    '_apply_mutation_rows',
    '_render_get',
    '_render_get_all',
    '_filter_snapshot_rows',
    '_snapshot_row_matches_namespace',
    '_snapshot_row_labels',
    '_snapshot_row_matches_field_selector',
    '_normalized_resource_prefix',
    '_render_describe',
    '_logs_uses_selector',
    '_render_logs_command',
    '_logs_target_pods',
    '_logs_container_name',
    '_logs_has_container_flag',
    '_logs_since_time',
    '_logs_tail_limit',
    '_render_logs',
    '_render_pod_logs',
    '_render_top',
    '_render_kubectl_version',
    '_render_kubectl_api_versions',
    '_render_kubectl_api_resources',
    '_render_kubectl_cluster_info',
    '_render_rollout_status',
    '_render_rollout_history',
    '_render_rollout_restart',
    '_render_rollout_pause',
    '_render_rollout_resume',
    '_render_rollout_undo',
    '_rollout_component',
    '_is_deployment_rollout_target',
    '_render_scale',
    '_render_delete',
    '_render_apply',
    '_resource_from_manifest_name',
    '_mutation_snapshot_kind',
    '_record_continuous_generation_failure',
    '_generic_resource_row',
    '_generic_resource_metadata',
    '_string_dict',
    '_configmap_keys_from_flags',
    '_parsed_replicas',
    '_render_wait',
    '_render_exec',
    '_render_port_forward',
    '_render_helm_list',
    '_render_helm_status',
    '_render_helm_history',
    '_render_helm_env',
    '_render_helm_get',
    '_render_helm_test',
    '_render_helm_install',
    '_render_helm_upgrade',
    '_helm_value_overrides',
    '_render_helm_rollback',
    '_not_found',
    '_component_health',
    '_component_impacts',
    '_apply_component_impact',
    '_status_priority',
    '_component_scenarios',
    '_exposed_active_scenarios',
    '_exposed_component_scenarios',
    '_component_events',
    '_component_rollout_notes',
    '_event_rows',
    '_node_rows',
    '_helm_release',
    '_helm_notes',
    '_helm_current_description',
    '_replica_count',
    '_pod_name',
    '_component_from_name',
    '_stable_cluster_ip',
    '_find_named',
    '_table',
    'command_fingerprint',
    'guess_intent',
    '_preview',
    '_redact_command_for_trace',
    '_redact_argv',
    '_redact_parsed_flags',
    '_is_sensitive_flag_name',
    '_format_dt',
    '_parse_user_timestamp',
    '_parse_optional_timestamp',
    'RequestBodyTooLarge',
    '_read_json_body',
    '_read_optional_json_body',
    '_content_length',
    'kubernetes_api_response',
    'kubernetes_api_post_response',
    'kubernetes_api_mutating_response',
    '_k8s_mutation_target',
    '_k8s_subresource_mutation_allowed',
    '_k8s_mutated_object',
    '_payload_replicas',
    '_k8s_scale',
    'render_kubeconfig',
    'record_kubernetes_api_call',
    '_redact_query',
    '_is_sensitive_query_key',
    '_k8s_json_response',
    '_k8s_text_response',
    '_k8s_status_response',
    '_k8s_read_only_response',
    '_k8s_read_only_status_args',
    '_k8s_api_group_list',
    '_k8s_api_group',
    '_k8s_group_resource_response',
    '_k8s_core_resource_response',
    '_k8s_api_resource_list',
    '_k8s_resource_response',
    '_filter_k8s_objects_by_namespace',
    '_k8s_list_resource_version',
    '_k8s_resource_meta',
    '_accepts_table',
    '_k8s_table',
    '_k8s_column',
    '_k8s_table_schema',
    '_k8s_pod_cells',
    '_k8s_pod_display_status',
    '_k8s_deployment_cells',
    '_k8s_service_cells',
    '_k8s_endpoints_cells',
    '_k8s_endpointslice_cells',
    '_k8s_event_cells',
    '_k8s_hpa_cells',
    '_k8s_node_cells',
    '_k8s_replicaset_cells',
    '_k8s_daemonset_cells',
    '_k8s_pvc_cells',
    '_k8s_statefulset_cells',
    '_k8s_ingress_cells',
    '_k8s_secret_cells',
    '_k8s_configmap_cells',
    '_k8s_serviceaccount_cells',
    '_k8s_job_cells',
    '_k8s_cronjob_cells',
    '_k8s_namespace_cells',
    '_k8s_default_cells',
    '_k8s_objects_for_resource',
    '_k8s_namespace',
    '_k8s_pod',
    '_k8s_configmap',
    '_k8s_secret',
    '_k8s_serviceaccount',
    '_k8s_deployment',
    '_k8s_replicaset',
    '_k8s_daemonset',
    '_k8s_statefulset',
    '_k8s_service',
    '_k8s_endpoints',
    '_k8s_event',
    '_k8s_hpa',
    '_k8s_job',
    '_k8s_cronjob',
    '_k8s_pvc',
    '_k8s_ingress',
    '_k8s_endpointslice',
    '_k8s_node',
    '_k8s_pod_metrics',
    '_k8s_node_metrics',
    '_helm_secret_objects',
    '_helm_release_revisions',
    '_helm_secret_object',
    '_helm_encoded_release_data',
    '_helm_release_payload',
    '_k8s_metadata',
    '_k8s_metadata_for_row',
    '_row_selector',
    '_row_template_labels',
    '_selector_string',
    '_k8s_owner_reference',
    '_k8s_workload_labels',
    '_k8s_container_state',
    '_filter_k8s_objects',
    '_matches_label_selector',
    '_matches_field_selector',
    '_selector_set_requirement',
    '_split_selector',
    '_nested_field',
    '_k8s_timestamp',
    '_stable_pod_ip',
    '_api_trace_body',
    '_redact_large_secret_data',
    '_api_namespace',
    '_api_resource_kind',
    '_api_resource_name',
    '_api_fingerprint',
    '_api_guess_intent',
    '_is_kubernetes_api_path',
    '_rate_limit_bucket',
]

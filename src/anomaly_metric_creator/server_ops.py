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
    DEFAULT_NAMESPACE as DEFAULT_NAMESPACE,
    HelmReleaseMutation as HelmReleaseMutation,
    SimulationMutations as SimulationMutations,
    WorkloadMutation as WorkloadMutation,
)
from .server_traces import (
    DEFAULT_TRACE_LIMIT as DEFAULT_TRACE_LIMIT,
    CommandTrace,
    CommandTraceStore as CommandTraceStore,
)

from .server_ops_support import (
    DEFAULT_RELEASE as DEFAULT_RELEASE,
    _snapshot_row_namespace as _snapshot_row_namespace,
    _parse_user_timestamp as _parse_user_timestamp,
    _parse_optional_timestamp as _parse_optional_timestamp,
)


from .server_ops_profiles import (
    OPS_SCENARIO_PROFILES as OPS_SCENARIO_PROFILES,
    OpsScenarioProfile as OpsScenarioProfile,
)


from .server_ops_state import (
    SimulationClock as SimulationClock,
)


# Client-command parsing lives in server_ops_parse.py (one-way import; the leaf
# never imports server_ops). Only names this module uses or that a caller reads
# through server_ops are re-imported.
from .server_ops_parse import (
    ParsedCommand as ParsedCommand,
    _MODELED_FLAGS as _MODELED_FLAGS,
    _EXPLAIN_RESOURCE_TARGETS as _EXPLAIN_RESOURCE_TARGETS,
    parse_command as parse_command,
    _normalize_kind as _normalize_kind,
    command_fingerprint as command_fingerprint,
    guess_intent as guess_intent,
    _redact_command_for_trace as _redact_command_for_trace,
    _redact_argv as _redact_argv,
    _redact_parsed_flags as _redact_parsed_flags,
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
    _capture_traceback_tail as _capture_traceback_tail,
    _emit_error_record as _emit_error_record,
    _record_server_error as _record_server_error,
    RefusalCounters as RefusalCounters,
    SimulationState as SimulationState,
    build_state as build_state,
    load_anomaly_rows as load_anomaly_rows,
)


from .server_ops_snapshot import (
    _SNAPSHOT_KINDS as _SNAPSHOT_KINDS,
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
)


from .server_ops_render import (
    _render_get_watch as _render_get_watch,
    _render_get as _render_get,
)


from .server_ops_render import (
    _render_describe as _render_describe,
    _logs_uses_selector as _logs_uses_selector,
    _render_logs_command as _render_logs_command,
    _render_top as _render_top,
    _render_kubectl_version as _render_kubectl_version,
    _render_kubectl_api_versions as _render_kubectl_api_versions,
    _render_kubectl_api_resources as _render_kubectl_api_resources,
    _render_kubectl_cluster_info as _render_kubectl_cluster_info,
)


from .server_k8s_resources import (
    _render_explain as _render_explain,
)


from .server_ops_render_workloads import (
    _render_rollout_status as _render_rollout_status,
    _render_rollout_history as _render_rollout_history,
    _render_rollout_restart as _render_rollout_restart,
    _render_rollout_pause as _render_rollout_pause,
    _render_rollout_resume as _render_rollout_resume,
    _render_rollout_undo as _render_rollout_undo,
    _is_deployment_rollout_target as _is_deployment_rollout_target,
    _render_scale as _render_scale,
    _render_delete as _render_delete,
)


from .server_ops_render_manifest import (
    _render_patch as _render_patch,
)


from .server_ops_render_manifest import (
    _render_diff as _render_diff,
    _render_apply as _render_apply,
)


from .server_ops_state import (
    _record_continuous_generation_failure as _record_continuous_generation_failure,
)


from .server_ops_render_workloads import (
    _render_wait as _render_wait,
    _render_exec as _render_exec,
    _render_port_forward as _render_port_forward,
)

from .server_helm_impl import (
    _render_helm_list as _render_helm_list,
    _render_helm_status as _render_helm_status,
    _render_helm_history as _render_helm_history,
    _render_helm_env as _render_helm_env,
    _render_helm_get as _render_helm_get,
    _render_helm_test as _render_helm_test,
    _render_helm_install as _render_helm_install,
    _render_helm_upgrade as _render_helm_upgrade,
    _helm_value_overrides as _helm_value_overrides,
    _render_helm_rollback as _render_helm_rollback,
)


from .server_helm_impl import (
    _helm_release as _helm_release,
    _helm_notes as _helm_notes,
    _helm_current_description as _helm_current_description,
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


from .server_k8s_api import _k8s_openapi_v3_discovery as _k8s_openapi_v3_discovery


from .server_k8s_dispatch import (
    kubernetes_api_post_response as kubernetes_api_post_response,
    kubernetes_api_mutating_response as kubernetes_api_mutating_response,
)


from .server_k8s_api import (
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
)


from .server_k8s_api import _k8s_api_resource_list as _k8s_api_resource_list


from .server_k8s_api import (
    k8s_watch_plan as k8s_watch_plan,
)


from .server_k8s_dispatch import (
    k8s_watch_objects as k8s_watch_objects,
)


from .server_k8s_api import (
    k8s_watch_object_key as k8s_watch_object_key,
    k8s_watch_trace_response as k8s_watch_trace_response,
)


from .server_k8s_tables import (
    _k8s_table as _k8s_table,
)


from .server_k8s_resources import (
    _k8s_objects_for_resource as _k8s_objects_for_resource,
)


from .server_k8s_objects import (
    _k8s_metadata as _k8s_metadata,
)


from .server_helm_impl import (
    _helm_secret_objects as _helm_secret_objects,
    _helm_release_revisions as _helm_release_revisions,
    _helm_secret_object as _helm_secret_object,
    _helm_encoded_release_data as _helm_encoded_release_data,
    _helm_release_payload as _helm_release_payload,
)


from .server_k8s_api_trace import (
    _is_kubernetes_api_path as _is_kubernetes_api_path,
    _rate_limit_bucket as _rate_limit_bucket,
)


__all__ = [
    'DEFAULT_RELEASE',
    'DEFAULT_NAMESPACE',
    'OpsScenarioProfile',
    'OPS_SCENARIO_PROFILES',
    'SimulationClock',
    'ParsedCommand',
    'CommandResult',
    'KubernetesApiResponse',
    'SimulationState',
    'build_state',
    'load_anomaly_rows',
    'run_command',
    'parse_command',
    'render_command',
    'resource_snapshot',
    'command_fingerprint',
    'guess_intent',
    'RequestBodyTooLarge',
    'kubernetes_api_response',
    'kubernetes_api_post_response',
    'kubernetes_api_mutating_response',
    'render_kubeconfig',
    'record_kubernetes_api_call',
]

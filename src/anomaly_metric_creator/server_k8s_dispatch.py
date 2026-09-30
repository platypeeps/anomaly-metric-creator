"""Kubernetes REST dispatch spine for the serve-mode simulator.

This leaf owns the Kubernetes API request dispatch: ``kubernetes_api_response``
(GET routing, discovery and watch), ``kubernetes_api_post_response``,
``kubernetes_api_mutating_response`` (PATCH/PUT/DELETE), the core, group and
per-resource response builders, ``_k8s_mutated_object``,
``k8s_watch_objects`` and ``record_kubernetes_api_call``. It reads the
overlay-aware ``resource_snapshot()`` from ``server_ops_snapshot`` and the
object builders from ``server_k8s_resources``; a test that stubs either for
these handlers patches it here. ``server_ops`` re-imports the names its callers
read through it, so the ``server_ops.<name>`` and ``server.<name>``
surfaces they use stay stable. This module never imports
``server_ops`` at runtime; ``SimulationState`` is a type-only import.
"""

from __future__ import annotations

import datetime as _dt
import urllib.parse
from typing import TYPE_CHECKING, Any

from .server_k8s_api import (
    _K8S_ADVERTISED_GIT_VERSION,
    _K8S_ADVERTISED_VERSION,
    KubernetesApiResponse,
    _filter_k8s_objects,
    _filter_k8s_objects_by_namespace,
    _k8s_api_group,
    _k8s_api_group_list,
    _k8s_api_resource_list,
    _k8s_json_response,
    _k8s_mutation_target,
    _k8s_read_only_response,
    _k8s_read_only_status_args,
    _k8s_resource_meta,
    _k8s_scale,
    _k8s_status_response,
    _k8s_subresource_mutation_allowed,
    _k8s_text_response,
    _payload_replicas,
)
from .server_k8s_api_trace import (
    _api_fingerprint,
    _api_guess_intent,
    _api_namespace,
    _api_resource_kind,
    _api_resource_name,
    _api_trace_body,
    _redact_query,
)
from .server_k8s_objects import _k8s_deployment, _k8s_timestamp
from .server_k8s_resources import _k8s_objects_for_resource, _k8s_openapi_response
from .server_k8s_tables import _accepts_table, _k8s_table
from .server_mutations import _format_dt
from .server_ops_parse import ParsedCommand
from .server_ops_render import _render_logs
from .server_ops_render_manifest import _generic_resource_row, _mutation_snapshot_kind
from .server_ops_render_workloads import _normalized_resource_prefix
from .server_ops_snapshot import resource_snapshot
from .server_ops_support import _find_named, _k8s_list_resource_version, _preview
from .server_traces import CommandTrace

if TYPE_CHECKING:
    from .server_ops import SimulationState


def kubernetes_api_response(
    state: SimulationState,
    method: str,
    path: str,
    query: dict[str, list[str]],
    accept_header: str = "",
) -> KubernetesApiResponse | None:
    if path != "/version" and not path.startswith(("/api", "/apis", "/openapi")):
        return None
    if method != "GET":
        return _k8s_read_only_response(method, path)
    if path.startswith("/openapi"):
        return _k8s_openapi_response(state, path)
    if path == "/version":
        major, minor, _ = _K8S_ADVERTISED_VERSION.split(".")
        return _k8s_json_response({
            "major": major,
            "minor": minor,
            "gitVersion": _K8S_ADVERTISED_GIT_VERSION,
            "gitCommit": "simulated",
            "gitTreeState": "clean",
            "buildDate": _k8s_timestamp(state.clock.now()),
            "goVersion": "go1.22.0",
            "compiler": "gc",
            "platform": "linux/amd64",
        }, "k8s.version")
    if path == "/api":
        return _k8s_json_response({
            "kind": "APIVersions",
            "apiVersion": "v1",
            "versions": ["v1"],
            "serverAddressByClientCIDRs": [],
        }, "k8s.discovery.core")
    if path == "/apis":
        return _k8s_json_response(_k8s_api_group_list(), "k8s.discovery.groups")

    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) == 2 and parts == ["api", "v1"]:
        return _k8s_json_response(_k8s_api_resource_list("", "v1"), "k8s.discovery.v1")
    if parts[:2] == ["api", "v1"]:
        return _k8s_core_resource_response(state, parts, query, _accepts_table(accept_header))
    if parts and parts[0] == "apis":
        return _k8s_group_resource_response(state, parts, query, _accepts_table(accept_header))
    return _k8s_status_response(
        404,
        f"{path} is not implemented by the simulator Kubernetes API",
        "NotFound",
        "unsupported",
        "k8s.path.unsupported",
    )


def kubernetes_api_post_response(
    state: SimulationState,
    path: str,
    payload: dict[str, Any],
) -> KubernetesApiResponse | None:
    if path.startswith("/openapi"):
        return _k8s_read_only_response("POST", path)
    if path == "/version":
        return _k8s_read_only_response("POST", path)
    if not path.startswith(("/api", "/apis")):
        return None
    if path == "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews":
        return KubernetesApiResponse(
            201,
            {
                "kind": "SelfSubjectAccessReview",
                "apiVersion": "authorization.k8s.io/v1",
                "metadata": payload.get("metadata", {}),
                "spec": payload.get("spec", {}),
                "status": {
                    "allowed": True,
                    "reason": "AMC simulator permits read-only diagnostic commands.",
                },
            },
            "application/json; charset=utf-8",
            "supported",
            "k8s.authorization.selfsubjectaccessreviews.create",
        )
    return kubernetes_api_mutating_response(state, "POST", path, payload)


def kubernetes_api_mutating_response(
    state: SimulationState,
    method: str,
    path: str,
    payload: dict[str, Any],
) -> KubernetesApiResponse:
    target = _k8s_mutation_target(path)
    if target is None:
        return _k8s_status_response(
            *_k8s_read_only_status_args(method, path),
        )
    resource = target["resource"]
    name = target["name"]
    subresource = target["subresource"]
    if target.get("extra") or not _k8s_subresource_mutation_allowed(method, resource, subresource):
        return _k8s_status_response(
            *_k8s_read_only_status_args(method, path),
        )
    now = state.clock.now()
    if method in {"PATCH", "PUT"} and resource == "deployments" and name:
        # Existence check BEFORE any overlay write: a refused mutation must
        # not leave partial state behind (the 404 used to be checked only
        # after set_workload/record_event had already mutated the overlay).
        if _find_named(resource_snapshot(state)["deployments"], name) is None:
            return _k8s_status_response(
                404,
                f"deployments {name!r} not found",
                "NotFound",
                "supported",
                "k8s.apps.deployments.mutate.not_found",
            )
        replicas = _payload_replicas(payload)
        if replicas is not None:
            state.mutations.set_workload(
                name,
                now=now,
                replicas=replicas,
                ready_replicas=replicas,
                deployment_status="Healthy" if replicas else "ScaledToZero",
                pod_status="Running",
            )
            reason = "ScalingReplicaSet" if subresource == "scale" else "Patched"
            state.mutations.record_event(
                "Normal",
                reason,
                f"deployment/{name}",
                f"{method.lower()} set deployment {name} replicas to {replicas}",
                now,
            )
        # Re-read after the overlay write so the response body reflects the
        # mutation, like a real API server's returned object would.
        deployment = _find_named(resource_snapshot(state)["deployments"], name)
        if deployment is None:  # pragma: no cover - defensive; checked above
            return _k8s_status_response(
                404,
                f"deployments {name!r} not found",
                "NotFound",
                "supported",
                "k8s.apps.deployments.mutate.not_found",
            )
        body = _k8s_scale(state, deployment) if subresource == "scale" else _k8s_deployment(state, deployment)
        return _k8s_json_response(body, f"k8s.apps.deployments.{method.lower()}")
    snapshot_kind = _mutation_snapshot_kind(resource)
    if method in {"PATCH", "PUT"} and snapshot_kind and name:
        state.mutations.put_resource(
            snapshot_kind,
            name,
            _generic_resource_row(state, snapshot_kind, name, payload=payload),
            now=now,
            namespace=target["namespace"],
        )
        body = _k8s_mutated_object(state, target, snapshot_kind, name)
        if body is not None:
            return _k8s_json_response(body, f"k8s.{resource}.{method.lower()}")
        return _k8s_status_response(
            200,
            f"{resource} {name!r} configured by simulator",
            "Configured",
            "supported",
            f"k8s.{resource}.{method.lower()}",
        )
    if method == "DELETE" and resource == "pods" and name:
        # Deleting a pod that is not in the (overlay-aware) snapshot must
        # 404 without touching the overlay — the unconditional delete used
        # to record phantom deletions for names that never existed.
        if _find_named(resource_snapshot(state)["pods"], name) is None:
            return _k8s_status_response(
                404,
                f"pods {name!r} not found",
                "NotFound",
                "supported",
                "k8s.core.pods.delete.not_found",
            )
        state.mutations.delete_pod(name, now=now)
        return _k8s_status_response(
            200,
            f"pods {name!r} deleted",
            "Deleted",
            "supported",
            "k8s.core.pods.delete",
        )
    if method == "DELETE" and resource == "deployments" and name:
        if _find_named(resource_snapshot(state)["deployments"], name) is None:
            return _k8s_status_response(
                404,
                f"deployments {name!r} not found",
                "NotFound",
                "supported",
                "k8s.apps.deployments.delete.not_found",
            )
        state.mutations.set_workload(
            name,
            now=now,
            replicas=0,
            ready_replicas=0,
            deployment_status="Deleted",
            pod_status="Terminating",
            deleted=True,
        )
        state.mutations.record_event(
            "Normal",
            "Deleted",
            f"deployment/{name}",
            f"deployment {name} deleted from simulator state",
            now,
        )
        return _k8s_status_response(
            200,
            f"deployments {name!r} deleted",
            "Deleted",
            "supported",
            "k8s.apps.deployments.delete",
        )
    if method == "DELETE" and snapshot_kind and name:
        rows = resource_snapshot(state).get(snapshot_kind, [])
        if _find_named(rows, name) is None:
            return _k8s_status_response(
                404,
                f"{resource} {name!r} not found",
                "NotFound",
                "supported",
                f"k8s.{resource}.delete.not_found",
            )
        state.mutations.delete_resource(snapshot_kind, name, now=now, namespace=target["namespace"])
        return _k8s_status_response(
            200,
            f"{resource} {name!r} deleted",
            "Deleted",
            "supported",
            f"k8s.{resource}.delete",
        )
    if method == "POST":
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        name = (
            name
            or str(metadata.get("name", ""))
            or f"simulated-{_normalized_resource_prefix(resource)}"
        )
        if snapshot_kind:
            state.mutations.put_resource(
                snapshot_kind,
                name,
                _generic_resource_row(state, snapshot_kind, name, payload=payload),
                now=now,
                namespace=target["namespace"],
            )
            body = _k8s_mutated_object(state, target, snapshot_kind, name)
            if body is not None:
                return KubernetesApiResponse(
                    201,
                    body,
                    "application/json; charset=utf-8",
                    "supported",
                    f"k8s.{resource}.create",
                )
        state.mutations.record_event(
            "Normal",
            "Created",
            f"{resource}/{name}",
            f"accepted create request for {resource}",
            now,
        )
        return _k8s_status_response(
            201,
            f"{resource} create accepted by simulator",
            "Created",
            "partial",
            f"k8s.{resource}.create.partial",
        )
    return _k8s_status_response(
        *_k8s_read_only_status_args(method, path),
    )


def _k8s_mutated_object(
    state: SimulationState,
    target: dict[str, str],
    snapshot_kind: str,
    name: str,
) -> dict[str, Any] | None:
    resource = target["resource"]
    group = target["group"]
    objects = _k8s_objects_for_resource(state, group, resource)
    if objects is None and snapshot_kind == "hpa":
        objects = _k8s_objects_for_resource(state, "autoscaling", "horizontalpodautoscalers")
    if objects is None and snapshot_kind == "ingress":
        objects = _k8s_objects_for_resource(state, "networking.k8s.io", "ingresses")
    if objects is None and snapshot_kind == "pvc":
        objects = _k8s_objects_for_resource(state, "", "persistentvolumeclaims")
    if objects is None:
        return None
    for obj in objects:
        metadata = obj.get("metadata", {})
        if (
            metadata.get("name") == name
            and metadata.get("namespace", target.get("namespace")) == target.get("namespace")
        ):
            return obj
    return None


def record_kubernetes_api_call(
    state: SimulationState,
    *,
    method: str,
    path: str,
    query: dict[str, list[str]],
    response: KubernetesApiResponse,
    client: str,
    user_agent: str,
    latency_ms: float,
    request_id: str = "",
) -> None:
    trace_query = _redact_query(query)
    raw_input = method + " " + path
    if trace_query:
        raw_input += "?" + urllib.parse.urlencode(trace_query, doseq=True)
    stdout = _api_trace_body(response)
    trace = CommandTrace(
        id=state.traces.next_id(),
        received_at_wall_time=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        simulated_time=_format_dt(state.clock.now()),
        raw_input=raw_input,
        argv=(method, path),
        client=client,
        command_family="kubernetes-api",
        verb=method,
        resource_kind=_api_resource_kind(path),
        resource_name=_api_resource_name(path),
        namespace=_api_namespace(path) or state.namespace,
        parsed_flags={
            "query": trace_query,
            "user_agent": user_agent,
        },
        support_status=response.support_status,
        matched_rule_id=response.matched_rule_id,
        active_scenarios=state.active_scenarios,
        exit_code=0 if response.status < 400 else 1,
        stdout_preview=_preview(stdout),
        stderr_preview="",
        stdout=stdout,
        stderr="",
        latency_ms=round(latency_ms, 3),
        fingerprint=_api_fingerprint(method, path),
        guessed_intent=_api_guess_intent(path, response),
        request_id=request_id,
    )
    state.traces.record(trace)


def _k8s_group_resource_response(
    state: SimulationState,
    parts: list[str],
    query: dict[str, list[str]],
    as_table: bool,
) -> KubernetesApiResponse:
    if len(parts) < 2:
        return _k8s_status_response(
            404, "/apis requires an API group", "NotFound", "unsupported", "k8s.apis.malformed"
        )
    group = parts[1]
    versions = {
        "apps": "v1",
        "autoscaling": "v2",
        "authorization.k8s.io": "v1",
        "batch": "v1",
        "discovery.k8s.io": "v1",
        "networking.k8s.io": "v1",
        "metrics.k8s.io": "v1beta1",
    }
    if group not in versions:
        return _k8s_status_response(
            404,
            f"API group {group!r} is not implemented by the simulator",
            "NotFound",
            "unsupported",
            "k8s.group.unsupported",
        )
    if len(parts) == 2:
        return _k8s_json_response(_k8s_api_group(group, versions[group]), f"k8s.discovery.{group}")
    version = parts[2]
    if version != versions[group]:
        return _k8s_status_response(
            404,
            f"API version {group}/{version} is not implemented by the simulator",
            "NotFound",
            "unsupported",
            "k8s.version.unsupported",
        )
    if len(parts) == 3:
        return _k8s_json_response(
            _k8s_api_resource_list(group, version),
            f"k8s.discovery.{group}.{version}",
        )
    if len(parts) >= 6 and parts[3] == "namespaces":
        namespace = parts[4]
        resource = parts[5]
        name = parts[6] if len(parts) >= 7 else ""
        subresource = parts[7] if len(parts) >= 8 else ""
        if group == "apps" and resource == "deployments" and name and subresource == "scale":
            deployment = _find_named(resource_snapshot(state)["deployments"], name)
            if deployment is None:
                return _k8s_status_response(
                    404,
                    f"{resource} {name!r} not found",
                    "NotFound",
                    "supported",
                    "k8s.apps.get.scale.not_found",
                )
            return _k8s_json_response(_k8s_scale(state, deployment), "k8s.apps.get.scale")
        return _k8s_resource_response(
            state, group, version, namespace, resource, name, query, as_table
        )
    if group == "metrics.k8s.io" and len(parts) >= 4 and parts[3] == "nodes":
        name = parts[4] if len(parts) >= 5 else ""
        return _k8s_resource_response(
            state, group, version, "", "nodes", name, query, as_table
        )
    return _k8s_status_response(
        404,
        f"/{'/'.join(parts)} is not implemented by the simulator Kubernetes API",
        "NotFound",
        "unsupported",
        "k8s.group.path.unsupported",
    )


def _k8s_core_resource_response(
    state: SimulationState,
    parts: list[str],
    query: dict[str, list[str]],
    as_table: bool,
) -> KubernetesApiResponse:
    if len(parts) == 3:
        return _k8s_resource_response(state, "", "v1", "", parts[2], "", query, as_table)
    if len(parts) == 4 and parts[2] in {"nodes", "namespaces"}:
        return _k8s_resource_response(
            state, "", "v1", "", parts[2], parts[3], query, as_table
        )
    if len(parts) >= 5 and parts[2] == "namespaces":
        namespace = parts[3]
        if len(parts) == 4:
            return _k8s_resource_response(
                state, "", "v1", "", "namespaces", namespace, query, as_table
            )
        resource = parts[4]
        name = parts[5] if len(parts) >= 6 else ""
        if resource == "pods" and len(parts) >= 7 and parts[6] == "log":
            pod_name = name
            parsed = ParsedCommand(
                raw_input=f"kubectl logs {pod_name} -n {namespace}",
                argv=("kubectl", "logs", pod_name, "-n", namespace),
                family="kubectl",
                verb="logs",
                resource_kind="pods",
                resource_name=pod_name,
                namespace=namespace,
                flags={"namespace": namespace},
                positionals=("logs", pod_name),
            )
            return _k8s_text_response(_render_logs(state, parsed), "k8s.core.pods.log")
        return _k8s_resource_response(
            state, "", "v1", namespace, resource, name, query, as_table
        )
    return _k8s_status_response(
        404,
        f"/{'/'.join(parts)} is not implemented by the simulator Kubernetes API",
        "NotFound",
        "unsupported",
        "k8s.core.path.unsupported",
    )


def _k8s_resource_response(
    state: SimulationState,
    group: str,
    version: str,
    namespace: str,
    resource: str,
    name: str,
    query: dict[str, list[str]],
    as_table: bool,
) -> KubernetesApiResponse:
    objects = _k8s_objects_for_resource(state, group, resource)
    if objects is None:
        return _k8s_status_response(
            404,
            f"resource {resource!r} is not implemented by the simulator Kubernetes API",
            "NotFound",
            "unsupported",
            "k8s.resource.unsupported",
        )
    objects = _filter_k8s_objects_by_namespace(resource, objects, namespace)
    objects = _filter_k8s_objects(objects, query)
    meta = _k8s_resource_meta(group, version, resource)
    if name:
        for obj in objects:
            if obj.get("metadata", {}).get("name") == name:
                if as_table:
                    return _k8s_json_response(
                        _k8s_table(state, resource, [obj]),
                        f"k8s.{group or 'core'}.get.{resource}.table",
                    )
                return _k8s_json_response(obj, f"k8s.{group or 'core'}.get.{resource}")
        return _k8s_status_response(
            404,
            f"{resource} {name!r} not found",
            "NotFound",
            "supported",
            f"k8s.{group or 'core'}.get.not_found",
        )
    if as_table:
        return _k8s_json_response(
            _k8s_table(state, resource, objects),
            f"k8s.{group or 'core'}.list.{resource}.table",
        )
    return _k8s_json_response({
        "kind": meta["list_kind"],
        "apiVersion": meta["api_version"],
        "metadata": {"resourceVersion": _k8s_list_resource_version(state)},
        "items": objects,
    }, f"k8s.{group or 'core'}.list.{resource}")


def k8s_watch_objects(
    state: SimulationState,
    plan: dict[str, str],
    query: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """Overlay-aware object set for a watch, mirroring the list path.

    Runs the exact ``_k8s_objects_for_resource`` -> namespace filter ->
    selector filter chain ``_k8s_resource_response`` uses, so a watch always
    observes the same objects the equivalent list would return.
    """
    objects = _k8s_objects_for_resource(state, plan["group"], plan["resource"]) or []
    objects = _filter_k8s_objects_by_namespace(plan["resource"], objects, plan["namespace"])
    return _filter_k8s_objects(objects, query)

"""Snapshot-bound Kubernetes resource builders for the serve-mode simulator.

This leaf owns ``kubectl explain`` (``_render_explain`` and the per-kind
schema lookup), the ``/openapi/v2`` and ``/openapi/v3`` document builders,
``_minimal_k8s_object``, the per-resource object dispatcher
``_k8s_objects_for_resource`` and the snapshot-coupled
``_k8s_endpointslice`` builder. Each reads the overlay-aware
``resource_snapshot()`` from ``server_ops_snapshot``; a test that stubs the
snapshot for these builders patches it here. The pure explain formatters stay
in ``server_ops_explain`` and the per-kind object builders in
``server_k8s_objects``. ``server_ops`` re-imports the names its callers
read through it, so the ``server_ops.<name>`` and ``server.<name>``
surfaces they use stay stable. This module never imports ``server_ops`` at runtime;
``SimulationState`` is a type-only import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .server_command_render import CommandResult
from .server_helm_impl import _helm_secret_objects
from .server_k8s_api import (
    _K8S_ADVERTISED_GIT_VERSION,
    _k8s_json_response,
    _k8s_openapi_v3_discovery,
    _k8s_resource_meta,
    _k8s_status_response,
    _openapi_group_version_from_path,
    _openapi_group_versions,
    _openapi_list_schema,
    _openapi_list_schema_name,
    _openapi_operation,
    _openapi_schema_name,
)
from .server_k8s_objects import (
    _k8s_configmap,
    _k8s_cronjob,
    _k8s_daemonset,
    _k8s_deployment,
    _k8s_endpoints,
    _k8s_event,
    _k8s_hpa,
    _k8s_ingress,
    _k8s_job,
    _k8s_metadata,
    _k8s_namespace,
    _k8s_node,
    _k8s_node_metrics,
    _k8s_pod,
    _k8s_pod_metrics,
    _k8s_pvc,
    _k8s_replicaset,
    _k8s_secret,
    _k8s_service,
    _k8s_serviceaccount,
    _k8s_statefulset,
)
from .server_ops_explain import _explain_schema_at_path, _format_explain, _openapi_schema_from_value
from .server_ops_parse import _EXPLAIN_RESOURCE_TARGETS
from .server_ops_snapshot import _snapshot_kind_namespaced, resource_snapshot
from .server_ops_support import _snapshot_row_namespace

if TYPE_CHECKING:
    from .server_k8s_api import KubernetesApiResponse
    from .server_ops import SimulationState
    from .server_ops_parse import ParsedCommand


_EXPLAIN_RESOURCE_DESCRIPTIONS = {
    "namespaces": "Namespace is a cluster-scoped boundary for AMC simulator resources.",
    "nodes": "Node is a simulated Kubernetes worker node that hosts AMC pods.",
    "pods": "Pod is a simulator-backed workload instance derived from resource_snapshot().",
    "configmaps": "ConfigMap exposes non-sensitive AMC simulator configuration data.",
    "secrets": "Secret exposes simulator Secret metadata and redacted data payload shape.",
    "replicationcontrollers": "ReplicationController is advertised for compatibility; AMC does not create baseline objects.",
    "services": "Service exposes the stable virtual endpoint for a simulated component.",
    "endpoints": "Endpoints exposes pod IPs selected by a simulated Service.",
    "events": "Event records scenario and mutation activity in Kubernetes-compatible form.",
    "pvc": "PersistentVolumeClaim exposes simulated storage pressure for stateful components.",
    "serviceaccounts": "ServiceAccount exposes identities used by simulator workloads.",
    "deployments": "Deployment describes desired and observed state for a simulated component workload.",
    "replicasets": "ReplicaSet is projected from simulated Deployment ownership.",
    "daemonsets": "DaemonSet describes node-level simulator agents.",
    "statefulsets": "StatefulSet describes stateful simulator workloads such as the database.",
    "hpa": "HorizontalPodAutoscaler exposes simulated scaling targets and current metrics.",
    "jobs": "Job describes one-shot simulator maintenance work.",
    "cronjobs": "CronJob describes recurring simulator maintenance work.",
    "endpointslices": "EndpointSlice exposes Service endpoint subsets for real kubectl clients.",
    "ingress": "Ingress exposes the simulator edge route for the API gateway.",
}


def _render_explain(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    target = parsed.positionals[1] if len(parsed.positionals) > 1 else ""
    if not target:
        return CommandResult(
            1,
            "",
            "error: resource required for kubectl explain\n",
            "partial",
            "kubectl.explain.missing-resource",
        )
    schema_info = _explain_schema_for_kind(state, parsed.resource_kind)
    if schema_info is None:
        return CommandResult(
            1,
            "",
            f"error: resource {target!r} is not exposed by the simulator OpenAPI schema\n",
            "unsupported",
            "kubectl.explain.unsupported",
        )
    requested_api_version = str(parsed.flags.get("--api-version") or "")
    if "--api-version" in parsed.flags and (
        not requested_api_version or requested_api_version.startswith("-")
    ):
        return CommandResult(
            1,
            "",
            "error: --api-version requires a non-empty value\n",
            "partial",
            "kubectl.explain.api-version.invalid",
        )
    if requested_api_version and requested_api_version != schema_info["api_version"]:
        return CommandResult(
            1,
            "",
            (
                f"error: resource {target!r} is available as "
                f"{schema_info['api_version']}, not {requested_api_version}\n"
            ),
            "partial",
            "kubectl.explain.api-version",
        )
    field_schema = _explain_schema_at_path(schema_info["schema"], parsed.resource_name)
    if field_schema is None:
        return CommandResult(
            1,
            "",
            (
                f"error: field {parsed.resource_name!r} is not exposed for "
                f"{schema_info['kind']}\n"
            ),
            "partial",
            "kubectl.explain.unknown-field",
        )
    return CommandResult(
        0,
        _format_explain(schema_info, parsed.resource_name, field_schema, bool(parsed.flags.get("--recursive"))),
        "",
        "supported",
        f"kubectl.explain.{parsed.resource_kind}",
    )


def _explain_schema_for_kind(
    state: SimulationState,
    kind: str,
    snapshot: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any] | None:
    target = _EXPLAIN_RESOURCE_TARGETS.get(kind)
    if target is None:
        return None
    group, version, resource = target
    meta = _k8s_resource_meta(group, version, resource)
    objects = _k8s_objects_for_resource(state, group, resource, snapshot=snapshot) or []
    sample = objects[0] if objects else _minimal_k8s_object(state, meta["api_version"], meta["kind"])
    schema = _openapi_schema_from_value(
        sample,
        root_kind=meta["kind"],
        path=(),
        description=_EXPLAIN_RESOURCE_DESCRIPTIONS.get(kind, f"{meta['kind']} is projected by the AMC simulator."),
    )
    schema["x-kubernetes-group-version-kind"] = [{
        "group": group,
        "version": version,
        "kind": meta["kind"],
    }]
    return {
        "api_version": meta["api_version"],
        "kind": meta["kind"],
        "resource": resource,
        "schema": schema,
    }


def _minimal_k8s_object(state: SimulationState, api_version: str, kind: str) -> dict[str, Any]:
    return {
        "apiVersion": api_version,
        "kind": kind,
        "metadata": _k8s_metadata(state, f"simulated-{kind.lower()}"),
    }


def _k8s_openapi_response(state: SimulationState, path: str) -> KubernetesApiResponse:
    normalized = path.rstrip("/") or "/"
    if normalized == "/openapi/v2":
        return _k8s_json_response(_k8s_openapi_v2_document(state), "k8s.openapi.v2")
    if normalized == "/openapi/v3":
        return _k8s_json_response(_k8s_openapi_v3_discovery(), "k8s.openapi.v3.discovery")
    prefix = "/openapi/v3/"
    if normalized.startswith(prefix):
        group_version = normalized[len(prefix):]
        group, version = _openapi_group_version_from_path(group_version)
        if (group, version) in _openapi_group_versions():
            return _k8s_json_response(
                _k8s_openapi_v3_document(state, group, version),
                f"k8s.openapi.v3.{group or 'core'}.{version}",
            )
    return _k8s_status_response(
        404,
        f"{path} is not implemented by the simulator OpenAPI facade",
        "NotFound",
        "unsupported",
        "k8s.openapi.unsupported",
    )


def _k8s_openapi_v2_document(state: SimulationState) -> dict[str, Any]:
    return {
        "swagger": "2.0",
        "info": {
            "title": "AMC simulator Kubernetes schema",
            "version": _K8S_ADVERTISED_GIT_VERSION,
        },
        "paths": _openapi_paths(openapi_version="2"),
        "definitions": _openapi_schema_definitions(state, ref_prefix="#/definitions/"),
    }


def _k8s_openapi_v3_document(
    state: SimulationState,
    group: str,
    version: str,
) -> dict[str, Any]:
    return {
        "openapi": "3.0.0",
        "info": {
            "title": f"AMC simulator Kubernetes schema {group or 'core'}/{version}",
            "version": _K8S_ADVERTISED_GIT_VERSION,
        },
        "paths": _openapi_paths(group=group, version=version, openapi_version="3"),
        "components": {
            "schemas": _openapi_schema_definitions(
                state,
                group=group,
                version=version,
                ref_prefix="#/components/schemas/",
            ),
        },
    }


def _openapi_schema_definitions(
    state: SimulationState,
    *,
    group: str | None = None,
    version: str | None = None,
    ref_prefix: str,
) -> dict[str, Any]:
    snapshot = resource_snapshot(state)
    definitions: dict[str, Any] = {}
    for kind, target in _EXPLAIN_RESOURCE_TARGETS.items():
        target_group, target_version, _resource = target
        if group is not None and (target_group != group or target_version != version):
            continue
        schema_info = _explain_schema_for_kind(state, kind, snapshot=snapshot)
        if schema_info is None:
            continue
        schema_name = _openapi_schema_name(schema_info["api_version"], schema_info["kind"])
        definitions[schema_name] = schema_info["schema"]
        definitions[_openapi_list_schema_name(schema_info["api_version"], schema_info["kind"])] = (
            _openapi_list_schema(schema_info, schema_name, ref_prefix)
        )
    return definitions


def _openapi_paths(
    *,
    group: str | None = None,
    version: str | None = None,
    openapi_version: str,
) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    ref_prefix = "#/definitions/" if openapi_version == "2" else "#/components/schemas/"
    for kind, target in _EXPLAIN_RESOURCE_TARGETS.items():
        target_group, target_version, resource = target
        if group is not None and (target_group != group or target_version != version):
            continue
        api_version = target_version if not target_group else f"{target_group}/{target_version}"
        meta_kind = _k8s_resource_meta(target_group, target_version, resource)["kind"]
        schema_name = _openapi_schema_name(api_version, meta_kind)
        list_schema_name = _openapi_list_schema_name(api_version, meta_kind)
        base_path = f"/api/{target_version}" if not target_group else f"/apis/{target_group}/{target_version}"
        if _snapshot_kind_namespaced(kind):
            all_namespaces_path = f"{base_path}/{resource}"
            namespaced_path = f"{base_path}/namespaces/{{namespace}}/{resource}"
            paths[all_namespaces_path] = {
                "get": _openapi_operation(
                    "list",
                    target_group,
                    target_version,
                    meta_kind,
                    list_schema_name,
                    ref_prefix,
                    openapi_version,
                ),
            }
            paths[namespaced_path] = paths[all_namespaces_path]
            paths[f"{namespaced_path}/{{name}}"] = {
                "get": _openapi_operation(
                    "get",
                    target_group,
                    target_version,
                    meta_kind,
                    schema_name,
                    ref_prefix,
                    openapi_version,
                ),
            }
        else:
            resource_path = f"{base_path}/{resource}"
            paths[resource_path] = {
                "get": _openapi_operation(
                    "list",
                    target_group,
                    target_version,
                    meta_kind,
                    list_schema_name,
                    ref_prefix,
                    openapi_version,
                ),
            }
            paths[f"{resource_path}/{{name}}"] = {
                "get": _openapi_operation(
                    "get",
                    target_group,
                    target_version,
                    meta_kind,
                    schema_name,
                    ref_prefix,
                    openapi_version,
                ),
            }
    return paths


def _k8s_objects_for_resource(
    state: SimulationState,
    group: str,
    resource: str,
    snapshot: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]] | None:
    snapshot = snapshot if snapshot is not None else resource_snapshot(state)
    if group == "metrics.k8s.io":
        if resource == "pods":
            return [_k8s_pod_metrics(state, pod) for pod in snapshot["pods"]]
        if resource == "nodes":
            return [_k8s_node_metrics(state, node) for node in snapshot["nodes"]]
        return None
    if resource == "namespaces":
        return [_k8s_namespace(state)]
    if resource == "nodes":
        return [_k8s_node(state, node) for node in snapshot["nodes"]]
    if resource == "pods":
        return [_k8s_pod(state, pod) for pod in snapshot["pods"]]
    if resource == "configmaps":
        return [_k8s_configmap(state, configmap) for configmap in snapshot["configmaps"]]
    if resource == "serviceaccounts":
        return [_k8s_serviceaccount(state, serviceaccount) for serviceaccount in snapshot["serviceaccounts"]]
    if resource == "replicationcontrollers":
        return []
    if resource == "services":
        return [_k8s_service(state, service) for service in snapshot["services"]]
    if resource == "endpoints":
        return [_k8s_endpoints(state, endpoint) for endpoint in snapshot["endpoints"]]
    if resource == "events":
        return [_k8s_event(state, event, index) for index, event in enumerate(snapshot["events"], start=1)]
    if resource == "persistentvolumeclaims":
        return [_k8s_pvc(state, pvc) for pvc in snapshot["pvc"]]
    if resource == "secrets":
        generic_secrets = [
            _k8s_secret(state, secret)
            for secret in snapshot["secrets"]
            if secret.get("type") != "helm.sh/release.v1"
        ]
        return [*_helm_secret_objects(state), *generic_secrets]
    if resource == "deployments" and group == "apps":
        return [_k8s_deployment(state, deployment) for deployment in snapshot["deployments"]]
    if resource == "replicasets" and group == "apps":
        return [_k8s_replicaset(state, replicaset) for replicaset in snapshot["replicasets"]]
    if resource == "daemonsets" and group == "apps":
        return [_k8s_daemonset(state, daemonset) for daemonset in snapshot["daemonsets"]]
    if resource == "statefulsets" and group == "apps":
        return [_k8s_statefulset(state, sts) for sts in snapshot["statefulsets"]]
    if resource == "horizontalpodautoscalers" and group == "autoscaling":
        return [_k8s_hpa(state, hpa) for hpa in snapshot["hpa"]]
    if resource == "jobs" and group == "batch":
        return [_k8s_job(state, job) for job in snapshot["jobs"]]
    if resource == "cronjobs" and group == "batch":
        return [_k8s_cronjob(state, cronjob) for cronjob in snapshot["cronjobs"]]
    if resource == "endpointslices" and group == "discovery.k8s.io":
        return [
            _k8s_endpointslice(state, endpointslice, snapshot=snapshot)
            for endpointslice in snapshot["endpointslices"]
        ]
    if resource == "ingresses" and group == "networking.k8s.io":
        return [_k8s_ingress(state, ingress) for ingress in snapshot["ingress"]]
    return None


def _k8s_endpointslice(
    state: SimulationState,
    endpointslice: dict[str, Any],
    snapshot: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    namespace = _snapshot_row_namespace(endpointslice, state.namespace)
    snapshot = snapshot if snapshot is not None else resource_snapshot(state)
    pods = [
        pod for pod in snapshot["pods"]
        if pod["component"] == endpointslice["service"]
        and _snapshot_row_namespace(pod, state.namespace) == namespace
    ]
    return {
        "apiVersion": "discovery.k8s.io/v1",
        "kind": "EndpointSlice",
        "metadata": _k8s_metadata(
            state,
            endpointslice["name"],
            namespace=namespace,
            labels={"kubernetes.io/service-name": endpointslice["service"]},
            resource_version=endpointslice.get("resource_version"),
        ),
        "addressType": endpointslice["address_type"],
        "ports": [{"name": "http", "protocol": "TCP", "port": 8080}],
        "endpoints": [
            {
                "addresses": [pod["pod_ip"]],
                "conditions": {"ready": pod["status"] == "Running"},
                "targetRef": {"kind": "Pod", "namespace": state.namespace, "name": pod["name"]},
            }
            for pod in pods
        ],
    }

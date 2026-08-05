"""Pure Kubernetes REST-facade builder/filter/format layer.

One-way leaf extracted from ``server_ops.py`` (epic
``07-06-server-ops-decomposition`` step 5). Owns the snapshot-free half of the
real-client Kubernetes API surface: response/dataclass builders, discovery and
OpenAPI-structural helpers, label/field selector filters, the pure watch
helpers, mutation-parse helpers, request-body readers, and
``render_kubeconfig``. The self-contained ``_api_*``
trace/fingerprint/redaction sink lives in the sibling leaf
``server_k8s_api_trace.py`` (one-way ``trace → api``), carved off for the
800-line cap.

Strictly one-way: it imports only stdlib and the lower leaves
(``server_mutations``, ``server_ops_parse``, ``server_ops_support``,
``server_k8s_objects``) and never imports ``server_ops`` at runtime.
``server_ops`` re-imports every public name here at each member's original
conceptual position, so the compatibility surface (``server.py``'s alias block,
the k8s facades, ``server_mcp.py``) is unchanged and ``server_ops.__all__``
membership stays byte-identical. The snapshot-bound dispatch spine
(``kubernetes_api_response`` and friends, ``_k8s_objects_for_resource``, the
OpenAPI *document* builders) stays in ``server_ops.py`` — no member here reads
the resource snapshot. ``SimulationState`` appears only in annotations, which
``from __future__ import annotations`` stringizes, so no runtime import of it is
needed.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any

from .server_mutations import DEFAULT_NAMESPACE
from .server_ops_parse import _EXPLAIN_RESOURCE_TARGETS
from .server_ops_support import _snapshot_row_labels
from .server_k8s_objects import (
    _k8s_metadata_for_row,
    _row_selector,
    _selector_string,
)

if TYPE_CHECKING:  # type-checking only; never executed, so the one-way rule holds
    from .server_ops import SimulationState


DEFAULT_MAX_BODY_BYTES = 1024 * 1024
_K8S_ADVERTISED_VERSION = "1.36.2"
_K8S_ADVERTISED_TAG = f"v{_K8S_ADVERTISED_VERSION}"
_K8S_ADVERTISED_GIT_VERSION = f"{_K8S_ADVERTISED_TAG}-amc"


def _query_int(query: dict[str, list[str]], name: str, default: int) -> int:
    try:
        return int(query.get(name, [str(default)])[0])
    except ValueError:
        return default


def _query_str(query: dict[str, list[str]], name: str, default: str) -> str:
    return query.get(name, [default])[0].strip()


@dataclass(frozen=True)
class KubernetesApiResponse:
    status: int
    body: Any
    content_type: str
    support_status: str
    matched_rule_id: str


class RequestBodyTooLarge(ValueError):
    """Raised when an HTTP request declares a body larger than server policy."""


def _read_json_body(
    handler: BaseHTTPRequestHandler,
    max_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> dict[str, Any]:
    length = _content_length(handler)
    if length > max_bytes:
        raise RequestBodyTooLarge(
            f"request body is {length} bytes; limit is {max_bytes} bytes"
        )
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON body: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    return payload


def _read_optional_json_body(
    handler: BaseHTTPRequestHandler,
    max_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> dict[str, Any]:
    length = _content_length(handler)
    if length > max_bytes:
        raise RequestBodyTooLarge(
            f"request body is {length} bytes; limit is {max_bytes} bytes"
        )
    raw = handler.rfile.read(length) if length else b"{}"
    with contextlib.suppress(UnicodeDecodeError, json.JSONDecodeError):
        payload = json.loads(raw.decode("utf-8"))
        if isinstance(payload, dict):
            return payload
    return {}


def _content_length(handler: BaseHTTPRequestHandler) -> int:
    value = handler.headers.get("content-length")
    if not value:
        return 0
    try:
        length = int(value)
    except ValueError as exc:
        raise ValueError("invalid content-length header") from exc
    if length < 0:
        raise ValueError("invalid negative content-length header")
    return length


def _k8s_openapi_v3_discovery() -> dict[str, Any]:
    paths = {}
    for group, version in _openapi_group_versions():
        api_path = f"api/{version}" if not group else f"apis/{group}/{version}"
        hash_token = f"amc-{(group or 'core').replace('.', '-')}-{version}"
        paths[api_path] = {
            "serverRelativeURL": f"/openapi/v3/{api_path}?hash={hash_token}",
        }
    return {"paths": paths}


def _openapi_operation(
    action: str,
    group: str,
    version: str,
    kind: str,
    schema_name: str,
    ref_prefix: str,
    openapi_version: str,
) -> dict[str, Any]:
    response: dict[str, Any] = {"description": "OK"}
    schema_ref = {"$ref": ref_prefix + schema_name}
    if openapi_version == "2":
        response["schema"] = schema_ref
    else:
        response["content"] = {"application/json": {"schema": schema_ref}}
    return {
        "description": f"{action.title()} simulated {kind} resources.",
        "operationId": f"{action}{(group or 'core').replace('.', '_')}{version}{kind}",
        "responses": {"200": response},
        "x-kubernetes-action": action,
        "x-kubernetes-group-version-kind": {
            "group": group,
            "version": version,
            "kind": kind,
        },
    }


def _openapi_list_schema(
    schema_info: dict[str, Any],
    item_schema_name: str,
    ref_prefix: str,
) -> dict[str, Any]:
    return {
        "type": "object",
        "title": f"{schema_info['kind']}List",
        "description": f"List of simulator-backed {schema_info['kind']} resources.",
        "properties": {
            "apiVersion": {"type": "string", "description": "API version of this list."},
            "kind": {"type": "string", "description": "Kind of this list."},
            "metadata": {
                "type": "object",
                "title": "ListMeta",
                "description": "List metadata projected by the simulator.",
                "properties": {
                    "resourceVersion": {
                        "type": "string",
                        "description": "Synthetic list resource version.",
                    },
                },
            },
            "items": {
                "type": "array",
                "description": f"{schema_info['kind']} items.",
                "items": {"$ref": ref_prefix + item_schema_name},
            },
        },
    }


def _openapi_schema_name(api_version: str, kind: str) -> str:
    if "/" in api_version:
        group, version = api_version.split("/", 1)
        return f"io.k8s.api.{group}.{version}.{kind}"
    return f"io.k8s.api.core.{api_version}.{kind}"


def _openapi_list_schema_name(api_version: str, kind: str) -> str:
    return _openapi_schema_name(api_version, f"{kind}List")


def _openapi_group_versions() -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            {
                (group, version)
                for group, version, _resource in _EXPLAIN_RESOURCE_TARGETS.values()
            }
        )
    )


def _openapi_group_version_from_path(path: str) -> tuple[str, str]:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) == 2 and parts[0] == "api":
        return "", parts[1]
    if len(parts) == 3 and parts[0] == "apis":
        return parts[1], parts[2]
    return "", ""


def _k8s_mutation_target(path: str) -> dict[str, str] | None:
    parts = [part for part in path.strip("/").split("/") if part]
    if parts[:3] == ["api", "v1", "namespaces"] and len(parts) >= 5:
        return {
            "group": "",
            "version": "v1",
            "namespace": parts[3],
            "resource": parts[4],
            "name": parts[5] if len(parts) >= 6 else "",
            "subresource": parts[6] if len(parts) >= 7 else "",
            "extra": "/".join(parts[7:]) if len(parts) >= 8 else "",
        }
    if parts and parts[0] == "apis" and len(parts) >= 6 and parts[3] == "namespaces":
        return {
            "group": parts[1],
            "version": parts[2],
            "namespace": parts[4],
            "resource": parts[5],
            "name": parts[6] if len(parts) >= 7 else "",
            "subresource": parts[7] if len(parts) >= 8 else "",
            "extra": "/".join(parts[8:]) if len(parts) >= 9 else "",
        }
    return None


def _k8s_subresource_mutation_allowed(method: str, resource: str, subresource: str) -> bool:
    if not subresource:
        return True
    return method in {"PATCH", "PUT"} and resource == "deployments" and subresource == "scale"


def _payload_replicas(payload: dict[str, Any]) -> int | None:
    spec = payload.get("spec")
    if isinstance(spec, dict) and "replicas" in spec:
        if isinstance(spec["replicas"], bool):
            return None
        try:
            return max(0, int(spec["replicas"]))
        except (TypeError, ValueError):
            return None
    return None


def _k8s_scale(state: SimulationState, deployment: dict[str, Any]) -> dict[str, Any]:
    replicas = int(str(deployment["ready"]).split("/", 1)[1])
    ready = int(str(deployment["ready"]).split("/", 1)[0])
    return {
        "apiVersion": "autoscaling/v1",
        "kind": "Scale",
        "metadata": _k8s_metadata_for_row(
            state,
            deployment,
            labels=_snapshot_row_labels("deployments", deployment),
            include_generation=True,
        ),
        "spec": {"replicas": replicas},
        "status": {
            "replicas": replicas,
            "selector": _selector_string(_row_selector(deployment, deployment["name"])),
            "readyReplicas": ready,
        },
    }


def render_kubeconfig(
    server_url: str,
    namespace: str = DEFAULT_NAMESPACE,
    token: str = "",
) -> str:
    user_block = "  user: {}\n"
    if token:
        user_block = f"  user:\n    token: {json.dumps(token)}\n"
    return (
        "apiVersion: v1\n"
        "kind: Config\n"
        "clusters:\n"
        "- name: amc-simulator\n"
        "  cluster:\n"
        f"    server: {server_url}\n"
        "    insecure-skip-tls-verify: true\n"
        "contexts:\n"
        "- name: amc-simulator\n"
        "  context:\n"
        "    cluster: amc-simulator\n"
        "    user: amc-simulator\n"
        f"    namespace: {namespace}\n"
        "current-context: amc-simulator\n"
        "users:\n"
        "- name: amc-simulator\n"
        f"{user_block}"
    )


def _k8s_json_response(body: dict[str, Any], matched_rule_id: str) -> KubernetesApiResponse:
    return KubernetesApiResponse(
        200,
        body,
        "application/json; charset=utf-8",
        "supported",
        matched_rule_id,
    )


def _k8s_text_response(text: str, matched_rule_id: str) -> KubernetesApiResponse:
    return KubernetesApiResponse(
        200,
        text,
        "text/plain; charset=utf-8",
        "supported",
        matched_rule_id,
    )


def _k8s_status_response(
    status: int,
    message: str,
    reason: str,
    support_status: str,
    matched_rule_id: str,
) -> KubernetesApiResponse:
    return KubernetesApiResponse(
        status,
        {
            "kind": "Status",
            "apiVersion": "v1",
            "metadata": {},
            "status": "Success" if status < 400 else "Failure",
            "message": message,
            "reason": reason,
            "code": status,
        },
        "application/json; charset=utf-8",
        support_status,
        matched_rule_id,
    )


def _k8s_read_only_response(method: str, path: str) -> KubernetesApiResponse:
    return _k8s_status_response(*_k8s_read_only_status_args(method, path))


def _k8s_read_only_status_args(method: str, path: str) -> tuple[int, str, str, str, str]:
    return (
        405,
        f"{method} {path} is not supported by the simulator Kubernetes mutation facade",
        "MethodNotAllowed",
        "unsupported",
        "k8s.method.unsupported",
    )


def _k8s_api_group_list() -> dict[str, Any]:
    groups = [
        _k8s_api_group("apps", "v1"),
        _k8s_api_group("autoscaling", "v2"),
        _k8s_api_group("authorization.k8s.io", "v1"),
        _k8s_api_group("batch", "v1"),
        _k8s_api_group("discovery.k8s.io", "v1"),
        _k8s_api_group("networking.k8s.io", "v1"),
        _k8s_api_group("metrics.k8s.io", "v1beta1"),
    ]
    return {"kind": "APIGroupList", "apiVersion": "v1", "groups": groups}


def _k8s_api_group(name: str, version: str) -> dict[str, Any]:
    return {
        "name": name,
        "versions": [{"groupVersion": f"{name}/{version}", "version": version}],
        "preferredVersion": {"groupVersion": f"{name}/{version}", "version": version},
    }


def _k8s_api_resource_list(group: str, version: str) -> dict[str, Any]:
    read_verbs = ["get", "list"]
    mutate_verbs = ["create", "delete", "get", "list", "patch", "update"]
    resources_by_group = {
        "": [
            ("namespaces", "Namespace", False, read_verbs),
            ("nodes", "Node", False, read_verbs),
            ("pods", "Pod", True, ["get", "list", "delete"]),
            ("pods/log", "Pod", True, ["get"]),
            ("configmaps", "ConfigMap", True, mutate_verbs),
            ("secrets", "Secret", True, mutate_verbs),
            ("replicationcontrollers", "ReplicationController", True, read_verbs),
            ("services", "Service", True, mutate_verbs),
            ("endpoints", "Endpoints", True, read_verbs),
            ("events", "Event", True, read_verbs),
            ("persistentvolumeclaims", "PersistentVolumeClaim", True, mutate_verbs),
            ("serviceaccounts", "ServiceAccount", True, mutate_verbs),
        ],
        "apps": [
            ("deployments", "Deployment", True, mutate_verbs),
            ("deployments/scale", "Scale", True, ["get", "patch", "update"]),
            ("replicasets", "ReplicaSet", True, read_verbs),
            ("daemonsets", "DaemonSet", True, mutate_verbs),
            ("statefulsets", "StatefulSet", True, mutate_verbs),
        ],
        "autoscaling": [
            ("horizontalpodautoscalers", "HorizontalPodAutoscaler", True, mutate_verbs),
        ],
        "authorization.k8s.io": [
            ("selfsubjectaccessreviews", "SelfSubjectAccessReview", False, ["create"]),
        ],
        "batch": [
            ("jobs", "Job", True, mutate_verbs),
            ("cronjobs", "CronJob", True, mutate_verbs),
        ],
        "discovery.k8s.io": [
            ("endpointslices", "EndpointSlice", True, read_verbs),
        ],
        "networking.k8s.io": [
            ("ingresses", "Ingress", True, mutate_verbs),
        ],
        "metrics.k8s.io": [
            ("nodes", "NodeMetrics", False, read_verbs),
            ("pods", "PodMetrics", True, read_verbs),
        ],
    }
    group_version = version if not group else f"{group}/{version}"
    resources = []
    for name, kind, namespaced, verbs in resources_by_group.get(group, []):
        entry = {
            "name": name,
            "singularName": "",
            "namespaced": namespaced,
            "kind": kind,
            "verbs": verbs,
        }
        if name == "pods":
            entry["shortNames"] = ["po"]
        elif name == "configmaps":
            entry["shortNames"] = ["cm"]
        elif name == "services":
            entry["shortNames"] = ["svc"]
        elif name == "endpoints":
            entry["shortNames"] = ["ep"]
        elif name == "serviceaccounts":
            entry["shortNames"] = ["sa"]
        elif name == "replicationcontrollers":
            entry["shortNames"] = ["rc"]
        elif name == "persistentvolumeclaims":
            entry["shortNames"] = ["pvc"]
        elif name == "deployments":
            entry["shortNames"] = ["deploy"]
        elif name == "replicasets":
            entry["shortNames"] = ["rs"]
        elif name == "daemonsets":
            entry["shortNames"] = ["ds"]
        elif name == "statefulsets":
            entry["shortNames"] = ["sts"]
        elif name == "horizontalpodautoscalers":
            entry["shortNames"] = ["hpa"]
        elif name == "cronjobs":
            entry["shortNames"] = ["cj"]
        elif name == "ingresses":
            entry["shortNames"] = ["ing"]
        if name in {
            "pods",
            "services",
            "deployments",
            "replicasets",
            "daemonsets",
            "statefulsets",
            "horizontalpodautoscalers",
            "jobs",
            "cronjobs",
        }:
            entry["categories"] = ["all"]
        resources.append(entry)
    return {
        "kind": "APIResourceList",
        "apiVersion": "v1",
        "groupVersion": group_version,
        "resources": resources,
    }


def _filter_k8s_objects_by_namespace(
    resource: str,
    objects: list[dict[str, Any]],
    namespace: str,
) -> list[dict[str, Any]]:
    if not namespace or resource in {"namespaces", "nodes"}:
        return objects
    return [
        obj for obj in objects
        if obj.get("metadata", {}).get("namespace") == namespace
    ]


_WATCHABLE_LIST_RESOURCES = {
    ("", "v1", "pods"),
    ("apps", "v1", "deployments"),
}


def _watch_requested(query: dict[str, list[str]]) -> bool:
    """True when the query asks for a watch (`watch=true` or `watch=1`)."""
    return any(value in ("true", "1") for value in query.get("watch", []))


def k8s_watch_plan(
    state: SimulationState,
    path: str,
    query: dict[str, list[str]],
) -> dict[str, str] | None:
    """Return a watch plan for a modeled list path, else ``None``.

    Fires only when the query requests a watch on a modeled *list* path (no
    object name) for a watchable resource family. Single-object paths,
    unmodeled resources, and non-watch requests return ``None`` so the caller
    falls through to the existing one-shot list / unsupported handling. The
    ``state`` argument is unused today but keeps the signature parallel with
    the other snapshot-backed helpers.
    """
    if not _watch_requested(query):
        return None
    parts = [segment for segment in path.split("/") if segment]
    group = version = namespace = resource = name = ""
    if parts[:2] == ["api", "v1"]:
        group, version = "", "v1"
        rest = parts[2:]
    elif parts[:1] == ["apis"] and len(parts) >= 4:
        group, version = parts[1], parts[2]
        rest = parts[3:]
    else:
        return None
    if len(rest) == 1:
        resource = rest[0]
    elif len(rest) >= 3 and rest[0] == "namespaces":
        namespace, resource = rest[1], rest[2]
        name = rest[3] if len(rest) >= 4 else ""
    else:
        return None
    if name:
        # Watching a single named object is out of scope for v1.
        return None
    if (group, version, resource) not in _WATCHABLE_LIST_RESOURCES:
        return None
    return {
        "group": group,
        "version": version,
        "namespace": namespace,
        "resource": resource,
        "matched_rule_id": f"k8s.{group or 'core'}.watch.{resource}",
    }


def k8s_watch_object_key(obj: dict[str, Any]) -> str:
    """Stable identity for watch diffing: ``uid`` when present, else ns/name."""
    meta = obj.get("metadata", {})
    uid = meta.get("uid")
    if uid:
        return f"uid:{uid}"
    return f"nn:{meta.get('namespace', '')}/{meta.get('name', '')}"


def k8s_watch_trace_response(
    plan: dict[str, str],
    *,
    event_count: int,
    refused: bool = False,
) -> KubernetesApiResponse:
    """Synthetic Status recorded as the watch's ``kubernetes-api`` trace.

    A refused watch (over the SSE ceiling) records a partial Status 503; a
    normal close records a supported Status naming the emitted event count.
    The refusal Status is *also* returned to the client as the HTTP response
    (the stream never started, so this Status body is what the client sees).
    The normal-close Status is trace-only: the watch body was already streamed
    to the client as newline-delimited watch events, so this Status just
    carries the event count into ``record_kubernetes_api_call``.
    """
    if refused:
        return _k8s_status_response(
            503,
            "watch stream refused: concurrent SSE connection limit reached",
            "ServiceUnavailable",
            "partial",
            plan["matched_rule_id"],
        )
    return KubernetesApiResponse(
        200,
        {
            "kind": "Status",
            "apiVersion": "v1",
            "metadata": {},
            "status": "Success",
            "message": f"watch closed after {event_count} event(s)",
            "reason": "WatchClosed",
            "code": 200,
        },
        "application/json; charset=utf-8",
        "supported",
        plan["matched_rule_id"],
    )


def _k8s_resource_meta(group: str, version: str, resource: str) -> dict[str, str]:
    api_version = version if not group else f"{group}/{version}"
    kinds = {
        "namespaces": "Namespace",
        "nodes": "Node" if group != "metrics.k8s.io" else "NodeMetrics",
        "pods": "Pod" if group != "metrics.k8s.io" else "PodMetrics",
        "configmaps": "ConfigMap",
        "secrets": "Secret",
        "replicationcontrollers": "ReplicationController",
        "services": "Service",
        "endpoints": "Endpoints",
        "endpointslices": "EndpointSlice",
        "events": "Event",
        "persistentvolumeclaims": "PersistentVolumeClaim",
        "serviceaccounts": "ServiceAccount",
        "deployments": "Deployment",
        "replicasets": "ReplicaSet",
        "daemonsets": "DaemonSet",
        "statefulsets": "StatefulSet",
        "horizontalpodautoscalers": "HorizontalPodAutoscaler",
        "jobs": "Job",
        "cronjobs": "CronJob",
        "ingresses": "Ingress",
    }
    kind = kinds.get(resource, resource.rstrip("s").title())
    return {"api_version": api_version, "kind": kind, "list_kind": f"{kind}List"}


def _filter_k8s_objects(
    objects: list[dict[str, Any]],
    query: dict[str, list[str]],
) -> list[dict[str, Any]]:
    label_selector = _query_str(query, "labelSelector", "")
    field_selector = _query_str(query, "fieldSelector", "")
    return [
        obj for obj in objects
        if _matches_label_selector(obj.get("metadata", {}).get("labels", {}), label_selector)
        and _matches_field_selector(obj, field_selector)
    ]


def _matches_label_selector(labels: dict[str, str], selector: str) -> bool:
    if not selector:
        return True
    for item in _split_selector(selector):
        if " notin " in item or " notin(" in item:
            key, values = _selector_set_requirement(item, "notin")
            if labels.get(key) in values:
                return False
        elif " in " in item or " in(" in item:
            key, values = _selector_set_requirement(item, "in")
            if labels.get(key) not in values:
                return False
        elif "!=" in item:
            key, value = item.split("!=", 1)
            if labels.get(key.strip()) == value.strip():
                return False
        elif "==" in item or "=" in item:
            separator = "==" if "==" in item else "="
            key, value = item.split(separator, 1)
            if labels.get(key.strip()) != value.strip():
                return False
        elif item.startswith("!"):
            if item[1:].strip() in labels:
                return False
        elif item.strip() not in labels:
            return False
    return True


def _matches_field_selector(obj: dict[str, Any], selector: str) -> bool:
    if not selector:
        return True
    for item in _split_selector(selector):
        if "!=" in item:
            key, value = item.split("!=", 1)
            if str(_nested_field(obj, key.strip())) == value.strip():
                return False
        elif "==" in item or "=" in item:
            separator = "==" if "==" in item else "="
            key, value = item.split(separator, 1)
            if str(_nested_field(obj, key.strip())) != value.strip():
                return False
    return True


def _selector_set_requirement(item: str, operator: str) -> tuple[str, set[str]]:
    key, _, rest = item.partition(operator)
    values = rest.strip()
    if values.startswith("(") and values.endswith(")"):
        values = values[1:-1]
    return key.strip(), {value.strip() for value in values.split(",") if value.strip()}


def _split_selector(selector: str) -> list[str]:
    items = []
    start = 0
    depth = 0
    for index, char in enumerate(selector):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            items.append(selector[start:index].strip())
            start = index + 1
    items.append(selector[start:].strip())
    return [item for item in items if item]


def _nested_field(obj: dict[str, Any], path: str) -> Any:
    value: Any = obj
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value

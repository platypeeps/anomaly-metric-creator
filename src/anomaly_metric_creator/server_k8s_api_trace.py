"""Kubernetes-API trace / fingerprint / query-redaction sub-cluster.

One-way sibling leaf of ``server_k8s_api.py`` (epic
``07-06-server-ops-decomposition`` step 5, size carve). Holds the pure
``kubernetes-api`` trace-body / fingerprint / intent / rate-limit-bucket helpers
and the query-secret redaction pair that ``record_kubernetes_api_call`` (which
stays in ``server_ops.py``) consumes. None of these are read back by any other
``server_k8s_api`` member, so the cluster is a pure sink and lifts cleanly into
its own leaf, keeping both leaves under the 800-line module cap.

Strictly one-way: imports only stdlib plus the lower leaves
(``server_k8s_api`` for ``KubernetesApiResponse``, ``server_ops_support`` for
``_preview``, ``server_ops_parse`` for ``_SENSITIVE_FLAG_TOKENS``) and never
imports ``server_ops`` at runtime. ``server_ops`` re-imports every name here at
its original conceptual position so the historic surface and ``__all__`` stay
stable.
"""

from __future__ import annotations

import json
from typing import Any

from .server_k8s_api import KubernetesApiResponse
from .server_ops_parse import _SENSITIVE_FLAG_TOKENS
from .server_ops_support import _preview


_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "bearer_token",
    "client_key",
    "client_secret",
    "id_token",
    "password",
    "refresh_token",
    "secret",
    "token",
}


def _redact_query(query: dict[str, list[str]]) -> dict[str, list[str]]:
    return {
        key: ["***"] if _is_sensitive_query_key(key) else list(values)
        for key, values in query.items()
    }


def _is_sensitive_query_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in _SENSITIVE_QUERY_KEYS:
        return True
    if normalized.endswith("_token"):
        return True
    return any(token.replace("-", "_") in normalized for token in _SENSITIVE_FLAG_TOKENS)


def _api_trace_body(response: KubernetesApiResponse) -> str:
    if isinstance(response.body, str):
        return _preview(response.body, 2000)
    safe_body = _redact_large_secret_data(response.body)
    return _preview(json.dumps(safe_body, sort_keys=True), 2000)


def _redact_large_secret_data(value: Any) -> Any:
    if isinstance(value, list):
        return [_redact_large_secret_data(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == "data" and isinstance(item, dict) and "release" in item:
            result[key] = {**item, "release": "<helm release payload>"}
        else:
            result[key] = _redact_large_secret_data(item)
    return result


def _api_namespace(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    for index, part in enumerate(parts):
        if part == "namespaces" and index + 1 < len(parts):
            return parts[index + 1]
    return ""


def _api_resource_kind(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    for index, part in enumerate(parts):
        if part == "namespaces" and index + 2 < len(parts):
            return parts[index + 2]
    if len(parts) >= 3 and parts[:2] == ["api", "v1"]:
        return parts[2]
    if len(parts) >= 4 and parts[0] == "apis":
        return parts[3]
    return parts[-1] if parts else ""


def _api_resource_name(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    for index, part in enumerate(parts):
        if part == "namespaces" and index + 3 < len(parts):
            return parts[index + 3]
    if len(parts) >= 4 and parts[:2] == ["api", "v1"]:
        return parts[3]
    if len(parts) >= 5 and parts[0] == "apis":
        return parts[4]
    return ""


def _api_fingerprint(method: str, path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    normalized = []
    index = 0
    while index < len(parts):
        part = parts[index]
        normalized.append(part)
        if part == "namespaces" and index + 1 < len(parts):
            normalized.append("{namespace}")
            index += 2
            continue
        if normalized[-1] in {
            "pods",
            "configmaps",
            "secrets",
            "replicationcontrollers",
            "services",
            "endpoints",
            "endpointslices",
            "events",
            "persistentvolumeclaims",
            "serviceaccounts",
            "deployments",
            "replicasets",
            "daemonsets",
            "statefulsets",
            "horizontalpodautoscalers",
            "ingresses",
            "nodes",
            "jobs",
            "cronjobs",
        } and index + 1 < len(parts):
            normalized.append("{name}")
            index += 2
            continue
        index += 1
    return f"kubernetes-api {method} /{'/'.join(normalized)}"


def _api_guess_intent(path: str, response: KubernetesApiResponse) -> str:
    if response.support_status == "supported":
        return "Real kubectl/helm-compatible API call handled by simulator."
    return f"Add Kubernetes API compatibility for {path}."


def _is_kubernetes_api_path(path: str) -> bool:
    return path == "/version" or path.startswith(("/api", "/apis", "/openapi"))


def _rate_limit_bucket(path: str) -> str:
    if path == "/v1/commands":
        return "commands"
    if path == "/mcp":
        # MCP tools/call is command-like: cap it per client like /v1/commands.
        return "mcp"
    if _is_kubernetes_api_path(path):
        return "kubernetes-api"
    return ""

"""meta.k8s.io/v1 Table response rendering for `kubectl get`.

Leaf extracted from ``server_ops.py`` (epic ``07-06-server-ops-decomposition``
step 4). Owns ``_k8s_table``, its column/schema dispatch, and every per-kind
cell builder (plus the generic ``_k8s_default_cells`` fallback). Reads
``_k8s_list_resource_version`` downward from ``server_ops_support``. Never
imports ``server_ops`` (one-way). ``SimulationState`` is annotation-only under
``from __future__ import annotations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .server_ops_support import _k8s_list_resource_version

if TYPE_CHECKING:  # type-checking only; never executed, so the one-way rule holds
    from .server_ops import SimulationState


def _accepts_table(accept_header: str) -> bool:
    return "as=Table" in accept_header and "g=meta.k8s.io" in accept_header


def _k8s_table(state: SimulationState, resource: str, objects: list[dict[str, Any]]) -> dict[str, Any]:
    columns, cell_builder = _k8s_table_schema(resource)
    return {
        "kind": "Table",
        "apiVersion": "meta.k8s.io/v1",
        "metadata": {"resourceVersion": _k8s_list_resource_version(state)},
        "columnDefinitions": columns,
        "rows": [
            {
                "cells": cell_builder(obj),
                "object": {
                    "kind": "PartialObjectMetadata",
                    "apiVersion": "meta.k8s.io/v1",
                    "metadata": obj.get("metadata", {}),
                },
            }
            for obj in objects
        ],
    }


def _k8s_column(name: str, column_type: str = "string") -> dict[str, str]:
    return {
        "name": name,
        "type": column_type,
        "format": "name" if name == "Name" else "",
        "description": name,
    }


def _k8s_table_schema(resource: str):
    if resource == "pods":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Ready"),
                _k8s_column("Status"),
                _k8s_column("Restarts", "integer"),
                _k8s_column("Age"),
            ],
            _k8s_pod_cells,
        )
    if resource == "deployments":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Ready"),
                _k8s_column("Up-to-date", "integer"),
                _k8s_column("Available", "integer"),
                _k8s_column("Age"),
            ],
            _k8s_deployment_cells,
        )
    if resource == "services":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Type"),
                _k8s_column("Cluster-IP"),
                _k8s_column("External-IP"),
                _k8s_column("Port(s)"),
                _k8s_column("Age"),
            ],
            _k8s_service_cells,
        )
    if resource == "endpoints":
        return (
            [_k8s_column("Name"), _k8s_column("Endpoints"), _k8s_column("Age")],
            _k8s_endpoints_cells,
        )
    if resource == "endpointslices":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("AddressType"),
                _k8s_column("Ports"),
                _k8s_column("Endpoints", "integer"),
                _k8s_column("Age"),
            ],
            _k8s_endpointslice_cells,
        )
    if resource == "events":
        return (
            [
                _k8s_column("Last Seen"),
                _k8s_column("Type"),
                _k8s_column("Reason"),
                _k8s_column("Object"),
                _k8s_column("Message"),
            ],
            _k8s_event_cells,
        )
    if resource == "horizontalpodautoscalers":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Reference"),
                _k8s_column("Targets"),
                _k8s_column("Minpods", "integer"),
                _k8s_column("Maxpods", "integer"),
                _k8s_column("Replicas", "integer"),
                _k8s_column("Age"),
            ],
            _k8s_hpa_cells,
        )
    if resource == "nodes":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Status"),
                _k8s_column("Roles"),
                _k8s_column("Age"),
                _k8s_column("Version"),
            ],
            _k8s_node_cells,
        )
    if resource == "replicasets":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Desired", "integer"),
                _k8s_column("Current", "integer"),
                _k8s_column("Ready", "integer"),
                _k8s_column("Age"),
            ],
            _k8s_replicaset_cells,
        )
    if resource == "daemonsets":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Desired", "integer"),
                _k8s_column("Current", "integer"),
                _k8s_column("Ready", "integer"),
                _k8s_column("Up-to-date", "integer"),
                _k8s_column("Available", "integer"),
                _k8s_column("Age"),
            ],
            _k8s_daemonset_cells,
        )
    if resource == "persistentvolumeclaims":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Status"),
                _k8s_column("Volume"),
                _k8s_column("Capacity"),
                _k8s_column("Access Modes"),
                _k8s_column("Storageclass"),
                _k8s_column("Age"),
            ],
            _k8s_pvc_cells,
        )
    if resource == "statefulsets":
        return (
            [_k8s_column("Name"), _k8s_column("Ready"), _k8s_column("Age")],
            _k8s_statefulset_cells,
        )
    if resource == "ingresses":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Class"),
                _k8s_column("Hosts"),
                _k8s_column("Address"),
                _k8s_column("Ports"),
                _k8s_column("Age"),
            ],
            _k8s_ingress_cells,
        )
    if resource == "secrets":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Type"),
                _k8s_column("Data", "integer"),
                _k8s_column("Age"),
            ],
            _k8s_secret_cells,
        )
    if resource == "configmaps":
        return (
            [_k8s_column("Name"), _k8s_column("Data", "integer"), _k8s_column("Age")],
            _k8s_configmap_cells,
        )
    if resource == "serviceaccounts":
        return (
            [_k8s_column("Name"), _k8s_column("Secrets", "integer"), _k8s_column("Age")],
            _k8s_serviceaccount_cells,
        )
    if resource == "jobs":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Completions"),
                _k8s_column("Duration"),
                _k8s_column("Age"),
            ],
            _k8s_job_cells,
        )
    if resource == "cronjobs":
        return (
            [
                _k8s_column("Name"),
                _k8s_column("Schedule"),
                _k8s_column("Suspend"),
                _k8s_column("Active", "integer"),
                _k8s_column("Last Schedule"),
                _k8s_column("Age"),
            ],
            _k8s_cronjob_cells,
        )
    if resource == "namespaces":
        return (
            [_k8s_column("Name"), _k8s_column("Status"), _k8s_column("Age")],
            _k8s_namespace_cells,
        )
    return ([_k8s_column("Name"), _k8s_column("Age")], _k8s_default_cells)


def _k8s_pod_cells(obj: dict[str, Any]) -> list[Any]:
    statuses = obj.get("status", {}).get("containerStatuses", [])
    ready = sum(1 for status in statuses if status.get("ready"))
    restarts = sum(int(status.get("restartCount", 0)) for status in statuses)
    return [
        obj["metadata"]["name"],
        f"{ready}/{len(statuses) or 1}",
        _k8s_pod_display_status(obj),
        restarts,
        "7d",
    ]


def _k8s_pod_display_status(obj: dict[str, Any]) -> str:
    statuses = obj.get("status", {}).get("containerStatuses", [])
    for status in statuses:
        state = status.get("state", {})
        if "waiting" in state:
            return state["waiting"].get("reason", "Waiting")
        if "terminated" in state:
            return state["terminated"].get("reason", "Terminated")
    return obj.get("status", {}).get("phase", "Unknown")


def _k8s_deployment_cells(obj: dict[str, Any]) -> list[Any]:
    spec = obj.get("spec", {})
    status = obj.get("status", {})
    replicas = int(spec.get("replicas", 0))
    ready = int(status.get("readyReplicas", 0))
    return [
        obj["metadata"]["name"],
        f"{ready}/{replicas}",
        int(status.get("updatedReplicas", 0)),
        int(status.get("availableReplicas", 0)),
        "7d",
    ]


def _k8s_service_cells(obj: dict[str, Any]) -> list[Any]:
    spec = obj.get("spec", {})
    ports = ",".join(
        f"{port.get('port')}/{port.get('protocol', 'TCP')}"
        for port in spec.get("ports", [])
    )
    return [
        obj["metadata"]["name"],
        spec.get("type", "ClusterIP"),
        spec.get("clusterIP", "<none>"),
        "<none>",
        ports,
        "7d",
    ]


def _k8s_endpoints_cells(obj: dict[str, Any]) -> list[Any]:
    subsets = obj.get("subsets", [])
    endpoints = []
    for subset in subsets:
        ports = subset.get("ports", [])
        port = ports[0].get("port", 8080) if ports else 8080
        for address in subset.get("addresses", []):
            endpoints.append(f"{address.get('ip')}:{port}")
    return [obj["metadata"]["name"], ",".join(endpoints) or "<none>", "7d"]


def _k8s_endpointslice_cells(obj: dict[str, Any]) -> list[Any]:
    ports = ",".join(str(port.get("port", "")) for port in obj.get("ports", []))
    return [
        obj["metadata"]["name"],
        obj.get("addressType", "IPv4"),
        ports,
        len(obj.get("endpoints", [])),
        "7d",
    ]


def _k8s_event_cells(obj: dict[str, Any]) -> list[Any]:
    involved = obj.get("involvedObject", {})
    return [
        "0s",
        obj.get("type", ""),
        obj.get("reason", ""),
        f"{involved.get('kind', '').lower()}/{involved.get('name', '')}",
        obj.get("message", ""),
    ]


def _k8s_hpa_cells(obj: dict[str, Any]) -> list[Any]:
    spec = obj.get("spec", {})
    status = obj.get("status", {})
    target = spec.get("scaleTargetRef", {})
    current = status.get("currentMetrics", [{}])[0].get("resource", {}).get("current", {})
    desired = spec.get("metrics", [{}])[0].get("resource", {}).get("target", {})
    current_pct = current.get("averageUtilization", 0)
    desired_pct = desired.get("averageUtilization", 0)
    return [
        obj["metadata"]["name"],
        f"{target.get('kind', 'Deployment')}/{target.get('name', '')}",
        f"{current_pct}%/{desired_pct}%",
        int(spec.get("minReplicas", 0)),
        int(spec.get("maxReplicas", 0)),
        int(status.get("currentReplicas", 0)),
        "7d",
    ]


def _k8s_node_cells(obj: dict[str, Any]) -> list[Any]:
    conditions = obj.get("status", {}).get("conditions", [])
    ready = next((condition for condition in conditions if condition.get("type") == "Ready"), {})
    role = obj.get("metadata", {}).get("labels", {}).get("kubernetes.io/role", "worker")
    version = obj.get("status", {}).get("nodeInfo", {}).get("kubeletVersion", "")
    return [
        obj["metadata"]["name"],
        "Ready" if ready.get("status") == "True" else "NotReady",
        role,
        "30d",
        version,
    ]


def _k8s_replicaset_cells(obj: dict[str, Any]) -> list[Any]:
    status = obj.get("status", {})
    return [
        obj["metadata"]["name"],
        int(status.get("replicas", 0)),
        int(status.get("fullyLabeledReplicas", status.get("replicas", 0))),
        int(status.get("readyReplicas", 0)),
        "7d",
    ]


def _k8s_daemonset_cells(obj: dict[str, Any]) -> list[Any]:
    status = obj.get("status", {})
    return [
        obj["metadata"]["name"],
        int(status.get("desiredNumberScheduled", 0)),
        int(status.get("currentNumberScheduled", 0)),
        int(status.get("numberReady", 0)),
        int(status.get("updatedNumberScheduled", 0)),
        int(status.get("numberAvailable", 0)),
        "7d",
    ]


def _k8s_pvc_cells(obj: dict[str, Any]) -> list[Any]:
    spec = obj.get("spec", {})
    status = obj.get("status", {})
    return [
        obj["metadata"]["name"],
        status.get("phase", ""),
        spec.get("volumeName", ""),
        status.get("capacity", {}).get("storage", ""),
        ",".join(status.get("accessModes", [])),
        spec.get("storageClassName", ""),
        "7d",
    ]


def _k8s_statefulset_cells(obj: dict[str, Any]) -> list[Any]:
    status = obj.get("status", {})
    return [
        obj["metadata"]["name"],
        f"{int(status.get('readyReplicas', 0))}/{int(status.get('replicas', 0))}",
        "7d",
    ]


def _k8s_ingress_cells(obj: dict[str, Any]) -> list[Any]:
    spec = obj.get("spec", {})
    status = obj.get("status", {})
    rules = spec.get("rules", [])
    ingress = status.get("loadBalancer", {}).get("ingress", [])
    return [
        obj["metadata"]["name"],
        spec.get("ingressClassName", ""),
        ",".join(rule.get("host", "") for rule in rules),
        ",".join(item.get("ip", "") for item in ingress),
        "80,443",
        "7d",
    ]


def _k8s_secret_cells(obj: dict[str, Any]) -> list[Any]:
    return [
        obj["metadata"]["name"],
        obj.get("type", "Opaque"),
        len(obj.get("data", {})),
        "7d",
    ]


def _k8s_configmap_cells(obj: dict[str, Any]) -> list[Any]:
    return [obj["metadata"]["name"], len(obj.get("data", {})), "7d"]


def _k8s_serviceaccount_cells(obj: dict[str, Any]) -> list[Any]:
    return [obj["metadata"]["name"], len(obj.get("secrets", [])), "7d"]


def _k8s_job_cells(obj: dict[str, Any]) -> list[Any]:
    status = obj.get("status", {})
    succeeded = int(status.get("succeeded", 0))
    completions = int(obj.get("spec", {}).get("completions", 1))
    return [obj["metadata"]["name"], f"{succeeded}/{completions}", "2m14s", "6d"]


def _k8s_cronjob_cells(obj: dict[str, Any]) -> list[Any]:
    spec = obj.get("spec", {})
    status = obj.get("status", {})
    return [
        obj["metadata"]["name"],
        spec.get("schedule", ""),
        str(spec.get("suspend", False)),
        len(status.get("active", [])),
        "18h",
        "7d",
    ]


def _k8s_namespace_cells(obj: dict[str, Any]) -> list[Any]:
    return [
        obj["metadata"]["name"],
        obj.get("status", {}).get("phase", "Active"),
        "7d",
    ]


def _k8s_default_cells(obj: dict[str, Any]) -> list[Any]:
    return [obj.get("metadata", {}).get("name", ""), "7d"]

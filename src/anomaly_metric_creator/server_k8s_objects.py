"""Kubernetes-compatible REST object builders.

Leaf extracted from ``server_ops.py`` (epic ``07-06-server-ops-decomposition``
step 4). Owns the per-kind object-dict builders and their metadata / owner /
label / container-state / timestamp / pod-ip helpers. Reads the shared
snapshot/label/timestamp/string accessors and the release identity constant
downward from ``server_ops_support``; the ``_k8s_objects_for_resource``
dispatcher, ``_helm_secret_objects``, and the snapshot-coupled
``_k8s_endpointslice`` builder (which resolves the full ``resource_snapshot``)
stay in ``server_ops`` (steps 5 / 3) and call these builders through
``server_ops``'s re-import. Never imports ``server_ops`` (one-way).
``SimulationState`` is annotation-only under
``from __future__ import annotations``.
"""

from __future__ import annotations

import datetime as _dt
from typing import TYPE_CHECKING, Any

from .server_ops_support import (
    DEFAULT_RELEASE,
    _parse_optional_timestamp,
    _parse_user_timestamp,
    _snapshot_row_labels,
    _snapshot_row_namespace,
    _string_dict,
)

if TYPE_CHECKING:  # type-checking only; never executed, so the one-way rule holds
    from .server_ops import SimulationState


def _k8s_namespace(state: SimulationState) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": _k8s_metadata(state, state.namespace),
        "status": {"phase": "Active"},
    }


def _k8s_pod(state: SimulationState, pod: dict[str, Any]) -> dict[str, Any]:
    ready = pod["ready"].split("/")[0] == pod["ready"].split("/")[1]
    status_text = pod["status"]
    phase = "Failed" if status_text == "Error" else "Running"
    component = pod["component"]
    namespace = _snapshot_row_namespace(pod, state.namespace)
    replicaset_name = f"{component}-6d9f7c8b9d"
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": _k8s_metadata(
            state,
            pod["name"],
            namespace=namespace,
            labels=_k8s_workload_labels(component),
            resource_version=pod.get("resource_version"),
            owner_references=[
                _k8s_owner_reference(
                    "apps/v1",
                    "ReplicaSet",
                    replicaset_name,
                    f"amc-{namespace}-{replicaset_name}",
                )
            ],
        ),
        "spec": {
            "nodeName": pod["node"],
            "containers": [{
                "name": component,
                "image": f"simulated-saas/{component}:0.3.0",
                "ports": [{"containerPort": 8080, "protocol": "TCP"}],
            }],
        },
        "status": {
            "phase": phase,
            "podIP": _stable_pod_ip(pod["name"]),
            "hostIP": "10.0.0.10",
            "startTime": _k8s_timestamp(state.clock.start_time),
            "conditions": [
                {"type": "Initialized", "status": "True"},
                {"type": "Ready", "status": "True" if ready else "False"},
                {"type": "ContainersReady", "status": "True" if ready else "False"},
                {"type": "PodScheduled", "status": "True"},
            ],
            "containerStatuses": [{
                "name": component,
                "ready": ready,
                "restartCount": pod["restarts"],
                "image": f"simulated-saas/{component}:0.3.0",
                "imageID": f"simulated-saas/{component}@sha256:simulated",
                "state": _k8s_container_state(state, status_text),
            }],
        },
    }


def _k8s_configmap(state: SimulationState, configmap: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": _k8s_metadata_for_row(state, configmap),
        "data": configmap["keys"],
    }


def _k8s_secret(state: SimulationState, secret: dict[str, Any]) -> dict[str, Any]:
    data_count = int(secret.get("data", 0) or 0)
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": _k8s_metadata_for_row(state, secret),
        "type": secret.get("type", "Opaque"),
        "data": {f"key{index}": "c2ltdWxhdGVk" for index in range(max(1, data_count))},
    }


def _k8s_serviceaccount(state: SimulationState, serviceaccount: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": _k8s_metadata_for_row(state, serviceaccount),
        "secrets": [],
    }


def _k8s_deployment(state: SimulationState, deployment: dict[str, Any]) -> dict[str, Any]:
    replicas = int(str(deployment["ready"]).split("/", 1)[1])
    ready_replicas = int(str(deployment["ready"]).split("/", 1)[0])
    name = deployment["name"]
    labels = _snapshot_row_labels("deployments", deployment)
    selector = _row_selector(deployment, name)
    template_labels = _row_template_labels(deployment, labels, selector)
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": _k8s_metadata_for_row(
            state,
            deployment,
            labels=labels,
            include_generation=True,
        ),
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": template_labels},
                "spec": {"containers": [{"name": name, "image": f"simulated-saas/{name}:0.3.0"}]},
            },
        },
        "status": {
            "replicas": replicas,
            "readyReplicas": ready_replicas,
            "updatedReplicas": deployment["up_to_date"],
            "availableReplicas": deployment["available"],
            "observedGeneration": int(deployment.get("observed_generation", deployment.get("generation", 1)) or 1),
            "conditions": [{
                "type": "Available",
                "status": "True" if ready_replicas else "False",
                "reason": deployment["status"],
                "message": f"deployment is {deployment['status']}",
            }],
        },
    }


def _k8s_replicaset(state: SimulationState, replicaset: dict[str, Any]) -> dict[str, Any]:
    owner = replicaset["owner"]
    namespace = _snapshot_row_namespace(replicaset, state.namespace)
    return {
        "apiVersion": "apps/v1",
        "kind": "ReplicaSet",
        "metadata": _k8s_metadata(
            state,
            replicaset["name"],
            namespace=namespace,
            labels=_k8s_workload_labels(owner),
            resource_version=replicaset.get("resource_version"),
            owner_references=[
                _k8s_owner_reference(
                    "apps/v1",
                    "Deployment",
                    owner,
                    f"amc-{namespace}-{owner}",
                )
            ],
        ),
        "spec": {
            "replicas": replicaset["desired"],
            "selector": {"matchLabels": {"app.kubernetes.io/name": owner}},
        },
        "status": {
            "replicas": replicaset["current"],
            "fullyLabeledReplicas": replicaset["current"],
            "readyReplicas": replicaset["ready"],
            "availableReplicas": replicaset["ready"],
        },
    }


def _k8s_daemonset(state: SimulationState, daemonset: dict[str, Any]) -> dict[str, Any]:
    name = daemonset["name"]
    labels = _snapshot_row_labels("daemonsets", daemonset)
    selector = _row_selector(daemonset, name)
    return {
        "apiVersion": "apps/v1",
        "kind": "DaemonSet",
        "metadata": _k8s_metadata_for_row(state, daemonset, labels=labels),
        "spec": {
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": _row_template_labels(daemonset, labels, selector)},
                "spec": {"containers": [{"name": name, "image": "simulated-saas/agent:0.3.0"}]},
            },
        },
        "status": {
            "desiredNumberScheduled": daemonset["desired"],
            "currentNumberScheduled": daemonset["current"],
            "numberReady": daemonset["ready"],
            "updatedNumberScheduled": daemonset["up_to_date"],
            "numberAvailable": daemonset["available"],
        },
    }


def _k8s_statefulset(state: SimulationState, sts: dict[str, Any]) -> dict[str, Any]:
    replicas = int(str(sts["ready"]).split("/", 1)[1])
    ready_replicas = int(str(sts["ready"]).split("/", 1)[0])
    name = sts["name"]
    labels = _snapshot_row_labels("statefulsets", sts)
    selector = _row_selector(sts, name)
    return {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": _k8s_metadata_for_row(state, sts, labels=labels, include_generation=True),
        "spec": {
            "replicas": replicas,
            "serviceName": name,
            "selector": {"matchLabels": selector},
        },
        "status": {
            "replicas": replicas,
            "readyReplicas": ready_replicas,
            "observedGeneration": int(sts.get("observed_generation", sts.get("generation", 1)) or 1),
        },
    }


def _k8s_service(state: SimulationState, service: dict[str, Any]) -> dict[str, Any]:
    port = int(service.get("port", 8080) or 8080)
    selector = service.get("selector")
    if not isinstance(selector, dict):
        selector = {"app.kubernetes.io/name": service["name"]}
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": _k8s_metadata_for_row(
            state,
            service,
            labels=_snapshot_row_labels("services", service),
        ),
        "spec": {
            "type": service["type"],
            "clusterIP": service["cluster_ip"],
            "ports": [{"name": "http", "port": port, "protocol": "TCP", "targetPort": port}],
            "selector": {str(key): str(value) for key, value in selector.items()},
        },
    }


def _k8s_endpoints(state: SimulationState, endpoint: dict[str, Any]) -> dict[str, Any]:
    addresses = []
    for item in endpoint["endpoints"].split(","):
        ip, _, _port = item.partition(":")
        if ip:
            addresses.append({"ip": ip})
    return {
        "apiVersion": "v1",
        "kind": "Endpoints",
        "metadata": _k8s_metadata_for_row(state, endpoint),
        "subsets": [{
            "addresses": addresses,
            "ports": [{"name": "http", "port": 8080, "protocol": "TCP"}],
        }],
    }


def _k8s_event(state: SimulationState, event: dict[str, str], index: int) -> dict[str, Any]:
    involved_kind, _, involved_name = event["object"].partition("/")
    first_seen = _parse_optional_timestamp(event.get("first_seen")) or state.clock.now()
    last_seen = _parse_optional_timestamp(event.get("last_seen")) or state.clock.now()
    return {
        "apiVersion": "v1",
        "kind": "Event",
        "metadata": _k8s_metadata(
            state,
            f"{involved_name}.{index}",
            namespace=state.namespace,
        ),
        "involvedObject": {
            "kind": involved_kind.title() if involved_kind else "Pod",
            "namespace": state.namespace,
            "name": involved_name or event["object"],
        },
        "reason": event["reason"],
        "message": event["message"],
        "type": event["type"],
        "count": int(event.get("count", 1) or 1),
        "firstTimestamp": _k8s_timestamp(first_seen),
        "lastTimestamp": _k8s_timestamp(last_seen),
        "source": {"component": "amc-simulator"},
    }


def _k8s_hpa(state: SimulationState, hpa: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "autoscaling/v2",
        "kind": "HorizontalPodAutoscaler",
        "metadata": _k8s_metadata_for_row(state, hpa),
        "spec": {
            "scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": hpa["name"]},
            "minReplicas": hpa["minpods"],
            "maxReplicas": hpa["maxpods"],
            "metrics": [{
                "type": "Resource",
                "resource": {
                    "name": "cpu",
                    "target": {"type": "Utilization", "averageUtilization": 80},
                },
            }],
        },
        "status": {
            "currentReplicas": hpa["replicas"],
            "desiredReplicas": hpa["replicas"],
            "currentMetrics": [{
                "type": "Resource",
                "resource": {
                    "name": "cpu",
                    "current": {"averageUtilization": int(str(hpa["targets"]).split("%", 1)[0])},
                },
            }],
        },
    }


def _k8s_job(state: SimulationState, job: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": _k8s_metadata_for_row(state, job),
        "spec": {"completions": 1, "parallelism": 1},
        "status": {"succeeded": 1, "ready": 0},
    }


def _k8s_cronjob(state: SimulationState, cronjob: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": _k8s_metadata_for_row(state, cronjob),
        "spec": {
            "schedule": cronjob["schedule"],
            "suspend": cronjob["suspend"] == "True",
            "jobTemplate": {"spec": {"template": {"spec": {"restartPolicy": "OnFailure"}}}},
        },
        "status": {"active": [], "lastScheduleTime": _k8s_timestamp(state.clock.now())},
    }


def _k8s_pvc(state: SimulationState, pvc: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": _k8s_metadata_for_row(state, pvc),
        "spec": {
            "accessModes": [pvc["access_modes"]],
            "resources": {"requests": {"storage": pvc["capacity"]}},
            "storageClassName": pvc["storageclass"],
            "volumeName": pvc["volume"],
        },
        "status": {
            "phase": pvc["status"],
            "accessModes": [pvc["access_modes"]],
            "capacity": {"storage": pvc["capacity"]},
        },
    }


def _k8s_ingress(state: SimulationState, ingress: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": _k8s_metadata_for_row(state, ingress),
        "spec": {
            "ingressClassName": ingress["class"],
            "rules": [{
                "host": ingress["hosts"],
                "http": {
                    "paths": [{
                        "path": "/",
                        "pathType": "Prefix",
                        "backend": {
                            "service": {
                                "name": ingress["name"],
                                "port": {"number": 8080},
                            },
                        },
                    }],
                },
            }],
        },
        "status": {"loadBalancer": {"ingress": [{"ip": ingress["address"]}]}},
    }


def _k8s_node(state: SimulationState, node: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Node",
        "metadata": _k8s_metadata(state, node["name"], labels={"kubernetes.io/role": node["roles"]}),
        "status": {
            "capacity": {"cpu": "4", "memory": "16384Mi", "pods": "110"},
            "allocatable": {"cpu": "3900m", "memory": "15000Mi", "pods": "110"},
            "conditions": [{
                "type": "Ready",
                "status": "True" if node["status"] == "Ready" else "False",
                "reason": node["status"],
            }],
            "nodeInfo": {"kubeletVersion": node["version"], "osImage": "AMC Linux"},
        },
    }


def _k8s_pod_metrics(state: SimulationState, pod: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "metrics.k8s.io/v1beta1",
        "kind": "PodMetrics",
        "metadata": _k8s_metadata_for_row(state, pod),
        "timestamp": _k8s_timestamp(state.clock.now()),
        "window": "30s",
        "containers": [{
            "name": pod["component"],
            "usage": {"cpu": f"{pod['cpu_m']}m", "memory": f"{pod['memory_mi']}Mi"},
        }],
    }


def _k8s_node_metrics(state: SimulationState, node: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "metrics.k8s.io/v1beta1",
        "kind": "NodeMetrics",
        "metadata": _k8s_metadata(state, node["name"]),
        "timestamp": _k8s_timestamp(state.clock.now()),
        "window": "30s",
        "usage": {"cpu": f"{node['cpu_m']}m", "memory": f"{node['memory_mi']}Mi"},
    }


def _k8s_metadata(
    state: SimulationState,
    name: str,
    *,
    namespace: str = "",
    labels: dict[str, str] | None = None,
    annotations: dict[str, str] | None = None,
    resource_version: str | int | None = None,
    generation: int | None = None,
    deletion_timestamp: str = "",
    owner_references: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "name": name,
        "uid": "amc-" + (namespace + "-" if namespace else "") + name,
        "resourceVersion": str(resource_version or "1"),
        "creationTimestamp": _k8s_timestamp(state.clock.start_time),
        "labels": labels or {},
    }
    if namespace:
        metadata["namespace"] = namespace
    if generation is not None:
        metadata["generation"] = generation
    if deletion_timestamp:
        metadata["deletionTimestamp"] = _k8s_timestamp(_parse_user_timestamp(deletion_timestamp))
    if annotations:
        metadata["annotations"] = annotations
    if owner_references:
        metadata["ownerReferences"] = owner_references
    return metadata


def _k8s_metadata_for_row(
    state: SimulationState,
    row: dict[str, Any],
    *,
    name: str | None = None,
    namespace: str | None = None,
    labels: dict[str, str] | None = None,
    annotations: dict[str, str] | None = None,
    include_generation: bool = False,
    owner_references: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    row_labels = _string_dict(row.get("labels"))
    row_annotations = _string_dict(row.get("annotations"))
    generation = None
    if include_generation:
        generation = int(row.get("generation", 1) or 1)
    return _k8s_metadata(
        state,
        name or str(row["name"]),
        namespace=namespace or _snapshot_row_namespace(row, state.namespace),
        labels=labels if labels is not None else row_labels,
        annotations=annotations if annotations is not None else row_annotations,
        resource_version=row.get("resource_version"),
        generation=generation,
        deletion_timestamp=str(row.get("deletion_timestamp", "")),
        owner_references=owner_references or row.get("owner_references"),
    )


def _row_selector(row: dict[str, Any], name: str) -> dict[str, str]:
    selector = row.get("selector")
    if isinstance(selector, dict):
        return {str(key): str(value) for key, value in selector.items()}
    return {"app.kubernetes.io/name": name}


def _row_template_labels(
    row: dict[str, Any],
    labels: dict[str, str],
    selector: dict[str, str],
) -> dict[str, str]:
    template_labels = row.get("template_labels")
    if isinstance(template_labels, dict):
        return {str(key): str(value) for key, value in template_labels.items()}
    return {**labels, **selector}


def _selector_string(selector: dict[str, str]) -> str:
    return ",".join(f"{key}={value}" for key, value in sorted(selector.items()))


def _k8s_owner_reference(
    api_version: str,
    kind: str,
    name: str,
    uid: str,
) -> dict[str, Any]:
    return {
        "apiVersion": api_version,
        "kind": kind,
        "name": name,
        "uid": uid,
        "controller": True,
        "blockOwnerDeletion": True,
    }


def _k8s_workload_labels(component: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/name": component,
        "app.kubernetes.io/instance": DEFAULT_RELEASE,
        "app.kubernetes.io/managed-by": "Helm",
    }


def _k8s_container_state(state: SimulationState, status_text: str) -> dict[str, Any]:
    if status_text == "Running":
        return {"running": {"startedAt": _k8s_timestamp(state.clock.start_time)}}
    if status_text == "CrashLoopBackOff":
        return {
            "waiting": {
                "reason": "CrashLoopBackOff",
                "message": "back-off restarting failed container",
            },
        }
    if status_text == "Error":
        return {
            "terminated": {
                "reason": "Error",
                "exitCode": 1,
                "startedAt": _k8s_timestamp(state.clock.start_time),
                "finishedAt": _k8s_timestamp(state.clock.now()),
            },
        }
    return {"waiting": {"reason": status_text}}


def _k8s_timestamp(value: _dt.datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=0).isoformat() + "Z"


def _stable_pod_ip(name: str) -> str:
    value = sum(ord(ch) for ch in name)
    return f"10.244.{value % 200}.{(value // 5) % 240 + 10}"

"""Overlay-aware resource snapshot for the serve-mode simulator.

``resource_snapshot()`` is the one resource model the kubectl, Helm, MCP and
Kubernetes REST surfaces read. This leaf owns it and the component-health,
event, node and replica helpers it needs. ``server_ops`` re-imports every name
at its original position, so the historic ``server_ops.<name>`` and
``server.<name>`` surfaces stay stable. This module never imports
``server_ops`` at runtime; ``SimulationState`` is a type-only import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .server_command_render import _exposed_active_scenarios
from .server_helm_impl import _helm_release, _helm_release_revisions
from .server_k8s_api import _K8S_ADVERTISED_TAG
from .server_k8s_objects import _stable_pod_ip
from .server_mutations import DEFAULT_NAMESPACE, _format_dt, _mutation_resource_key
from .server_ops_profiles import OPS_SCENARIO_PROFILES
from .server_ops_support import DEFAULT_RELEASE, _snapshot_row_namespace

if TYPE_CHECKING:
    from .server_ops import SimulationState
    from .server_ops_profiles import OpsComponentImpact


_SNAPSHOT_KINDS = {
    "namespaces",
    "pods",
    "configmaps",
    "secrets",
    "replicationcontrollers",
    "deployments",
    "replicasets",
    "daemonsets",
    "services",
    "endpoints",
    "endpointslices",
    "events",
    "hpa",
    "jobs",
    "cronjobs",
    "serviceaccounts",
    "nodes",
    "pvc",
    "statefulsets",
    "ingress",
}


_CLUSTER_SCOPED_SNAPSHOT_KINDS = {"namespaces", "nodes"}


_NAMESPACED_SNAPSHOT_KINDS = _SNAPSHOT_KINDS - _CLUSTER_SCOPED_SNAPSHOT_KINDS


def _snapshot_row_key(row: dict[str, Any], default_namespace: str = DEFAULT_NAMESPACE) -> str:
    return _mutation_resource_key(_snapshot_row_namespace(row, default_namespace), str(row.get("name", "")))


def _snapshot_kind_namespaced(kind: str) -> bool:
    return kind in _NAMESPACED_SNAPSHOT_KINDS or kind in {"hpa", "pvc", "ingress"}


def resource_snapshot(state: SimulationState) -> dict[str, list[dict[str, Any]]]:
    pods: list[dict[str, Any]] = []
    deployments: list[dict[str, Any]] = []
    replicasets: list[dict[str, Any]] = []
    services: list[dict[str, Any]] = []
    endpoints: list[dict[str, Any]] = []
    endpointslices: list[dict[str, Any]] = []
    hpas: list[dict[str, Any]] = []
    pvcs: list[dict[str, Any]] = []
    statefulsets: list[dict[str, Any]] = []
    daemonsets: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    cronjobs: list[dict[str, Any]] = []
    configmaps: list[dict[str, Any]] = []
    serviceaccounts: list[dict[str, Any]] = []
    ingress: list[dict[str, Any]] = []
    nodes = _node_rows(state)

    serviceaccounts.extend([
        {"name": "default", "secrets": 0, "age": "30d"},
        {"name": DEFAULT_RELEASE, "secrets": 0, "age": "7d"},
    ])
    configmaps.extend([
        {
            "name": "simulated-saas-config",
            "data": 4,
            "age": "7d",
            "keys": {
                "LOG_LEVEL": "info",
                "FEATURE_FLAGS": "checkout_v2,adaptive_cache",
                "OTEL_EXPORTER": "enabled",
                "SCENARIOS": ",".join(_exposed_active_scenarios(state)),
            },
        },
        {
            "name": "simulated-saas-runbook",
            "data": 2,
            "age": "7d",
            "keys": {
                "summary": "Synthetic incident runbook for AMC server mode",
                "first_steps": "kubectl get pods; kubectl get events; helm status simulated-saas",
            },
        },
    ])

    with state.mutations.lock:
        deleted_components = {
            name for name, mutation in state.mutations.workloads.items()
            if mutation.deleted
        }
        deleted_pods = set(state.mutations.deleted_pods)
        workload_metadata: dict[str, dict[str, Any]] = {
            name: {
                "generation": mutation.generation,
                "observed_generation": mutation.observed_generation,
                "resource_version": str(mutation.resource_version),
                "deletion_timestamp": mutation.deletion_timestamp,
            }
            for name, mutation in state.mutations.workloads.items()
        }

    for component in state.components:
        if component in deleted_components:
            continue
        health = _component_health(state, component)
        replicas = _replica_count(state, component)
        ready_replicas = min(replicas, health["ready_replicas"])
        metadata = workload_metadata.get(component, {})
        generation = int(metadata.get("generation", 1) or 1)
        observed_generation = int(metadata.get("observed_generation", generation) or generation)
        resource_version = str(metadata.get("resource_version", "1") or "1")
        deployments.append({
            "name": component,
            "ready": f"{ready_replicas}/{replicas}",
            "up_to_date": ready_replicas,
            "available": ready_replicas,
            "age": "7d",
            "status": health["deployment_status"],
            "generation": generation,
            "observed_generation": observed_generation,
            "resource_version": resource_version,
        })
        replicasets.append({
            "name": f"{component}-6d9f7c8b9d",
            "desired": replicas,
            "current": replicas,
            "ready": ready_replicas,
            "age": "7d",
            "owner": component,
            "resource_version": resource_version,
        })
        services.append({
            "name": component,
            "type": "ClusterIP",
            "cluster_ip": _stable_cluster_ip(component),
            "external_ip": "<none>",
            "ports": "8080/TCP",
            "age": "7d",
        })
        endpoints.append({
            "name": component,
            "endpoints": "",
            "ports": "8080",
            "age": "7d",
        })
        endpointslices.append({
            "name": f"{component}-slice",
            "address_type": "IPv4",
            "ports": "8080",
            "endpoints": 0,
            "age": "7d",
            "service": component,
        })
        hpas.append({
            "name": component,
            "reference": f"Deployment/{component}",
            "targets": f"{health['cpu_pct']}%/80%",
            "minpods": 1,
            "maxpods": 8,
            "replicas": replicas,
            "age": "7d",
        })
        endpoint_ips: list[str] = []
        deleted_for_component: list[str] = []
        # Loop-invariant per component: hoist above the replica loop so a
        # high --instances-per-component run does not recompute the profile
        # scan / mutations-lock walk once per pod. The returned lists are
        # read-only downstream, so sharing one object across pods is
        # output-identical.
        component_scenario_ids = _exposed_component_scenarios(state, component)
        component_events = _component_events(state, component)
        for index in range(replicas):
            pod_name = _pod_name(component, index)
            if pod_name in deleted_pods:
                deleted_for_component.append(pod_name)
                continue
            pod_ip = _stable_pod_ip(pod_name)
            endpoint_ips.append(pod_ip)
            pods.append({
                "name": pod_name,
                "component": component,
                "ready": health["ready"],
                "status": health["pod_status"],
                "restarts": health["restarts"] + (1 if index == 0 and health["pod_status"] != "Running" else 0),
                "age": "7d",
                "node": nodes[index % len(nodes)]["name"],
                "pod_ip": pod_ip,
                "cpu_m": health["cpu_m"],
                "memory_mi": health["memory_mi"],
                "scenario_ids": component_scenario_ids,
                "events": component_events,
                "resource_version": resource_version,
            })
        for replacement_index, deleted_pod_name in enumerate(deleted_for_component):
            replacement_name = f"{component}-recreated-{replacement_index}"
            pod_ip = _stable_pod_ip(replacement_name)
            endpoint_ips.append(pod_ip)
            pods.append({
                "name": replacement_name,
                "component": component,
                "ready": health["ready"],
                "status": health["pod_status"],
                "restarts": health["restarts"],
                "age": "0s",
                "node": nodes[(replicas + replacement_index) % len(nodes)]["name"],
                "pod_ip": pod_ip,
                "cpu_m": health["cpu_m"],
                "memory_mi": health["memory_mi"],
                "scenario_ids": component_scenario_ids,
                "events": component_events,
                "recreated_from": deleted_pod_name,
                "resource_version": resource_version,
            })
        endpoints[-1]["endpoints"] = ",".join(f"{ip}:8080" for ip in endpoint_ips[:3])
        endpointslices[-1]["endpoints"] = len(endpoint_ips)
        if component == "database":
            statefulsets.append({
                "name": "database",
                "ready": f"{ready_replicas}/{replicas}",
                "age": "7d",
            })
            pvcs.append({
                "name": "database-data-database-0",
                "status": "Bound",
                "volume": "pvc-database-0",
                "capacity": "200Gi",
                "access_modes": "RWO",
                "storageclass": "gp3",
                "age": "7d",
                "used_pct": health["pvc_used_pct"],
            })
    if "observabilitypipeline" in state.components:
        daemonsets.append({
            "name": "observability-agent",
            "desired": len(nodes),
            "current": len(nodes),
            "ready": len(nodes),
            "up_to_date": len(nodes),
            "available": len(nodes),
            "node_selector": "kubernetes.io/os=linux",
            "age": "7d",
        })
    else:
        daemonsets.append({
            "name": "node-observer",
            "desired": len(nodes),
            "current": len(nodes),
            "ready": len(nodes),
            "up_to_date": len(nodes),
            "available": len(nodes),
            "node_selector": "kubernetes.io/os=linux",
            "age": "7d",
        })
    jobs.append({
        "name": "scheduler-backfill",
        "completions": "1/1",
        "duration": "2m14s",
        "age": "6d",
    })
    cronjobs.append({
        "name": "scheduler-nightly",
        "schedule": "15 2 * * *",
        "suspend": "False",
        "active": 0,
        "last_schedule": "18h",
        "age": "7d",
    })
    if "apigateway" in state.components:
        ingress.append({
            "name": "apigateway",
            "class": "nginx",
            "hosts": "api.simulated-saas.local",
            "address": "10.0.0.20",
            "ports": "80,443",
            "age": "7d",
        })
    snapshot = {
        "namespaces": [{"name": state.namespace, "status": "Active", "age": "30d"}],
        "pods": pods,
        "configmaps": configmaps,
        "secrets": [
            {
                "name": f"sh.helm.release.v1.{DEFAULT_RELEASE}.v{revision['version']}",
                "type": "helm.sh/release.v1",
                "data": 1,
                "age": "7d",
            }
            for revision in _helm_release_revisions(state)
        ],
        "replicationcontrollers": [],
        "deployments": deployments,
        "replicasets": replicasets,
        "daemonsets": daemonsets,
        "services": services,
        "endpoints": endpoints,
        "endpointslices": endpointslices,
        "hpa": hpas,
        "nodes": nodes,
        "pvc": pvcs,
        "statefulsets": statefulsets,
        "jobs": jobs,
        "cronjobs": cronjobs,
        "serviceaccounts": serviceaccounts,
        "ingress": ingress,
        "events": _event_rows(state),
        "helm_releases": [_helm_release(state)],
    }
    _apply_default_namespaces(state, snapshot)
    _apply_mutation_rows(state, snapshot)
    return snapshot


def _apply_default_namespaces(state: SimulationState, snapshot: dict[str, list[dict[str, Any]]]) -> None:
    for kind, rows in snapshot.items():
        if not _snapshot_kind_namespaced(kind):
            continue
        for row in rows:
            row.setdefault("namespace", state.namespace)


def _apply_mutation_rows(state: SimulationState, snapshot: dict[str, list[dict[str, Any]]]) -> None:
    with state.mutations.lock:
        deleted = {
            kind: set(names)
            for kind, names in state.mutations.deleted_resources.items()
        }
        created = {
            kind: {name: dict(row) for name, row in rows.items()}
            for kind, rows in state.mutations.created_resources.items()
        }
    for kind, rows in snapshot.items():
        if kind in {"events", "helm_releases"}:
            continue
        deleted_names = deleted.get(kind, set())
        if deleted_names:
            snapshot[kind] = [
                row for row in rows
                if _snapshot_row_key(row, state.namespace) not in deleted_names
            ]
        if kind in created:
            existing = {
                _snapshot_row_key(row, state.namespace): index
                for index, row in enumerate(snapshot[kind])
            }
            for key, row in created[kind].items():
                if key in existing:
                    snapshot[kind][existing[key]] = row
                else:
                    snapshot[kind].append(row)


_DEPLOYMENT_STATUS_PRIORITY = {
    "Healthy": 0,
    "TrafficBurst": 1,
    "ScenarioInfluenced": 1,
    "RecoveredAfterRollback": 1,
    "RetryPressure": 2,
    "CacheMissPressure": 2,
    "DatabaseBackpressure": 2,
    "AuthDependencyDegraded": 2,
    "DNSDependencyFailure": 2,
    "NetworkDegraded": 2,
    "FallbackServing": 2,
    "InferenceFallback": 2,
    "EndpointChurn": 2,
    "Backpressure": 2,
    "TelemetryBacklog": 2,
    "TenantImport": 2,
    "ContextCachePressure": 2,
    "HotKeyChurn": 2,
    "JWKSCacheChurn": 2,
    "DependencyDegraded": 3,
    "RateLimited": 3,
    "CPUSaturated": 3,
    "CacheDegraded": 3,
    "ReadPressure": 3,
    "DatabaseStall": 3,
    "QueueBacklog": 3,
    "HealthCheckFlap": 3,
    "ObjectStore5xx": 3,
    "Upstream5xx": 3,
    "IndexRebuild": 3,
    "RetrievalDegraded": 3,
    "QueueOverflow": 3,
    "Provider5xx": 3,
    "CheckoutDegraded": 3,
    "JWKSCacheMiss": 3,
    "TokenValidationSlow": 3,
    "IngestLag": 3,
    "LLMSurge": 3,
    "LargeContext": 3,
    "LookupPressure": 3,
    "ProviderRateLimited": 3,
    "BatchPressure": 3,
    "BandwidthPressure": 3,
    "BatchWritePressure": 3,
    "BatchEvictions": 3,
    "ViralTraffic": 3,
    "GatewayPressure": 3,
    "MetadataWritePressure": 3,
    "GPUFragmented": 3,
    "RegionalFailover": 3,
    "FailoverSaturated": 3,
    "ReplicationLag": 3,
    "FailoverPressure": 3,
    "ReplayBacklog": 3,
    "ProviderUnavailable": 3,
    "ProviderOutage": 3,
    "FallbackPressure": 3,
    "StoragePressure": 3,
    "StorageWait": 3,
    "UploadDegraded": 3,
    "RolledBack": 3,
    "NetworkPartition": 3,
    "CertRotation": 3,
    "JWKSRotation": 3,
    "TokenValidationFailing": 3,
    "WriteBacklog": 3,
    "PartialOutage": 4,
    "AZIsolated": 4,
    "Degraded": 4,
}


_POD_STATUS_PRIORITY = {
    "Running": 0,
    "Pending": 1,
    "CrashLoopBackOff": 3,
    "Error": 4,
}


def _component_health(state: SimulationState, component: str) -> dict[str, Any]:
    replicas = _replica_count(state, component)
    health: dict[str, Any] = {
        "pod_status": "Running",
        "deployment_status": "Healthy",
        "ready": "1/1",
        "ready_replicas": replicas,
        "restarts": 0,
        "cpu_pct": 36,
        "cpu_m": 180,
        "memory_mi": 384,
        "memory_pct": 42,
        "pvc_used_pct": 61,
    }
    impacts = _component_impacts(state, component)
    for impact in impacts:
        _apply_component_impact(health, impact, replicas)
    scenarios = _component_scenarios(state, component)
    if scenarios and not impacts:
        health.update({"deployment_status": "ScenarioInfluenced", "cpu_pct": 55, "cpu_m": 550})
    with state.mutations.lock:
        mutation = state.mutations.workloads.get(component)
        if mutation is not None:
            if mutation.deployment_status:
                health["deployment_status"] = mutation.deployment_status
            if mutation.pod_status:
                health["pod_status"] = mutation.pod_status
            if mutation.ready_replicas is not None:
                health["ready_replicas"] = mutation.ready_replicas
            if mutation.restarts_delta:
                health["restarts"] += mutation.restarts_delta
            if mutation.deleted:
                health.update({
                    "deployment_status": "Deleted",
                    "pod_status": "Terminating",
                    "ready_replicas": 0,
                    "ready": "0/1",
                })
    health["ready_replicas"] = max(0, min(replicas, health["ready_replicas"]))
    return health


def _component_impacts(state: SimulationState, component: str) -> list[OpsComponentImpact]:
    return [
        impact
        for profile in state.profiles()
        for impact in profile.impacts
        if impact.component == component
    ]


def _apply_component_impact(
    health: dict[str, Any],
    impact: OpsComponentImpact,
    replicas: int,
) -> None:
    if _status_priority(
        impact.deployment_status,
        _DEPLOYMENT_STATUS_PRIORITY,
    ) >= _status_priority(
        health["deployment_status"],
        _DEPLOYMENT_STATUS_PRIORITY,
    ):
        health["deployment_status"] = impact.deployment_status
    if _status_priority(impact.pod_status, _POD_STATUS_PRIORITY) >= _status_priority(
        health["pod_status"],
        _POD_STATUS_PRIORITY,
    ):
        health["pod_status"] = impact.pod_status
    if impact.ready:
        health["ready"] = impact.ready
    elif impact.pod_status != "Running":
        health["ready"] = "0/1"
    if impact.ready_replicas is not None:
        health["ready_replicas"] = impact.ready_replicas
    elif impact.ready_replicas_delta:
        health["ready_replicas"] += impact.ready_replicas_delta
    health["ready_replicas"] = max(0, min(replicas, health["ready_replicas"]))
    health["restarts"] += impact.restarts
    if impact.cpu_pct is not None:
        health["cpu_pct"] = max(health["cpu_pct"], impact.cpu_pct)
        health["cpu_m"] = max(health["cpu_m"], impact.cpu_m or impact.cpu_pct * 10)
    elif impact.cpu_m is not None:
        health["cpu_m"] = max(health["cpu_m"], impact.cpu_m)
    if impact.memory_mi is not None:
        health["memory_mi"] = max(health["memory_mi"], impact.memory_mi)
    if impact.memory_pct is not None:
        health["memory_pct"] = max(health["memory_pct"], impact.memory_pct)
    if impact.pvc_used_pct is not None:
        health["pvc_used_pct"] = max(health["pvc_used_pct"], impact.pvc_used_pct)


def _status_priority(status: str, priority: dict[str, int]) -> int:
    return priority.get(status, 2)


def _component_scenarios(state: SimulationState, component: str) -> list[str]:
    matches = []
    for scenario_id in state.active_scenarios:
        profile = OPS_SCENARIO_PROFILES.get(scenario_id)
        if profile is not None:
            affected = set(profile.affected_components)
        else:
            affected = set(state.legacy.SCENARIOS[scenario_id].components_touched)
        if component in affected:
            matches.append(scenario_id)
    return matches


def _exposed_component_scenarios(state: SimulationState, component: str) -> list[str]:
    """Per-component scenario slugs for pod snapshot rows; empty in eval
    mode. See :func:`_exposed_active_scenarios` for the rationale. The
    behavioral :func:`_component_scenarios` (which drives the
    ``ScenarioInfluenced`` health signal) is intentionally *not* gated, so
    symptoms stay visible while the labels do not.
    """
    return [] if state.eval_mode else _component_scenarios(state, component)


def _component_events(state: SimulationState, component: str) -> list[str]:
    events: list[str] = []
    for profile in state.profiles():
        if component in profile.affected_components:
            events.extend(profile.events)
    if not events:
        events.append(f"Normal Healthy {component} probes passing")
    with state.mutations.lock:
        for event in state.mutations.extra_events:
            obj = event.get("object", "")
            if obj.endswith(f"/{component}") or obj.startswith(f"pod/{component}-"):
                events.append(
                    f"{event.get('type', 'Normal')} {event.get('reason', 'Mutation')} "
                    f"{event.get('message', '')}".strip()
                )
    return events


def _event_rows(state: SimulationState) -> list[dict[str, str]]:
    rows = []
    now = _format_dt(state.clock.now())
    for profile in state.profiles():
        target = profile.affected_components[0] if profile.affected_components else "cluster"
        for event in profile.events:
            parts = event.split(" ", 2)
            event_type = parts[0] if parts else "Normal"
            reason = parts[1] if len(parts) > 1 else "Scenario"
            message = parts[2] if len(parts) > 2 else event
            rows.append({
                "last_seen": now,
                "type": event_type,
                "reason": reason,
                "object": f"pod/{_pod_name(target, 0)}",
                "message": message,
            })
    if not rows:
        rows.append({
            "last_seen": now,
            "type": "Normal",
            "reason": "Healthy",
            "object": "deployment/simulated-saas",
            "message": "all simulated workloads are healthy",
        })
    with state.mutations.lock:
        rows.extend(dict(event) for event in state.mutations.extra_events)
    return rows


def _node_rows(state: SimulationState) -> list[dict[str, Any]]:
    partition = "network_partition_az_split" in state.active_scenarios
    return [
        {
            "name": "ip-10-0-1-21",
            "status": "Ready",
            "roles": "worker",
            "age": "30d",
            "version": _K8S_ADVERTISED_TAG,
            "cpu_m": 2100,
            "cpu_pct": 52,
            "memory_mi": 9240,
            "memory_pct": 58,
        },
        {
            "name": "ip-10-0-2-17",
            "status": "Ready",
            "roles": "worker",
            "age": "30d",
            "version": _K8S_ADVERTISED_TAG,
            "cpu_m": 1840,
            "cpu_pct": 46,
            "memory_mi": 8120,
            "memory_pct": 51,
        },
        {
            "name": "ip-10-0-3-42",
            "status": "NotReady" if partition else "Ready",
            "roles": "worker",
            "age": "30d",
            "version": _K8S_ADVERTISED_TAG,
            "cpu_m": 2600 if partition else 1760,
            "cpu_pct": 78 if partition else 44,
            "memory_mi": 10400 if partition else 7900,
            "memory_pct": 73 if partition else 49,
        },
    ]


def _replica_count(state: SimulationState, component: str) -> int:
    with state.mutations.lock:
        mutation = state.mutations.workloads.get(component)
        if mutation is not None and mutation.replicas is not None:
            return mutation.replicas
    if getattr(state.args, "instances_per_component", 1) > 1:
        return int(state.args.instances_per_component)
    if component in {"apigateway", "authservice", "cacheservice"}:
        return 3
    return 1


def _pod_name(component: str, index: int) -> str:
    if component == "database":
        return f"database-{index}"
    return f"{component}-{index}"


def _stable_cluster_ip(component: str) -> str:
    value = sum(ord(ch) for ch in component)
    return f"10.96.{value % 200}.{(value // 3) % 240 + 10}"

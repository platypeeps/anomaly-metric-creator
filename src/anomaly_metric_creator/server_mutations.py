"""Mutable simulator overlay state for server mode."""

from __future__ import annotations

import datetime as _dt
import threading
from dataclasses import dataclass, field
from typing import Any

from .server_traces import DEFAULT_TRACE_LIMIT


DEFAULT_NAMESPACE = "saas-prod"


def _format_dt(value: _dt.datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _mutation_resource_key(namespace: str, name: str) -> str:
    return f"{namespace or DEFAULT_NAMESPACE}/{name}"


def _component_from_pod_name(name: str) -> str:
    if not name:
        return ""
    prefix, _, suffix = name.rpartition("-")
    return prefix if suffix.isdigit() else name.split("-", 1)[0]


def _resource_prefix(kind: str) -> str:
    return {
        "namespaces": "namespace",
        "pods": "pod",
        "configmaps": "configmap",
        "secrets": "secret",
        "deployments": "deployment",
        "replicasets": "replicaset",
        "daemonsets": "daemonset",
        "services": "service",
        "endpoints": "endpoints",
        "endpointslices": "endpointslice",
        "events": "event",
        "hpa": "horizontalpodautoscaler",
        "jobs": "job",
        "cronjobs": "cronjob",
        "serviceaccounts": "serviceaccount",
        "nodes": "node",
        "pvc": "persistentvolumeclaim",
        "statefulsets": "statefulset",
        "ingress": "ingress",
    }.get(kind, kind.rstrip("s"))


@dataclass
class WorkloadMutation:
    replicas: int | None = None
    deployment_status: str = ""
    pod_status: str = ""
    ready_replicas: int | None = None
    restarts_delta: int = 0
    deleted: bool = False
    generation: int = 1
    observed_generation: int = 1
    resource_version: int = 1
    deletion_timestamp: str = ""
    updated_at: str = ""


@dataclass
class HelmReleaseMutation:
    revisions: list[dict[str, Any]] | None = None
    uninstalled: bool = False
    values: dict[str, str] = field(default_factory=dict)
    updated_at: str = ""


@dataclass
class SimulationMutations:
    """Thread-safe in-memory overlay for mutating simulator operations."""

    workloads: dict[str, WorkloadMutation] = field(default_factory=dict)
    deleted_pods: set[str] = field(default_factory=set)
    deleted_resources: dict[str, set[str]] = field(default_factory=dict)
    created_resources: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    extra_events: list[dict[str, Any]] = field(default_factory=list)
    extra_event_limit: int = DEFAULT_TRACE_LIMIT
    release: HelmReleaseMutation = field(default_factory=HelmReleaseMutation)
    version: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock)

    def summary(self) -> dict[str, Any]:
        with self.lock:
            return {
                "version": self.version,
                "workloads": {
                    name: {
                        "replicas": mutation.replicas,
                        "deployment_status": mutation.deployment_status,
                        "pod_status": mutation.pod_status,
                        "ready_replicas": mutation.ready_replicas,
                        "restarts_delta": mutation.restarts_delta,
                        "deleted": mutation.deleted,
                        "generation": mutation.generation,
                        "observed_generation": mutation.observed_generation,
                        "resource_version": str(mutation.resource_version),
                        "deletion_timestamp": mutation.deletion_timestamp,
                        "updated_at": mutation.updated_at,
                    }
                    for name, mutation in sorted(self.workloads.items())
                },
                "deleted_pods": sorted(self.deleted_pods),
                "deleted_resources": {
                    kind: sorted(names)
                    for kind, names in sorted(self.deleted_resources.items())
                    if names
                },
                "created_resources": {
                    kind: sorted(
                        f"{row.get('namespace', DEFAULT_NAMESPACE)}/{row.get('name', key.rsplit('/', 1)[-1])}"
                        for key, row in items.items()
                    )
                    for kind, items in sorted(self.created_resources.items())
                    if items
                },
                "extra_event_count": len(self.extra_events),
                "extra_event_limit": self.extra_event_limit,
                "drift": {
                    "workloads": len(self.workloads),
                    "deleted_pods": len(self.deleted_pods),
                    "created_resources": sum(len(items) for items in self.created_resources.values()),
                    "deleted_resources": sum(len(items) for items in self.deleted_resources.values()),
                    "event_overlays": len(self.extra_events),
                    "namespaces": sorted({
                        row.get("namespace", DEFAULT_NAMESPACE)
                        for items in self.created_resources.values()
                        for row in items.values()
                    }),
                },
                "release": {
                    "uninstalled": self.release.uninstalled,
                    "revision_count": len(self.release.revisions or []),
                    "values": dict(sorted(self.release.values.items())),
                    "updated_at": self.release.updated_at,
                },
            }

    def record_event(self, event_type: str, reason: str, obj: str, message: str, now: _dt.datetime) -> None:
        with self.lock:
            timestamp = _format_dt(now)
            for event in self.extra_events:
                if (
                    event.get("type") == event_type
                    and event.get("reason") == reason
                    and event.get("object") == obj
                    and event.get("message") == message
                ):
                    event["last_seen"] = timestamp
                    event["count"] = int(event.get("count", 1)) + 1
                    self.version += 1
                    return
            self.extra_events.append({
                "first_seen": timestamp,
                "last_seen": timestamp,
                "type": event_type,
                "reason": reason,
                "object": obj,
                "message": message,
                "count": 1,
            })
            limit = max(self.extra_event_limit, 0)
            if limit:
                overflow = len(self.extra_events) - limit
                if overflow > 0:
                    del self.extra_events[:overflow]
            else:
                self.extra_events.clear()
            self.version += 1

    def set_workload(
        self,
        component: str,
        *,
        now: _dt.datetime,
        replicas: int | None = None,
        deployment_status: str = "",
        pod_status: str = "",
        ready_replicas: int | None = None,
        restarts_delta: int = 0,
        deleted: bool | None = None,
    ) -> WorkloadMutation:
        with self.lock:
            mutation = self.workloads.setdefault(component, WorkloadMutation())
            spec_changed = False
            if replicas is not None:
                replicas = max(0, replicas)
                spec_changed = mutation.replicas != replicas
                mutation.replicas = replicas
            if deployment_status:
                mutation.deployment_status = deployment_status
            if pod_status:
                mutation.pod_status = pod_status
            if ready_replicas is not None:
                mutation.ready_replicas = max(0, ready_replicas)
            if restarts_delta:
                mutation.restarts_delta += restarts_delta
            if deleted is not None:
                spec_changed = spec_changed or mutation.deleted != deleted
                mutation.deleted = deleted
                mutation.deletion_timestamp = _format_dt(now) if deleted else ""
            if spec_changed:
                mutation.generation += 1
            mutation.observed_generation = mutation.generation
            mutation.updated_at = _format_dt(now)
            self.version += 1
            mutation.resource_version = max(mutation.resource_version + 1, self.version + 1)
            return mutation

    def delete_pod(self, pod_name: str, *, now: _dt.datetime) -> None:
        component = _component_from_pod_name(pod_name)
        with self.lock:
            self.deleted_pods.add(pod_name)
        self.set_workload(
            component,
            now=now,
            deployment_status="Restarting",
            pod_status="Running",
            restarts_delta=1,
        )
        self.record_event(
            "Normal",
            "Killing",
            f"pod/{pod_name}",
            f"pod {pod_name} deleted; controller recreated replacement pod",
            now,
        )

    def put_resource(
        self,
        kind: str,
        name: str,
        row: dict[str, Any],
        *,
        now: _dt.datetime,
        namespace: str = DEFAULT_NAMESPACE,
    ) -> None:
        with self.lock:
            stored = dict(row)
            namespace = str(namespace or stored.get("namespace") or DEFAULT_NAMESPACE)
            stored["name"] = name
            stored["namespace"] = namespace
            stored["resource_version"] = str(
                max(int(str(stored.get("resource_version", "1")) or 1), self.version + 2)
            )
            key = _mutation_resource_key(namespace, name)
            self.created_resources.setdefault(kind, {})[key] = stored
            self.deleted_resources.setdefault(kind, set()).discard(key)
            self.version += 1
        self.record_event(
            "Normal",
            "Configured",
            f"{_resource_prefix(kind)}/{name}",
            f"{_resource_prefix(kind)} {name} configured in simulator state",
            now,
        )

    def delete_resource(
        self,
        kind: str,
        name: str,
        *,
        now: _dt.datetime,
        namespace: str = DEFAULT_NAMESPACE,
    ) -> None:
        with self.lock:
            key = _mutation_resource_key(namespace, name)
            self.created_resources.setdefault(kind, {}).pop(key, None)
            self.deleted_resources.setdefault(kind, set()).add(key)
            self.version += 1
        self.record_event(
            "Normal",
            "Deleted",
            f"{_resource_prefix(kind)}/{name}",
            f"{_resource_prefix(kind)} {name} deleted from simulator state",
            now,
        )

    def reset(self) -> None:
        with self.lock:
            self.workloads.clear()
            self.deleted_pods.clear()
            self.deleted_resources.clear()
            self.created_resources.clear()
            self.extra_events.clear()
            self.release = HelmReleaseMutation()
            self.version += 1

    def current_revisions(self, base: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self.lock:
            if self.release.revisions is None:
                return [dict(item) for item in base]
            return [dict(item) for item in self.release.revisions]

    def set_revisions(self, revisions: list[dict[str, Any]], *, now: _dt.datetime, uninstalled: bool = False) -> None:
        with self.lock:
            self.release.revisions = [dict(item) for item in revisions]
            self.release.uninstalled = uninstalled
            if uninstalled:
                self.release.values.clear()
            self.release.updated_at = _format_dt(now)
            self.version += 1

    def set_release_values(self, values: dict[str, str], *, now: _dt.datetime) -> None:
        with self.lock:
            self.release.values.update(values)
            self.release.updated_at = _format_dt(now)
            self.version += 1

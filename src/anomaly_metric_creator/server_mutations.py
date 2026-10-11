"""Mutable simulator overlay state for server mode."""

from __future__ import annotations

import datetime as _dt
import json
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .artifacts import _atomic_write_text
from .server_traces import DEFAULT_TRACE_LIMIT


DEFAULT_NAMESPACE = "saas-prod"

# Bump when the persisted overlay's shape changes. A file written by a newer
# version is refused at load rather than half-hydrated, matching the command
# trace store's posture.
MUTATION_STATE_SCHEMA_VERSION = 1

# Which SimulationMutations fields round-trip to disk, and which deliberately
# do not. The union is checked against the live dataclass every time an
# envelope is built, so adding a field without classifying it fails loudly
# instead of silently dropping out of the persisted overlay.
_PERSISTED_MUTATION_FIELDS = frozenset({
    "workloads",
    "deleted_pods",
    "deleted_resources",
    "created_resources",
    "extra_events",
    "release",
    "version",
})
_UNPERSISTED_MUTATION_FIELDS = frozenset({
    # threading.RLock is unserializable by construction.
    "lock",
    # Runtime config for *this* run (from --debug-ring-size), not overlay
    # state -- restoring a previous run's cap would silently override the
    # operator's current flag.
    "extra_event_limit",
    # Where the overlay is being persisted is a property of this process's
    # flags, not of the overlay contents.
    "persist_path",
})


def _format_dt(value: _dt.datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _mutation_resource_key(namespace: str, name: str) -> str:
    return f"{namespace or DEFAULT_NAMESPACE}/{name}"


def _component_from_pod_name(name: str) -> str:
    if not name:
        return ""
    prefix, _, suffix = name.rpartition("-")
    if suffix.isdigit():
        return prefix.removesuffix("-recreated")
    return name.split("-", 1)[0]


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
    persist_path: Path | None = None

    # -- persistence ------------------------------------------------------
    #
    # Opt-in restart continuity (--persist-mutations). Off by default: with
    # persist_path None every hook below is a no-op, so the in-memory path is
    # unchanged.

    def _serialized_locked(self) -> dict[str, Any]:
        """Serialize the overlay. Caller must hold ``self.lock``."""
        declared = {f.name for f in fields(self)}
        classified = _PERSISTED_MUTATION_FIELDS | _UNPERSISTED_MUTATION_FIELDS
        unclassified = declared - classified
        if unclassified:
            raise RuntimeError(
                "SimulationMutations field(s) "
                f"{', '.join(sorted(unclassified))} are not classified as "
                "persisted or unpersisted. Add them to "
                "_PERSISTED_MUTATION_FIELDS or _UNPERSISTED_MUTATION_FIELDS "
                "in server_mutations.py and bump "
                "MUTATION_STATE_SCHEMA_VERSION if the on-disk shape changed."
            )
        stale = classified - declared
        if stale:
            raise RuntimeError(
                "server_mutations.py classifies field(s) "
                f"{', '.join(sorted(stale))} that SimulationMutations no "
                "longer declares."
            )
        return {
            "workloads": {
                name: asdict(mutation)
                for name, mutation in sorted(self.workloads.items())
            },
            "deleted_pods": sorted(self.deleted_pods),
            "deleted_resources": {
                kind: sorted(names)
                for kind, names in sorted(self.deleted_resources.items())
            },
            "created_resources": {
                kind: {key: dict(body) for key, body in sorted(entries.items())}
                for kind, entries in sorted(self.created_resources.items())
            },
            "extra_events": [dict(event) for event in self.extra_events],
            "release": asdict(self.release),
            "version": self.version,
        }

    def envelope(self) -> dict[str, Any]:
        """Return the versioned on-disk envelope for this overlay."""
        with self.lock:
            return self._envelope_locked()

    def _envelope_locked(self) -> dict[str, Any]:
        """Build the on-disk envelope. Caller must hold ``self.lock``.

        The one place the envelope's shape is written. ``envelope()`` takes
        the lock and ``_persist_locked()`` already holds it, so both need the
        shape but only one may acquire -- hence the locked-context split
        rather than one calling the other.
        """
        return {
            "schema_version": MUTATION_STATE_SCHEMA_VERSION,
            "mutations": self._serialized_locked(),
        }

    def _persist_locked(self) -> None:
        """Write the overlay to disk. Caller must hold ``self.lock``.

        Called at the end of every mutator's locked block, so a mutator that
        records an event after its own commit writes more than once per
        logical mutation: ``record_event`` re-enters the RLock and persists
        again. ``put_resource``, ``delete_resource``, and ``delete_pod`` all
        do this -- stated as the rule rather than as a list, because the list
        is what drifts. Every write is atomic, so a crash between them leaves
        a valid file that is merely missing the event -- never a torn one.
        """
        if self.persist_path is None:
            return
        # Publish through the shared atomic writer, not open(path, "w"), so a
        # concurrent reader or a restart never sees a partial file.
        _atomic_write_text(
            self.persist_path, json.dumps(self._envelope_locked(), indent=2) + "\n"
        )

    def _commit_locked(self) -> None:
        """Mark one overlay commit. Caller must hold ``self.lock``.

        Every mutator ends its locked block here rather than bumping
        ``version`` inline, so the version bump and the persistence write
        cannot drift apart. ``tests/test_server_mutation_persistence.py``
        pins that: a new mutator that bumps ``version`` by hand fails the
        source guard.
        """
        self.version += 1
        self._persist_locked()

    def attach_persistence(self, path: Path | None) -> None:
        """Enable persistence and write the current overlay immediately."""
        with self.lock:
            self.persist_path = path
            self._persist_locked()

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

    def record_event(
        self,
        event_type: str,
        reason: str,
        obj: str,
        message: str,
        now: _dt.datetime,
        *,
        namespace: str = DEFAULT_NAMESPACE,
    ) -> None:
        with self.lock:
            timestamp = _format_dt(now)
            namespace = str(namespace or DEFAULT_NAMESPACE)
            for event in self.extra_events:
                if (
                    event.get("type") == event_type
                    and event.get("reason") == reason
                    and event.get("namespace", DEFAULT_NAMESPACE) == namespace
                    and event.get("object") == obj
                    and event.get("message") == message
                ):
                    event["last_seen"] = timestamp
                    event["count"] = int(event.get("count", 1)) + 1
                    self._commit_locked()
                    return
            self.extra_events.append({
                "first_seen": timestamp,
                "last_seen": timestamp,
                "type": event_type,
                "reason": reason,
                "namespace": namespace,
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
            self._commit_locked()

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
            # `self.version + 2` is the post-commit version plus one -- the
            # same value the pre-persistence code computed as
            # `self.version + 1` after bumping. Ordered this way so the
            # commit (and its disk write) is the last thing in the locked
            # block and cannot publish a stale `resource_version`.
            mutation.resource_version = max(mutation.resource_version + 1, self.version + 2)
            self._commit_locked()
            return mutation

    def delete_pod(self, pod_name: str, *, now: _dt.datetime) -> None:
        component = _component_from_pod_name(pod_name)
        # One logical mutation, so hold the RLock across all three parts and
        # commit the set itself. Uncommitted, it reached disk only via
        # whatever ran next: a failed write in `set_workload` left the
        # deletion in memory alone, resurrecting the pod on restart, and a
        # reader in the gap saw a deleted pod at an unbumped `version`.
        with self.lock:
            self.deleted_pods.add(pod_name)
            self._commit_locked()
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
            self._commit_locked()
        self.record_event(
            "Normal",
            "Configured",
            f"{_resource_prefix(kind)}/{name}",
            f"{_resource_prefix(kind)} {name} configured in simulator state",
            now,
            namespace=namespace,
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
            self._commit_locked()
        self.record_event(
            "Normal",
            "Deleted",
            f"{_resource_prefix(kind)}/{name}",
            f"{_resource_prefix(kind)} {name} deleted from simulator state",
            now,
            namespace=namespace,
        )

    def reset(self) -> None:
        with self.lock:
            self.workloads.clear()
            self.deleted_pods.clear()
            self.deleted_resources.clear()
            self.created_resources.clear()
            self.extra_events.clear()
            self.release = HelmReleaseMutation()
            self._commit_locked()

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
            self._commit_locked()

    def set_release_values(self, values: dict[str, str], *, now: _dt.datetime) -> None:
        with self.lock:
            self.release.values.update(values)
            self.release.updated_at = _format_dt(now)
            self._commit_locked()

    def replace_release_values(self, values: dict[str, str], *, now: _dt.datetime) -> None:
        with self.lock:
            self.release.values = dict(values)
            self.release.updated_at = _format_dt(now)
            self._commit_locked()

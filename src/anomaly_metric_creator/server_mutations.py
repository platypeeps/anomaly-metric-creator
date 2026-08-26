"""Mutable simulator overlay state for server mode."""

from __future__ import annotations

import datetime as _dt
import json
import sys
import threading
from collections.abc import Callable
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
# The envelope wrapping the overlay. Named once, so the writer and the
# downgrade check cannot disagree about what a valid file's top level holds;
# `test_envelope_keys_match_the_declared_set` pins the two together.
_PERSISTED_ENVELOPE_KEYS = frozenset({"schema_version", "mutations"})

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


PERSIST_ERROR_PREFIX = "--persist-mutations "
"""Marker every persisted-overlay refusal carries.

`serve_main` converts these into an operator-facing `SystemExit` and must let
every other `ValueError` through unchanged, so it matches on this rather than
assuming the loader is the only thing under `build_state()` that can raise.
Named here, next to the function that writes it, so the producer and the
matcher cannot drift.
"""


def _persist_error(path: Path, detail: str) -> ValueError:
    """Build the shared --persist-mutations diagnostic, always naming the file."""
    return ValueError(f"{PERSIST_ERROR_PREFIX}{path}: {detail}")


def _require_mapping(path: Path, value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _persist_error(path, f"{what} must be a JSON object, got {type(value).__name__}")
    return value


def _require_sequence(path: Path, value: Any, what: str) -> list[Any]:
    """Require a JSON array, so a wrong type refuses instead of half-reading.

    Every wrong type here is *iterable*, which is what makes the unguarded
    form dangerous rather than merely wrong: a dict iterates its keys and a
    string iterates its characters, so a malformed file would be accepted
    and silently mean something else. Name the offending type instead.
    """
    if not isinstance(value, list):
        raise _persist_error(path, f"{what} must be a JSON array, got {type(value).__name__}")
    return value


def _require_string_sequence(path: Path, value: Any, what: str) -> list[str]:
    """Require a JSON array whose elements are all strings.

    ``_require_sequence`` alone stops one level short. The container is
    checked but its elements are not, so a mixed-type array -- valid JSON,
    like ``["pod-a", 1]`` -- reaches ``sorted()`` and raises ``TypeError``
    comparing an ``int`` to a ``str``. That escapes the ``ValueError``
    refusal that names the file, which is the entire operator-facing
    contract. Validate before sorting, not after.
    """
    items = _require_sequence(path, value, what)
    for index, item in enumerate(items):
        if not isinstance(item, str):
            raise _persist_error(
                path, f"{what}[{index}] must be a string, got {type(item).__name__}"
            )
    return items


def _require_version(path: Path, value: Any) -> int:
    """Require a plain non-negative integer version.

    ``int()`` would coerce rather than validate -- ``True`` to 1, ``3.9`` to
    3, ``"5"`` to 5 -- and ``bool`` is a subclass of ``int``, so the isinstance
    check has to exclude it explicitly. The overlay file is an untrusted
    read-back boundary, so take the value only when it already is one.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise _persist_error(
            path, f"mutations.version must be an integer, got {type(value).__name__}"
        )
    if value < 0:
        raise _persist_error(path, f"mutations.version must not be negative, got {value}")
    return value


def _arm_persistence(mutations: SimulationMutations, path: Path) -> None:
    """Arm persistence, converting a failed first write into the refusal.

    Arming writes immediately, and that write can fail for reasons that have
    nothing to do with the file's contents -- an unwritable directory, a
    missing parent, a failed fsync. ``serve_main`` refuses on ``ValueError``
    alone, so convert here rather than widen that catch: it keeps "the loader
    raises ValueError naming the file" true for every startup failure, while
    a write that fails later, mid serve, still surfaces as the ``OSError`` it
    is instead of being mistaken for a malformed file.

    Both load paths need this. The missing-file first run is if anything the
    likelier failure -- ``--persist-mutations /no/such/dir/mutations.json``
    reaches it with nothing to hydrate and fails on the very first write.
    """
    try:
        mutations.attach_persistence(path)
    except OSError as exc:
        raise _persist_error(path, f"overlay could not be written ({exc})") from exc


def _hydrate_workloads(
    path: Path, raw: Any, known_components: frozenset[str], dropped: list[str]
) -> dict[str, WorkloadMutation]:
    mapping = _require_mapping(path, raw, "mutations.workloads")
    allowed = {f.name for f in fields(WorkloadMutation)}
    workloads: dict[str, WorkloadMutation] = {}
    for component, body in sorted(mapping.items()):
        if component not in known_components:
            dropped.append(f"workload overlay for unknown component '{component}'")
            continue
        body = _require_mapping(path, body, f"mutations.workloads['{component}']")
        unknown = set(body) - allowed
        if unknown:
            raise _persist_error(
                path,
                f"mutations.workloads['{component}'] has unknown field(s) "
                f"{', '.join(sorted(unknown))}",
            )
        _require_field_types(
            path, f"mutations.workloads['{component}']", WorkloadMutation, body
        )
        workloads[component] = WorkloadMutation(**body)
    return workloads


def _is_int(value: Any) -> bool:
    """`bool` is an `int` subclass, so `True` would otherwise pass as 1."""
    return isinstance(value, int) and not isinstance(value, bool)


# Field-type checks for the two `**body`-constructed dataclasses, keyed on the
# annotation's *source text* -- `from __future__ import annotations` makes
# every annotation a string, so this derives from the declaration rather than
# duplicating it. A new field is checked the moment it is declared, and an
# annotation form this table does not know refuses instead of passing the
# value through: an unchecked boundary field is the failure this prevents.
_FIELD_TYPE_CHECKS: dict[str, tuple[Callable[[Any], bool], str]] = {
    "int": (_is_int, "an integer"),
    "str": (lambda value: isinstance(value, str), "a string"),
    "bool": (lambda value: isinstance(value, bool), "a boolean"),
    "int | None": (lambda value: value is None or _is_int(value), "an integer or null"),
    "list[dict[str, Any]] | None": (
        lambda value: value is None
        or (isinstance(value, list) and all(isinstance(item, dict) for item in value)),
        "an array of JSON objects or null",
    ),
    "dict[str, str]": (
        lambda value: isinstance(value, dict)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()),
        "an object with string keys and string values",
    ),
}


def _require_field_types(path: Path, what: str, cls: type, body: dict[str, Any]) -> None:
    """Refuse a persisted field whose JSON type its dataclass cannot hold.

    Rejecting unknown field *names* stops one level short: `**body`
    construction happily accepts `{"replicas": "3"}` or `{"values": []}`, and
    the crash then lands in `server_ops` -- `min(replicas, ...)`,
    `values.items()` -- far from the file that caused it, as a traceback
    rather than the path-naming refusal the overlay boundary promises.
    """
    for spec in fields(cls):
        if spec.name not in body:
            continue
        if spec.type not in _FIELD_TYPE_CHECKS:
            raise _persist_error(
                path,
                f"{what}.{spec.name} is declared {spec.type!r}, which this "
                "loader cannot validate",
            )
        predicate, description = _FIELD_TYPE_CHECKS[spec.type]
        value = body[spec.name]
        if not predicate(value):
            raise _persist_error(
                path,
                f"{what}.{spec.name} must be {description}, got {type(value).__name__}",
            )


def _hydrate_release(path: Path, raw: Any) -> HelmReleaseMutation:
    body = _require_mapping(path, raw, "mutations.release")
    allowed = {f.name for f in fields(HelmReleaseMutation)}
    unknown = set(body) - allowed
    if unknown:
        raise _persist_error(
            path, f"mutations.release has unknown field(s) {', '.join(sorted(unknown))}"
        )
    _require_field_types(path, "mutations.release", HelmReleaseMutation, body)
    return HelmReleaseMutation(**body)


def load_persisted_mutations(
    path: Path,
    *,
    known_components: frozenset[str],
    extra_event_limit: int = DEFAULT_TRACE_LIMIT,
) -> SimulationMutations:
    """Rebuild the overlay from ``path`` and arm it to keep persisting there.

    A missing file is the normal first-run case and yields an empty overlay.
    Anything present but unreadable -- corrupt JSON, an unknown
    ``schema_version``, a field this build does not declare -- raises
    ``ValueError`` naming the file rather than half-hydrating: a partially
    restored overlay would render a snapshot that never existed.

    Entries keyed by a component this run does not have are *dropped* with a
    stderr WARNING naming each one, not refused. Restart continuity assumes a
    compatible run; a ghost component would put the Kubernetes facade out of
    parity with the generated data, and a hard failure would strand an
    operator who merely narrowed ``--components``.
    """
    mutations = SimulationMutations(extra_event_limit=extra_event_limit)
    if not path.exists():
        _arm_persistence(mutations, path)
        return mutations

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _persist_error(path, f"file is not valid JSON ({exc})") from exc
    except OSError as exc:
        raise _persist_error(path, f"file could not be read ({exc})") from exc

    payload = _require_mapping(path, payload, "persisted state")
    # Both envelope keys must be *present*, not merely not-unexpected. The
    # unknown-key check below is one-directional, so a truncated or
    # hand-edited file missing `mutations` would otherwise default to `{}`
    # and restore an empty overlay in silence -- the operator would see a
    # server that started clean and conclude the mutations were never made.
    # Checked before `schema_version`, whose own `.get()` would report a
    # missing envelope as `schema_version None`, naming the wrong problem.
    missing_envelope = _PERSISTED_ENVELOPE_KEYS - set(payload)
    if missing_envelope:
        raise _persist_error(
            path,
            f"persisted state is missing key(s) {', '.join(sorted(missing_envelope))}",
        )
    version = payload.get("schema_version")
    if version != MUTATION_STATE_SCHEMA_VERSION:
        raise _persist_error(
            path,
            f"schema_version {version!r} is not supported by this build "
            f"(expected {MUTATION_STATE_SCHEMA_VERSION}). Delete the file to "
            "start from a clean overlay.",
        )
    # Refuse an unknown *envelope* key for the same reason an unknown
    # `mutations` key is refused: a newer build's field would otherwise be
    # dropped in silence on downgrade, and the operator would see a restored
    # overlay that quietly means less than the file says. Checked after
    # `schema_version`, so a file from a future build reports the version
    # mismatch -- which tells the operator what to do -- rather than leading
    # with whichever new key happens to sort first.
    unknown_envelope = set(payload) - _PERSISTED_ENVELOPE_KEYS
    if unknown_envelope:
        raise _persist_error(
            path,
            f"persisted state has unknown key(s) {', '.join(sorted(unknown_envelope))}",
        )
    state = _require_mapping(path, payload["mutations"], "mutations")
    unknown = set(state) - _PERSISTED_MUTATION_FIELDS
    if unknown:
        raise _persist_error(
            path, f"mutations has unknown key(s) {', '.join(sorted(unknown))}"
        )

    dropped: list[str] = []
    mutations.workloads = _hydrate_workloads(
        path, state.get("workloads", {}), known_components, dropped
    )
    for pod_name in sorted(
        _require_string_sequence(
            path, state.get("deleted_pods", []), "mutations.deleted_pods"
        )
    ):
        if _component_from_pod_name(pod_name) not in known_components:
            dropped.append(f"deleted pod '{pod_name}' of unknown component")
            continue
        mutations.deleted_pods.add(pod_name)
    for kind, names in sorted(
        _require_mapping(path, state.get("deleted_resources", {}), "mutations.deleted_resources").items()
    ):
        # `deleted_resources` is declared `dict[str, set[str]]`; a non-string
        # element would satisfy the annotation nowhere and surface later as a
        # crash while sorting or serializing, far from the file that caused
        # it. Refuse at the boundary instead.
        mutations.deleted_resources[kind] = set(
            _require_string_sequence(path, names, f"mutations.deleted_resources['{kind}']")
        )
    for kind, entries in sorted(
        _require_mapping(path, state.get("created_resources", {}), "mutations.created_resources").items()
    ):
        mutations.created_resources[kind] = {
            key: dict(_require_mapping(path, body, f"mutations.created_resources['{kind}']['{key}']"))
            for key, body in sorted(_require_mapping(path, entries, f"mutations.created_resources['{kind}']").items())
        }
    mutations.extra_events = [
        dict(_require_mapping(path, event, "mutations.extra_events[]"))
        for event in _require_sequence(
            path, state.get("extra_events", []), "mutations.extra_events"
        )
    ]
    mutations.release = _hydrate_release(path, state.get("release", {}))
    mutations.version = _require_version(path, state.get("version", 0))

    for note in dropped:
        print(f"WARNING: --persist-mutations {path}: dropped {note}", file=sys.stderr)

    # Arm persistence last, so the immediate write records the post-drop
    # overlay -- the dropped entries do not survive a second restart.
    _arm_persistence(mutations, path)
    return mutations

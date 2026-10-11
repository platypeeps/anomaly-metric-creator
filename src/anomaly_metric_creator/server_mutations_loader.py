"""Read the persisted mutation overlay back for ``--persist-mutations``.

A one-way leaf: it imports the overlay dataclasses from ``server_mutations``,
and ``server_mutations`` never imports it back. The file it reads is an
untrusted read-back boundary, so every check here refuses with the path named
rather than half-hydrating.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path
from typing import Any

from .server_mutations import (
    _PERSISTED_MUTATION_FIELDS,
    MUTATION_STATE_SCHEMA_VERSION,
    HelmReleaseMutation,
    SimulationMutations,
    WorkloadMutation,
    _component_from_pod_name,
)
from .server_traces import DEFAULT_TRACE_LIMIT


# The envelope wrapping the overlay. Named once, so the writer and the
# downgrade check cannot disagree about what a valid file's top level holds;
# `test_envelope_keys_match_the_declared_set` pins the two together.
_PERSISTED_ENVELOPE_KEYS = frozenset({"schema_version", "mutations"})


# Marker every persisted-overlay refusal carries. `serve_main` converts these
# into an operator-facing `SystemExit` and must let every other `ValueError`
# through unchanged, so it matches on this rather than assuming the loader is
# the only thing under `build_state()` that can raise. Named here, next to the
# function that writes it, so the producer and the matcher cannot drift. A
# comment, not a bare string literal below the assignment: that form is an
# attribute-docstring convention some tools read and others treat as a useless
# expression, which is ambiguity this constant does not need.
PERSIST_ERROR_PREFIX = "--persist-mutations "


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
# annotation missing from this table refuses instead of passing the value
# through: an unchecked boundary field is the failure this prevents.
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
    # Checked before `schema_version`, so a missing envelope is not reported
    # as `schema_version None`, naming the wrong problem.
    missing_envelope = _PERSISTED_ENVELOPE_KEYS - set(payload)
    if missing_envelope:
        raise _persist_error(
            path,
            f"persisted state is missing key(s) {', '.join(sorted(missing_envelope))}",
        )
    # `True` and `1.0` both compare equal to 1, so `!=` alone accepts them.
    version = payload["schema_version"]
    if not _is_int(version) or version != MUTATION_STATE_SCHEMA_VERSION:
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
    # The writer emits every field, so a missing one means a truncated or
    # hand-edited file; defaulting it would restore less than the file held.
    missing = _PERSISTED_MUTATION_FIELDS - set(state)
    if missing:
        raise _persist_error(
            path, f"mutations is missing key(s) {', '.join(sorted(missing))}"
        )

    dropped: list[str] = []
    mutations.workloads = _hydrate_workloads(
        path, state["workloads"], known_components, dropped
    )
    for pod_name in sorted(
        _require_string_sequence(path, state["deleted_pods"], "mutations.deleted_pods")
    ):
        if _component_from_pod_name(pod_name) not in known_components:
            dropped.append(f"deleted pod '{pod_name}' of unknown component")
            continue
        mutations.deleted_pods.add(pod_name)
    for kind, names in sorted(
        _require_mapping(path, state["deleted_resources"], "mutations.deleted_resources").items()
    ):
        # `deleted_resources` is declared `dict[str, set[str]]`; a non-string
        # element would satisfy the annotation nowhere and surface later as a
        # crash while sorting or serializing, far from the file that caused
        # it. Refuse at the boundary instead.
        mutations.deleted_resources[kind] = set(
            _require_string_sequence(path, names, f"mutations.deleted_resources['{kind}']")
        )
    for kind, entries in sorted(
        _require_mapping(path, state["created_resources"], "mutations.created_resources").items()
    ):
        mutations.created_resources[kind] = {
            key: dict(_require_mapping(path, body, f"mutations.created_resources['{kind}']['{key}']"))
            for key, body in sorted(_require_mapping(path, entries, f"mutations.created_resources['{kind}']").items())
        }
    events = [
        dict(_require_mapping(path, event, "mutations.extra_events[]"))
        for event in _require_sequence(path, state["extra_events"], "mutations.extra_events")
    ]
    # The limit is this run's --debug-ring-size, so a file written under a
    # larger ring keeps only its newest events, as `record_event` would.
    limit = max(mutations.extra_event_limit, 0)
    mutations.extra_events = events[-limit:] if limit else []
    mutations.release = _hydrate_release(path, state["release"])
    mutations.version = _require_version(path, state["version"])

    for note in dropped:
        print(f"WARNING: --persist-mutations {path}: dropped {note}", file=sys.stderr)

    # Arm persistence last, so the immediate write records the post-drop
    # overlay -- the dropped entries do not survive a second restart.
    _arm_persistence(mutations, path)
    return mutations

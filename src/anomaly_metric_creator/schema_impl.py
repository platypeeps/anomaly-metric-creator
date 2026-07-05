"""Schema document writer helpers.

Extracted from ``legacy.py`` (decomposition step 6). ``legacy.py`` configures
live topology access and re-imports every moved name so the historic
``legacy.<name>`` surface remains stable.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from .artifacts import _atomic_write_text
from .csv_layout import (
    _INSTANCE_DIMENSION_FIELDS,
    _is_anonymous_instance_list,
)

_get_topology: Callable[[], dict[str, list[object]]] | None = None


def _configure_schema_runtime(
    *, get_topology: Callable[[], dict[str, list[object]]]
) -> None:
    """Wire live registry access from ``legacy.py`` without importing it."""
    global _get_topology
    _get_topology = get_topology


def _live_topology() -> dict[str, list[object]]:
    if _get_topology is None:
        raise RuntimeError("schema_impl runtime is not configured")
    return _get_topology()


SCHEMA_DOCUMENT_VERSION = 2


def _metric_spec_to_schema_entry(spec: "MetricSpec") -> dict:
    """Return the schema.json entry for one ``MetricSpec``.

    Schema metadata is emitted verbatim with stable key order. ``None`` values
    are preserved (rather than dropped) so consumers can distinguish "field
    explicitly declared unbounded" from "field absent due to old schema".
    """
    return {
        "name": spec.name,
        "unit": spec.unit,
        "semantic_type": spec.semantic_type,
        "dtype": spec.dtype,
        "min_value": spec.min_value,
        "max_value": spec.max_value,
        "derivation": spec.derivation,
    }


def _saturation_params_to_schema_entry(
    sat: "SaturationParams | None",
) -> dict | None:
    """Return the schema.json entry for one ``SaturationParams``.

    Returns ``None`` when the edge has no saturation. Keys are emitted in
    a stable order so ``sort_keys=True`` produces byte-deterministic
    JSON.
    """
    if sat is None:
        return None
    return {
        "midpoint": sat.midpoint,
        "steepness": sat.steepness,
        "latency_gain": sat.latency_gain,
        "error_gain": sat.error_gain,
    }


def _edge_to_schema_entry(edge: "Edge") -> dict:
    """Return the schema.json entry for one ``Edge``.

    Constant-weight edges serialize their numeric weight verbatim;
    callable-weight edges serialize the literal string ``"callable"``
    (full reproducibility of the per-row weight is a code concern — the
    schema only declares that the coupling exists).
    """
    weight: float | str
    if callable(edge.weight):
        weight = "callable"
    else:
        weight = edge.weight
    return {
        "target": edge.target,
        "weight": weight,
        "saturation": _saturation_params_to_schema_entry(edge.saturation),
        "correlation_threshold": edge.correlation_threshold,
    }


def _component_dimensions_schema_entry(
    instances: list["Instance"] | None,
) -> dict | None:
    """Return the ``schema.json`` ``dimensions`` entry for a component's
    instance list, or ``None`` for the dimensionless default.

    Mirrors the long-form per-component CSV writer's branch predicate
    (``_is_anonymous_instance_list``): any non-anonymous instance list —
    whether ``--instances-per-component N>1`` fan-out or
    ``--instance-config`` with a non-default declaration — produces
    dim-aware CSV output and therefore declares ``dimensions`` in the
    schema. The single-anonymous-``Instance()`` default produces
    dimensionless output and omits the block so the v1 (default)
    ``schema.json`` stays byte-identical to the dimensionless baseline.

    The ``axes`` list is the sorted subset of
    ``_INSTANCE_DIMENSION_FIELDS`` (i.e. ``_INSTANCE_DIMENSION_COLUMNS``
    minus the leading ``id`` slot — ``id`` identifies an instance, it is
    not a dimension to slice on) whose value is non-``None`` on at least
    one instance in the list. ``cardinality`` is ``len(instances)``.
    Both keys are always present together so the validator can read them
    in lockstep. ``axes`` is allowed to be empty when the schema still
    declares dimensions: that is the shape produced by an id-only
    non-anonymous instance list (for example ``[Instance(id="i0"),
    Instance(id="i1")]``) with no slicable dimension yet. The schema still
    declares the long-form CSV layout under that shape because the
    per-component CSV carries the full ``id, host, pod, az, region,
    tenant`` prefix block for every non-anonymous instance list,
    regardless of which dimension columns are populated.
    """
    if instances is None or _is_anonymous_instance_list(instances):
        return None
    axes = sorted(
        {
            field
            for inst in instances
            for field in _INSTANCE_DIMENSION_FIELDS
            if getattr(inst, field) is not None
        }
    )
    return {"axes": axes, "cardinality": len(instances)}


def _serialize_topology(
    components: list[str],
) -> dict[str, list[dict]]:
    """Return the ``schema.json`` ``topology`` section for the live ``TOPOLOGY``.

    The output is keyed by source component and contains only edges whose
    *source and target both appear in* ``components``; a run that drops a
    component via ``--components`` does not couple to it, so the snapshot
    must reflect the actual coupling graph the validator should check. The
    surviving source keys are restricted to ``TOPOLOGY``'s declared sources
    (sources with no surviving outgoing edges in the filtered graph are
    omitted to keep the section minimal), and each source's edge list is
    sorted by target name for byte-deterministic output (top-level keys
    are already byte-sorted via ``json.dumps(sort_keys=True)``).
    """
    components_set = set(components)
    topology: dict[str, list[dict]] = {}
    for source, edges in _live_topology().items():
        if source not in components_set:
            continue
        kept = [
            _edge_to_schema_entry(edge)
            for edge in edges
            if edge.target in components_set
        ]
        if not kept:
            continue
        kept.sort(key=lambda entry: entry["target"])
        topology[source] = kept
    return topology


def write_schema_json(
    output_path: Path,
    *,
    components: list[str],
    effective_specs: dict[str, list["MetricSpec"]],
    metadata: dict,
    emitted_files: list[str],
    instances_by_component: dict[str, list["Instance"]] | None = None,
) -> None:
    """Write a declarative ``schema.json`` describing the current run's artifacts.

    The document is the single source of truth the ``validate`` subcommand consumes
    to check the run after the fact. It captures five slices of information:

    - ``schema_version`` — integer schema-document version (see
      ``SCHEMA_DOCUMENT_VERSION``).
    - ``metadata`` — run-level parameters (timestamp anchor, duration, drop
      rate, scenario set, seed, ...) needed to reconstruct the timeline and
      row-count expectations from the artifacts on disk.
    - ``components`` — per-component metric metadata in MetricSpec column
      order, so the validator can check ``dtype`` / ``min_value`` /
      ``max_value`` / ``semantic_type`` / ``derivation`` cell-by-cell against
      the per-component CSV. Each per-component payload also carries an
      optional ``dimensions`` block (phase 8) declaring the
      instance topology's axes + cardinality when the per-component CSV
      is dim-aware (``--instances-per-component N>1`` fan-out or a non-
      default ``--instance-config`` entry); the block is omitted in the
      default single-anonymous-``Instance()`` path so the v1 schema bytes
      stay byte-identical to the dimensionless baseline.
    - ``files`` — sorted list of artifact filenames the run was supposed to
      write, so the validator can flag missing or extra files.
    - ``topology`` (phase 7) — the directed coupling graph
      restricted to the active component set: ``{source:
      [{target, weight, saturation, correlation_threshold}, ...]}``.
      Callable weights serialize as the literal string ``"callable"``;
      ``saturation`` is either a
      ``{midpoint, steepness, latency_gain, error_gain}`` dict or
      ``null``; ``correlation_threshold`` is either a float in
      ``(-1, 1]`` (per-edge override) or ``null`` (fall back to
      ``_TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD``). The validator reads
      this to run ``_validate_topology_coupling`` under
      ``metadata.topology_mode == "realistic"``.

    ``instances_by_component`` is the live per-run instance map
    (``RunContext.instances``) restricted to the schema's components.
    A missing entry, or the single-anonymous-``Instance()`` default,
    omits the per-component ``dimensions`` block.

    The output is byte-deterministic: ``json.dumps`` with ``sort_keys=True``,
    fixed indent, ``ensure_ascii=False``, and a trailing newline. The
    per-component ``metrics`` list intentionally preserves MetricSpec column
    order (not sorted) so the validator can zip it against CSV header columns
    in one pass. The ``topology`` section sorts each source's edge list by
    target name for stable output independent of declaration order.
    """
    instances_by_component = instances_by_component or {}
    component_payload = {}
    for component in components:
        specs = effective_specs.get(component, [])
        payload = {
            "csv_filename": f"{component}.csv",
            "metrics": [_metric_spec_to_schema_entry(spec) for spec in specs],
        }
        dimensions = _component_dimensions_schema_entry(
            instances_by_component.get(component)
        )
        if dimensions is not None:
            payload["dimensions"] = dimensions
        component_payload[component] = payload

    document = {
        "schema_version": SCHEMA_DOCUMENT_VERSION,
        "metadata": metadata,
        "files": sorted(emitted_files),
        "components": component_payload,
        "topology": _serialize_topology(components),
    }

    # ``sort_keys=True`` gives byte-stable top-level ordering. Nested lists
    # (metrics, files, scenarios) keep their declared order — they are sorted
    # by the caller where determinism matters (files, scenarios) and left in
    # MetricSpec column order where the order carries meaning (metrics).
    _atomic_write_text(
        output_path,
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )

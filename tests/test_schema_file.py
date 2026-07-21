"""Tests for the declarative ``schema.json`` artifact.

Covers:
- ``--emit`` accepts the ``schema`` token, rejects bad combos.
- The file is written only when opted in, absent by default.
- Byte-determinism, locked SHA-256 golden hashes at 1d and 7d.
- ``--components`` / ``--metrics-per-component`` filter passthrough.
- Document structure: ``schema_version``, ``metadata``, ``files``,
  ``components`` block with per-MetricSpec metadata in column order.
- ``_pre_clean_output_dir`` removes a stale ``schema.json`` when ``schema``
  is dropped from the next run's --emit selection.
- the ``combine`` subcommand does NOT regenerate ``schema.json``.
"""
import csv
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import SCRIPT_PATH, run_capture, sha256_path


# Short-run helper so most tests stay under a couple of seconds.
SHORT_RUN_ARGS = ("--interval-seconds", "60")


# Locked SHA-256 golden hashes for ``schema.json`` at the default --seed (42)
# and the default scenario / signal-level / metrics-per-component knobs at
# --duration-days 1 and 7. Re-locked for phase 7 (schema document
# version bumped from 1 to 2, topology section added, metadata gained
# ``topology_mode``). Protects against silent drift in:
# - the MetricSpec schema metadata (unit/semantic_type/min/max/dtype/derivation),
# - the active-scenario / component list,
# - the run-level metadata (duration, interval, drop_rate, seed,
#   topology_mode, ...),
# - the ``files`` registry,
# - the new ``topology`` block (source -> [{target, weight, saturation}, ...]).
SCHEMA_ONE_DAY_HASH = (
    "6b79531d611755bd0df5bf14cca2244853d8339602d5703828534d4666b92aec"
)
SCHEMA_SEVEN_DAY_HASH = (
    "779936a803989cd142de2438d6123fe63904f458bace9f7c1efa0b274c28508c"
)




# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------
@pytest.fixture(scope="module")
def one_day_schema_run(amc, tmp_path_factory):
    # Explicit 1s cadence preserves the full-resolution SCHEMA_ONE_DAY_HASH.
    out = tmp_path_factory.mktemp("ver139_one_day_schema")
    return run_capture(
        amc, out, days=1, interval_seconds=1.0,
        extra_args=["--emit", "metrics,schema"],
    )


@pytest.fixture(scope="module")
def seven_day_schema_run(amc, tmp_path_factory):
    """Generate the 7-day schema lock at a cheap cadence.

    The sole consumer reads ``schema.json``; it never opens the generated
    metric CSVs. This fixture regenerates rather than deriving from
    ``seven_day_run``: schema.json's ``files`` section is coupled to the exact
    ``--emit metrics,schema`` selection, which ``seven_day_run``
    (``metrics,logs,traces``) does not match. The 60s cadence preserves the
    7-day duration/cardinality contract without generating unread 1s CSVs.
    """
    out = tmp_path_factory.mktemp("ver139_seven_day_schema")
    return run_capture(
        amc, out, days=7, interval_seconds=60.0,
        extra_args=["--emit", "metrics,schema"],
    )


def _load_schema(out_dir: Path) -> dict:
    return json.loads((out_dir / "schema.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------
# parse_args validation
# ------------------------------------------------------------------
def test_emit_accepts_schema_token(amc, tmp_path):
    args = amc.parse_args([
        "--output-dir", str(tmp_path),
        "--duration-days", "1",
        "--emit", "metrics,schema",
    ])
    assert "schema" in args.emit_selection
    assert "metrics" in args.emit_selection


def test_emit_schema_standalone_allowed(amc, tmp_path):
    """Unlike ``gauges``, ``schema`` does not require ``metrics`` — the
    metric metadata catalog is static and still useful documentation when
    no per-component CSVs are emitted."""
    args = amc.parse_args([
        "--output-dir", str(tmp_path),
        "--duration-days", "1",
        "--emit", "schema",
    ])
    assert "schema" in args.emit_selection


def test_emit_help_advertises_schema(amc):
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--help"],
        capture_output=True, text=True,
    )
    assert "schema" in result.stdout


def test_emit_rejects_unknown_token_lists_schema(capsys, amc, tmp_path):
    """The validator error message should advertise the new token alongside
    the existing ones so callers can discover ``schema`` from a typo."""
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--output-dir", str(tmp_path),
            "--duration-days", "1",
            "--emit", "metrics,bogus",
        ])
    err = capsys.readouterr().err
    assert "schema" in err


# ------------------------------------------------------------------
# File presence
# ------------------------------------------------------------------
def test_schema_json_written_when_opted_in(one_day_schema_run):
    path = one_day_schema_run.out_dir / "schema.json"
    assert path.exists(), "schema.json must be written when 'schema' is in --emit"


def test_schema_json_absent_by_default(one_day_run_a):
    assert not (one_day_run_a.out_dir / "schema.json").exists(), (
        "default run must not write schema.json unless opted in via --emit"
    )


def test_schema_json_pre_clean_removes_stale(amc, tmp_path):
    """A run with ``schema`` selected followed by a run without it must
    delete the prior ``schema.json`` (mirrors gauges.csv pre-clean behavior)."""
    out = tmp_path / "pre_clean"
    run_capture(amc, out, days=1, interval_seconds=600,
                extra_args=["--emit", "metrics,schema"])
    assert (out / "schema.json").exists()
    run_capture(amc, out, days=1, interval_seconds=600,
                extra_args=["--emit", "metrics"])
    assert not (out / "schema.json").exists(), (
        "_pre_clean_output_dir must remove schema.json when 'schema' is "
        "dropped from --emit"
    )


# ------------------------------------------------------------------
# Document structure
# ------------------------------------------------------------------
def test_schema_has_version(one_day_schema_run, amc):
    doc = _load_schema(one_day_schema_run.out_dir)
    assert doc["schema_version"] == amc.SCHEMA_DOCUMENT_VERSION


def test_schema_metadata_captures_run_parameters(one_day_schema_run, amc):
    doc = _load_schema(one_day_schema_run.out_dir)
    meta = doc["metadata"]
    assert meta["seed"] == 42
    assert meta["duration_days"] == 1
    assert meta["interval_seconds"] == 1.0
    assert meta["total_seconds"] == amc.SECONDS_PER_DAY
    assert meta["rows_per_component"] == amc.SECONDS_PER_DAY  # interval 1.0
    assert meta["signal_level"] == "medium"
    assert meta["drop_rate"] == pytest.approx(0.0)
    assert meta["inject_dst_artifact_day"] == 0
    assert meta["start"] == amc.START.isoformat()
    assert meta["combine"] is False
    assert "metrics" in meta["emit_selection"]
    assert "schema" in meta["emit_selection"]
    assert isinstance(meta["scenarios"], list)
    assert meta["scenarios"] == sorted(meta["scenarios"]), (
        "metadata.scenarios must be sorted for byte determinism"
    )


def test_start_time_shifts_component_csv_and_schema_metadata(amc, tmp_path):
    out = tmp_path / "custom_start_time"
    run_capture(
        amc,
        out,
        days=1,
        interval_seconds=3600,
        extra_args=[
            "--start-time", "2026-06-24T12:34:56Z",
            "--components", "cacheservice",
            "--scenarios", "cache_collapse",
            "--emit", "metrics,schema",
        ],
    )

    doc = _load_schema(out)
    assert doc["metadata"]["start"] == "2026-06-24T12:34:56"

    with open(out / "cacheservice.csv", encoding="utf-8", newline="") as f:
        first_row = next(csv.DictReader(f))
    assert first_row["timestamp"] == "2026-06-24 12:34:56"


def test_schema_files_list_matches_emitted_artifacts(one_day_schema_run):
    doc = _load_schema(one_day_schema_run.out_dir)
    files = doc["files"]
    # Every per-component CSV plus the manifest plus schema.json itself.
    assert "anomalies.csv" in files
    assert "schema.json" in files
    # Per-component CSVs for every active component.
    for component in doc["metadata"]["components"]:
        assert f"{component}.csv" in files
    # Sorted for byte determinism.
    assert files == sorted(files)
    # No surprise files.
    assert "metric_report.log" not in files
    assert "metric_traces.jsonl" not in files
    assert "gauges.csv" not in files


def test_schema_components_metrics_in_metricspec_order(one_day_schema_run, amc):
    """The per-component metrics array must be in MetricSpec column order so
    the validator can zip it against CSV header columns in one pass."""
    doc = _load_schema(one_day_schema_run.out_dir)
    for component, payload in doc["components"].items():
        names = [m["name"] for m in payload["metrics"]]
        catalog = amc._resolve_effective_specs(None)[component]
        expected = [spec.name for spec in catalog]
        assert names == expected, (
            f"{component}: schema metric order {names} must match "
            f"MetricSpec order {expected}"
        )


def test_schema_components_metric_metadata_round_trip(one_day_schema_run, amc):
    """Each metric entry must carry the exact MetricSpec metadata."""
    doc = _load_schema(one_day_schema_run.out_dir)
    effective = amc._resolve_effective_specs(None)
    for component, payload in doc["components"].items():
        for i, entry in enumerate(payload["metrics"]):
            spec = effective[component][i]
            assert entry["name"] == spec.name
            assert entry["unit"] == spec.unit
            assert entry["semantic_type"] == spec.semantic_type
            assert entry["min_value"] == spec.min_value
            assert entry["max_value"] == spec.max_value
            assert entry["dtype"] == spec.dtype
            assert entry["derivation"] == spec.derivation


def test_schema_records_hit_ratio_derivation(one_day_schema_run):
    """cacheservice.hit_ratio is the canonical derived column; the schema
    must record its derivation expression so the validator can reproduce
    the check at output-validation time."""
    doc = _load_schema(one_day_schema_run.out_dir)
    cache_metrics = {m["name"]: m for m in doc["components"]["cacheservice"]["metrics"]}
    assert cache_metrics["hit_ratio"]["derivation"] == (
        "100 * cache_hits / (cache_hits + cache_misses)"
    )


# ------------------------------------------------------------------
# Determinism + locked golden hashes
# ------------------------------------------------------------------
def test_schema_byte_deterministic_same_seed(amc, tmp_path):
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    run_capture(amc, out_a, days=1, extra_args=["--emit", "metrics,schema"])
    run_capture(amc, out_b, days=1, extra_args=["--emit", "metrics,schema"])
    assert sha256_path(out_a / "schema.json") == sha256_path(out_b / "schema.json"), (
        "schema.json must be byte-identical across two identical runs"
    )


def test_schema_byte_identical_default_one_day(one_day_schema_run):
    path = one_day_schema_run.out_dir / "schema.json"
    actual = sha256_path(path)
    assert actual == SCHEMA_ONE_DAY_HASH, (
        f"schema.json drifted from locked 1-day hash. "
        f"expected={SCHEMA_ONE_DAY_HASH} actual={actual}"
    )


def test_schema_byte_identical_default_seven_day(seven_day_schema_run, amc):
    """The coarse 7-day lock retains duration and cardinality semantics."""
    path = seven_day_schema_run.out_dir / "schema.json"
    metadata = _load_schema(seven_day_schema_run.out_dir)["metadata"]
    assert metadata["total_seconds"] == 7 * amc.SECONDS_PER_DAY
    assert metadata["rows_per_component"] == 7 * amc.SECONDS_PER_DAY // 60
    actual = sha256_path(path)
    assert actual == SCHEMA_SEVEN_DAY_HASH, (
        f"schema.json drifted from locked 7-day hash. "
        f"expected={SCHEMA_SEVEN_DAY_HASH} actual={actual}"
    )


# ------------------------------------------------------------------
# Filter passthrough
# ------------------------------------------------------------------
def test_schema_respects_components(amc, tmp_path):
    pair = list(amc.COMPONENTS)[:2]
    keep = pair[0]
    drop = pair[1]
    out = tmp_path / "narrowed"
    run_capture(
        amc, out, days=1,
        interval_seconds=600,
        extra_args=[
            "--emit", "metrics,schema",
            "--components", keep,
        ],
    )
    doc = _load_schema(out)
    assert keep in doc["components"]
    assert drop not in doc["components"]
    assert doc["metadata"]["components"] == [keep]
    assert f"{keep}.csv" in doc["files"]
    assert f"{drop}.csv" not in doc["files"]


def test_schema_respects_metrics_per_component(amc, tmp_path):
    out = tmp_path / "trim"
    run_capture(
        amc, out, days=1,
        interval_seconds=600,
        extra_args=[
            "--emit", "metrics,schema",
            "--metrics-per-component", "1",
        ],
    )
    doc = _load_schema(out)
    for component, payload in doc["components"].items():
        assert len(payload["metrics"]) == 1, (
            f"--metrics-per-component=1 must trim {component} to its first "
            f"MetricSpec; schema lists {len(payload['metrics'])}"
        )
        assert payload["metrics"][0]["name"] == amc.COMPONENTS[component][0].name
    assert doc["metadata"]["metrics_per_component"] == 1


def test_schema_combine_subcommand_does_not_regenerate(amc, tmp_path):
    """The ``combine`` subcommand reads existing per-component CSVs and must NOT
    rewrite ``schema.json``; the path never runs the pre-clean step
    (which would also rewrite it). Mirrors the gauges-file invariant."""
    out = tmp_path / "combine_only"
    # First run: generate schema.json.
    run_capture(amc, out, days=1, interval_seconds=600,
                extra_args=["--emit", "metrics,schema"])
    schema_path = out / "schema.json"
    original_bytes = schema_path.read_bytes()  # resource-lint: allow
    # Now mutate the on-disk schema; the combine subcommand must leave it alone.
    schema_path.write_bytes(b'{"sentinel": "untouched"}\n')
    # Combine subcommand run.
    amc.main(["combine", str(out)])
    assert schema_path.read_bytes() == b'{"sentinel": "untouched"}\n', (  # resource-lint: allow
        "the combine subcommand must not rewrite or pre-clean schema.json"
    )
    # And the original schema bytes are recoverable by re-running normally.
    run_capture(amc, out, days=1, interval_seconds=600,
                extra_args=["--emit", "metrics,schema"])
    assert schema_path.read_bytes() == original_bytes  # resource-lint: allow


# ------------------------------------------------------------------
# DST artifact compatibility
# ------------------------------------------------------------------
def test_schema_records_dst_inject_day(amc, tmp_path):
    """When ``--inject-dst-artifact-day N`` is set, the schema must record
    it so the validator can adjust its row-count expectation."""
    out = tmp_path / "dst"
    run_capture(amc, out, days=2, interval_seconds=600,
                extra_args=["--emit", "metrics,schema",
                            "--inject-dst-artifact-day", "1"])
    doc = _load_schema(out)
    assert doc["metadata"]["inject_dst_artifact_day"] == 1


# ------------------------------------------------------------------
# Topology section (phase 7)
# ------------------------------------------------------------------
def test_schema_records_topology_mode_in_metadata(one_day_schema_run):
    """``metadata.topology_mode`` is always ``"realistic"`` since the
    phase-9 flag day removed the ``--topology-mode`` CLI flag. The
    field is kept so the validator can still short-circuit the
    coupling check on older (or hand-edited) documents that carry
    ``"independent"`` — which declared decoupled baselines by
    construction."""
    doc = _load_schema(one_day_schema_run.out_dir)
    assert doc["metadata"]["topology_mode"] == "realistic"


def test_schema_has_topology_block(one_day_schema_run):
    """The top-level ``topology`` block is the directed coupling graph
    snapshot the validator's ``_validate_topology_coupling`` consumes.

    The default 1-day run covers every component, so the snapshot must
    contain *exactly* the live ``TOPOLOGY`` source set — both missing
    and unknown keys are regressions. Asserting set equality catches
    the case where the serializer silently drops a real source (e.g.
    ``apigateway`` or ``cacheservice``) which a looser subset check
    would let through."""
    doc = _load_schema(one_day_schema_run.out_dir)
    assert "topology" in doc, (
        "phase 7 adds a top-level 'topology' section to schema.json"
    )
    topology = doc["topology"]
    assert isinstance(topology, dict)
    assert set(topology.keys()) == {
        "loadbalancer", "apigateway", "cacheservice",
    }


def test_schema_topology_edge_shape(one_day_schema_run):
    """Each edge entry carries exactly ``target``, ``weight``,
    ``saturation``, and ``correlation_threshold``. Callable weights
    serialize as the literal string ``"callable"``; ``saturation`` is
    either ``None`` or the four ``SaturationParams`` fields;
    ``correlation_threshold`` is either ``None`` (fall back to the
    module default) or a float in ``(-1, 1]``."""
    doc = _load_schema(one_day_schema_run.out_dir)
    topology = doc["topology"]

    # apigateway -> {authservice, cacheservice, database, llm_analytics}
    apigateway_edges = topology["apigateway"]
    assert {e["target"] for e in apigateway_edges} == {
        "authservice", "cacheservice", "database", "llm_analytics",
    }
    for edge in apigateway_edges:
        assert set(edge.keys()) == {
            "target", "weight", "saturation", "correlation_threshold",
        }

        assert isinstance(edge["weight"], (int, float))
        sat = edge["saturation"]
        assert sat is not None
        assert set(sat.keys()) == {
            "midpoint", "steepness", "latency_gain", "error_gain"
        }

    # cacheservice -> database has callable weight (cache-miss ratio)
    # and no saturation in v1.
    cache_edges = topology["cacheservice"]
    db_edge = next(e for e in cache_edges if e["target"] == "database")
    assert db_edge["weight"] == "callable"
    assert db_edge["saturation"] is None


def test_schema_topology_edges_sorted_by_target(one_day_schema_run):
    """Each source's edge list must be sorted by target name for
    byte-deterministic output."""
    doc = _load_schema(one_day_schema_run.out_dir)
    topology = doc["topology"]
    for source, edges in topology.items():
        targets = [e["target"] for e in edges]
        assert targets == sorted(targets), (
            f"topology[{source!r}] edges not sorted by target: {targets}"
        )


def test_schema_topology_omits_filtered_components(amc, tmp_path):
    """A run that drops a component via ``--components`` must omit edges
    whose source or target was filtered out — the validator should not
    try to correlate columns the run did not write."""
    out = tmp_path / "narrowed_topology"
    run_capture(
        amc, out, days=1,
        interval_seconds=600,
        extra_args=[
            "--emit", "metrics,schema",
            "--components", "loadbalancer,apigateway",
        ],
    )
    doc = _load_schema(out)
    topology = doc["topology"]
    # loadbalancer -> apigateway survives.
    assert "loadbalancer" in topology
    lb_edges = topology["loadbalancer"]
    assert len(lb_edges) == 1 and lb_edges[0]["target"] == "apigateway"
    # apigateway is in the run but every downstream (authservice,
    # cacheservice, database, llm_analytics) was filtered out, so the
    # apigateway source key should be absent.
    assert "apigateway" not in topology


def test_schema_topology_version_is_two(one_day_schema_run, amc):
    """Phase 7 bumps the schema-document version from 1 to 2 so
    older readers can refuse to validate a v2 doc and v2 readers reject
    stale v1 docs."""
    doc = _load_schema(one_day_schema_run.out_dir)
    assert doc["schema_version"] == 2
    assert amc.SCHEMA_DOCUMENT_VERSION == 2


# ------------------------------------------------------------------
# Dimensions block (phase 8)
# ------------------------------------------------------------------
# Locked SHA-256 golden hashes for ``schema.json`` at
# ``--instances-per-component 3`` and the default --seed / scenario set
# at --duration-days 1 and 7. Re-locked at phase 8 alongside the
# new per-component ``dimensions`` block. Protects against silent drift
# in the dim-aware schema output: the axes/cardinality block, the
# component payload order, and the metadata reflect the multi-instance
# run exactly. The schema-document version is unchanged at 2 because
# the ``dimensions`` block is purely additive (omitted entirely for
# anonymous single-instance runs), which keeps the existing
# ``SCHEMA_ONE_DAY_HASH`` / ``SCHEMA_SEVEN_DAY_HASH`` constants
# byte-identical above.
SCHEMA_N3_ONE_DAY_HASH = (
    "a5b385e419b646f960efeb0eba16418be4276f217512984b7b84298a85e1ef9f"
)
SCHEMA_N3_SEVEN_DAY_HASH = (
    "795e069ae587ab546b1f71bdcd1d6c9cde8b7523d5128fb78fb9e8843421852a"
)


@pytest.fixture(scope="module")
def one_day_schema_run_n3(n3_one_day_dataset_dir):
    """N=3 1-day run with ``metrics,schema`` --emit selection so the
    schema's dim block fires on every component. Delegates to the
    session-scoped ``n3_one_day_dataset_dir`` in ``conftest.py``
    (identical args: days=1, 1s cadence, N=3, ``metrics,schema``)
    instead of regenerating the measured 4.12s / 264 MiB-on-disk dataset for this
    module — the PR #63 module-scoped-duplicate antipattern from the
    "Test resource cost" checklist. The locked
    ``SCHEMA_N3_ONE_DAY_HASH`` holds byte-identically because the
    shared fixture uses the same --emit selection this module's fixture
    used when the hash was locked."""
    return SimpleNamespace(out_dir=n3_one_day_dataset_dir)


@pytest.fixture(scope="module")
def seven_day_schema_run_n3(n3_seven_day_dataset_dir):
    """Delegates to the session-scoped 7-day N=3 dataset — the single
    most expensive generation in the suite; see
    ``conftest.n3_seven_day_dataset_dir`` for the sharing rationale.
    ``SCHEMA_N3_SEVEN_DAY_HASH`` holds byte-identically (same args,
    same ``metrics,schema`` --emit selection)."""
    return SimpleNamespace(out_dir=n3_seven_day_dataset_dir)


def test_schema_omits_dimensions_block_for_anonymous_default(one_day_schema_run):
    """The default single-anonymous-``Instance()`` path must NOT add a
    ``dimensions`` key under any component — that's what keeps the v1
    schema bytes (and the locked SHA-256 hashes above) byte-identical
    to the pre-existing baseline."""
    doc = _load_schema(one_day_schema_run.out_dir)
    for component, payload in doc["components"].items():
        assert "dimensions" not in payload, (
            f"default N=1 anonymous run must not emit a dimensions block; "
            f"{component} has {payload.get('dimensions')!r}"
        )


def test_schema_n3_emits_dimensions_block_on_every_component(
    one_day_schema_run_n3, amc
):
    """``--instances-per-component 3`` makes every component dim-aware
    (Phase 2 fan-out sets ``id`` and ``pod`` on each instance), so the
    schema must declare ``dimensions`` on every component in the run."""
    doc = _load_schema(one_day_schema_run_n3.out_dir)
    expected_components = {c for c in amc.COMPONENTS}
    schema_components = set(doc["components"].keys())
    assert expected_components <= schema_components, (
        "the default --components=all run must cover every COMPONENTS key"
    )
    for component, payload in doc["components"].items():
        assert "dimensions" in payload, (
            f"--instances-per-component 3 must emit a dimensions block "
            f"on every component; {component} is missing one"
        )
        assert payload["dimensions"]["cardinality"] == 3
        assert payload["dimensions"]["axes"] == ["pod"], (
            f"Phase 2 fan-out only sets the 'pod' dimension on each "
            f"Instance; {component} declares axes "
            f"{payload['dimensions']['axes']!r}"
        )


def test_schema_n3_dimension_axes_excludes_id(one_day_schema_run_n3):
    """``id`` identifies an instance — it is not a dimension to slice
    on. The axes list is built from ``_INSTANCE_DIMENSION_FIELDS``
    (which excludes ``id``) so the schema never declares ``id`` as an
    axis even though every instance carries one."""
    doc = _load_schema(one_day_schema_run_n3.out_dir)
    for component, payload in doc["components"].items():
        axes = payload["dimensions"]["axes"]
        assert "id" not in axes, (
            f"{component}.dimensions.axes must exclude 'id' "
            f"(got {axes!r})"
        )


def test_schema_n3_byte_identical_one_day(one_day_schema_run_n3):
    """Locked SHA-256 hash for the default N=3 1-day schema.json so a
    silent drift in the dim-block emitter (axes ordering, cardinality
    derivation, payload key set) gets caught at test time."""
    path = one_day_schema_run_n3.out_dir / "schema.json"
    actual = sha256_path(path)
    assert actual == SCHEMA_N3_ONE_DAY_HASH, (
        f"N=3 1-day schema.json drifted from locked hash. "
        f"expected={SCHEMA_N3_ONE_DAY_HASH} actual={actual}"
    )


def test_schema_n3_byte_identical_seven_day(seven_day_schema_run_n3):
    """Locked SHA-256 hash for the default N=3 7-day schema.json — same
    protection as the 1-day case, plus catches drift introduced only at
    multi-day boundaries (e.g. a metadata field that depends on
    duration)."""
    path = seven_day_schema_run_n3.out_dir / "schema.json"
    actual = sha256_path(path)
    assert actual == SCHEMA_N3_SEVEN_DAY_HASH, (
        f"N=3 7-day schema.json drifted from locked hash. "
        f"expected={SCHEMA_N3_SEVEN_DAY_HASH} actual={actual}"
    )


def test_schema_n3_byte_deterministic(amc, tmp_path):
    """Two N=3 runs with the same seed must produce byte-identical
    schema.json — proves the dim block emission is order-independent
    even though the instance fan-out builds the list in registry
    order."""
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    run_capture(amc, out_a, days=1, interval_seconds=600,
                extra_args=["--emit", "metrics,schema",
                            "--instances-per-component", "3"])
    run_capture(amc, out_b, days=1, interval_seconds=600,
                extra_args=["--emit", "metrics,schema",
                            "--instances-per-component", "3"])
    assert sha256_path(out_a / "schema.json") == sha256_path(out_b / "schema.json")


def test_schema_dimensions_from_instance_config_multiple_axes(amc, tmp_path):
    """A non-default ``--instance-config`` declaring multiple varying
    dim fields (e.g. pod + az) must surface every populated axis in
    sorted order, with cardinality equal to the per-component list
    length. Exercises the "any non-None field on any instance" inference
    used by ``_component_dimensions_schema_entry``."""
    cfg_path = tmp_path / "instances.json"
    cfg_path.write_text(
        json.dumps({
            "components": {
                "apigateway": [
                    {"id": "i0", "pod": "pod-a", "az": "us-east-1"},
                    {"id": "i1", "pod": "pod-b", "az": "us-west-2"},
                ],
            },
        }),
        encoding="utf-8",
    )
    out = tmp_path / "run"
    run_capture(
        amc, out, days=1,
        interval_seconds=600,
        extra_args=[
            "--emit", "metrics,schema",
            "--instance-config", str(cfg_path),
            "--components", "apigateway",
        ],
    )
    doc = _load_schema(out)
    dims = doc["components"]["apigateway"]["dimensions"]
    assert dims["axes"] == ["az", "pod"], (
        f"axes must be sorted (alphabetic) and include every populated "
        f"dim field; got {dims['axes']!r}"
    )
    assert dims["cardinality"] == 2


def test_schema_n3_omits_dimensions_for_unfanned_components(amc, tmp_path):
    """A run with ``--instance-config`` covering only one component
    must omit ``dimensions`` from components left with the anonymous
    default. Establishes that the schema mirrors the per-component
    CSV layout (which itself only emits dim columns for the dimensioned
    components)."""
    cfg_path = tmp_path / "instances.json"
    cfg_path.write_text(
        json.dumps({
            "components": {
                "apigateway": [
                    {"id": "i0", "pod": "pod-0"},
                    {"id": "i1", "pod": "pod-1"},
                ],
            },
        }),
        encoding="utf-8",
    )
    out = tmp_path / "run"
    run_capture(
        amc, out, days=1,
        interval_seconds=600,
        extra_args=[
            "--emit", "metrics,schema",
            "--instance-config", str(cfg_path),
            "--components", "apigateway,cacheservice",
        ],
    )
    doc = _load_schema(out)
    assert "dimensions" in doc["components"]["apigateway"]
    assert doc["components"]["apigateway"]["dimensions"]["cardinality"] == 2
    assert "dimensions" not in doc["components"]["cacheservice"], (
        "cacheservice was not declared in --instance-config; it should "
        "fall back to the anonymous default (no dim columns in CSV, no "
        "dimensions block in schema)"
    )


def test_component_dimensions_schema_entry_anonymous_returns_none(amc):
    """``_component_dimensions_schema_entry`` returns ``None`` for the
    single-anonymous-``Instance()`` default so the schema emitter can
    just check truthiness to decide whether to attach the block.
    Mirrors ``_is_anonymous_instance_list``."""
    assert amc._component_dimensions_schema_entry([amc.Instance()]) is None
    assert amc._component_dimensions_schema_entry(None) is None


def test_component_dimensions_schema_entry_axes_dedup(amc):
    """When multiple instances populate the same axis (e.g. every
    instance has a ``pod`` value), ``axes`` lists that field once.
    Sorted output guards against insertion-order drift across runs."""
    instances = [
        amc.Instance(id="i0", pod="pod-0", host="h0"),
        amc.Instance(id="i1", pod="pod-1", host="h1"),
    ]
    entry = amc._component_dimensions_schema_entry(instances)
    assert entry == {"axes": ["host", "pod"], "cardinality": 2}


def test_component_dimensions_schema_entry_partial_axes(amc):
    """An axis populated on at least one instance but not all is still
    a dimension — the schema records its presence so the validator can
    confirm the column exists in the CSV header."""
    instances = [
        amc.Instance(id="i0", pod="pod-0", region="us-east"),
        amc.Instance(id="i1", pod="pod-1"),  # no region
    ]
    entry = amc._component_dimensions_schema_entry(instances)
    assert entry == {"axes": ["pod", "region"], "cardinality": 2}

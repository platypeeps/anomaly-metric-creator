"""Tests for the declarative ``schema.json`` artifact (VER-139).

Covers:
- ``--emit-selection`` accepts the new ``schema`` token, rejects bad combos.
- The file is written only when opted in, absent by default.
- Byte-determinism, locked SHA-256 golden hashes at 1d and 7d.
- ``--components`` / ``--metrics-per-component`` filter passthrough.
- Document structure: ``schema_version``, ``metadata``, ``files``,
  ``components`` block with per-MetricSpec metadata in column order.
- ``_pre_clean_output_dir`` removes a stale ``schema.json`` when ``schema``
  is dropped from the next run's emit-selection.
- ``--combine-only`` does NOT regenerate ``schema.json``.
"""
import csv
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import SCRIPT_PATH, run_capture


# Short-run helper so most tests stay under a couple of seconds.
SHORT_RUN_ARGS = ("--interval-seconds", "60")


# Locked SHA-256 golden hashes for ``schema.json`` at the default --seed (42)
# and the default scenario / signal-level / metrics-per-component knobs at
# --duration-days 1 and 7. Captured against the merged main commit 8dc8ec1
# plus this VER-139 patch and protect against silent drift in:
# - the MetricSpec schema metadata (unit/semantic_type/min/max/dtype/derivation),
# - the active-scenario / component list,
# - the run-level metadata (duration, interval, drop_rate, seed, ...),
# - the ``files`` registry.
SCHEMA_ONE_DAY_HASH = (
    "c372e7f725b913bd9857b34e6ff18e3e0abcafd9c02790c0f27558cb9ec677a1"
)
SCHEMA_SEVEN_DAY_HASH = (
    "ccb33cc39994f4373a03ff6268270f84ce8aa1092bba2a81bc41e061e2984968"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------
@pytest.fixture(scope="module")
def one_day_schema_run(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("ver139_one_day_schema")
    return run_capture(
        amc, out, days=1, extra_args=["--emit-selection", "metrics,schema"]
    )


@pytest.fixture(scope="module")
def seven_day_schema_run(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("ver139_seven_day_schema")
    return run_capture(
        amc, out, days=7, extra_args=["--emit-selection", "metrics,schema"]
    )


def _load_schema(out_dir: Path) -> dict:
    return json.loads((out_dir / "schema.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------
# parse_args validation
# ------------------------------------------------------------------
def test_emit_selection_accepts_schema_token(amc, tmp_path):
    args = amc.parse_args([
        "--output-dir", str(tmp_path),
        "--duration-days", "1",
        "--emit-selection", "metrics,schema",
    ])
    assert "schema" in args.emit_selection
    assert "metrics" in args.emit_selection


def test_emit_selection_schema_standalone_allowed(amc, tmp_path):
    """Unlike ``gauges``, ``schema`` does not require ``metrics`` — the
    metric metadata catalog is static and still useful documentation when
    no per-component CSVs are emitted."""
    args = amc.parse_args([
        "--output-dir", str(tmp_path),
        "--duration-days", "1",
        "--emit-selection", "schema",
    ])
    assert "schema" in args.emit_selection


def test_emit_selection_help_advertises_schema(amc):
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--help"],
        capture_output=True, text=True,
    )
    assert "schema" in result.stdout


def test_emit_selection_rejects_unknown_token_lists_schema(capsys, amc, tmp_path):
    """The validator error message should advertise the new token alongside
    the existing ones so callers can discover ``schema`` from a typo."""
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--output-dir", str(tmp_path),
            "--duration-days", "1",
            "--emit-selection", "metrics,bogus",
        ])
    err = capsys.readouterr().err
    assert "schema" in err


# ------------------------------------------------------------------
# File presence
# ------------------------------------------------------------------
def test_schema_json_written_when_opted_in(one_day_schema_run):
    path = one_day_schema_run.out_dir / "schema.json"
    assert path.exists(), "schema.json must be written when 'schema' is in --emit-selection"


def test_schema_json_absent_by_default(one_day_run_a):
    assert not (one_day_run_a.out_dir / "schema.json").exists(), (
        "default run must not write schema.json unless opted in via --emit-selection"
    )


def test_schema_json_pre_clean_removes_stale(amc, tmp_path):
    """A run with ``schema`` selected followed by a run without it must
    delete the prior ``schema.json`` (mirrors gauges.csv pre-clean behavior)."""
    out = tmp_path / "pre_clean"
    run_capture(amc, out, days=1,
                extra_args=["--emit-selection", "metrics,schema",
                            "--interval-seconds", "600"])
    assert (out / "schema.json").exists()
    run_capture(amc, out, days=1,
                extra_args=["--emit-selection", "metrics",
                            "--interval-seconds", "600"])
    assert not (out / "schema.json").exists(), (
        "_pre_clean_output_dir must remove schema.json when 'schema' is "
        "dropped from --emit-selection"
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
    assert meta["drop_rate"] == pytest.approx(0.0005)
    assert meta["inject_dst_artifact_day"] == 0
    assert meta["start"] == amc.START.isoformat()
    assert meta["combine"] is False
    assert "metrics" in meta["emit_selection"]
    assert "schema" in meta["emit_selection"]
    assert isinstance(meta["scenarios"], list)
    assert meta["scenarios"] == sorted(meta["scenarios"]), (
        "metadata.scenarios must be sorted for byte determinism"
    )


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
    run_capture(amc, out_a, days=1, extra_args=["--emit-selection", "metrics,schema"])
    run_capture(amc, out_b, days=1, extra_args=["--emit-selection", "metrics,schema"])
    assert _sha256(out_a / "schema.json") == _sha256(out_b / "schema.json"), (
        "schema.json must be byte-identical across two identical runs"
    )


def test_schema_byte_identical_default_one_day(one_day_schema_run):
    path = one_day_schema_run.out_dir / "schema.json"
    actual = _sha256(path)
    assert actual == SCHEMA_ONE_DAY_HASH, (
        f"schema.json drifted from locked 1-day hash. "
        f"expected={SCHEMA_ONE_DAY_HASH} actual={actual}"
    )


def test_schema_byte_identical_default_seven_day(seven_day_schema_run):
    path = seven_day_schema_run.out_dir / "schema.json"
    actual = _sha256(path)
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
        extra_args=[
            "--emit-selection", "metrics,schema",
            "--components", keep,
            "--interval-seconds", "600",
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
        extra_args=[
            "--emit-selection", "metrics,schema",
            "--metrics-per-component", "1",
            "--interval-seconds", "600",
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


def test_schema_combine_only_does_not_regenerate(amc, tmp_path):
    """``--combine-only`` reads existing per-component CSVs and must NOT
    rewrite ``schema.json``; the path returns before the pre-clean step
    (which would also rewrite it). Mirrors the gauges-file invariant."""
    out = tmp_path / "combine_only"
    # First run: generate schema.json.
    run_capture(amc, out, days=1, extra_args=[
        "--emit-selection", "metrics,schema",
        "--interval-seconds", "600",
    ])
    schema_path = out / "schema.json"
    original_bytes = schema_path.read_bytes()
    # Now mutate the on-disk schema; --combine-only must leave it alone.
    schema_path.write_bytes(b'{"sentinel": "untouched"}\n')
    # Combine-only run.
    run_capture(amc, out, days=1, extra_args=[
        "--combine-only",
        "--interval-seconds", "600",
    ])
    assert schema_path.read_bytes() == b'{"sentinel": "untouched"}\n', (
        "--combine-only must not rewrite or pre-clean schema.json"
    )
    # And the original schema bytes are recoverable by re-running normally.
    run_capture(amc, out, days=1, extra_args=[
        "--emit-selection", "metrics,schema",
        "--interval-seconds", "600",
    ])
    assert schema_path.read_bytes() == original_bytes


# ------------------------------------------------------------------
# DST artifact compatibility
# ------------------------------------------------------------------
def test_schema_records_dst_inject_day(amc, tmp_path):
    """When ``--inject-dst-artifact-day N`` is set, the schema must record
    it so the validator can adjust its row-count expectation."""
    out = tmp_path / "dst"
    run_capture(amc, out, days=2,
                extra_args=["--emit-selection", "metrics,schema",
                            "--inject-dst-artifact-day", "1",
                            "--interval-seconds", "600"])
    doc = _load_schema(out)
    assert doc["metadata"]["inject_dst_artifact_day"] == 1

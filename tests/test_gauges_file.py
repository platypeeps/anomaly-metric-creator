"""Tests for the long-form ``gauges.csv`` file artifact (VER-138).

Covers:
- ``--emit-selection`` accepts the new ``gauges`` token and rejects bad combos.
- The file is written only when opted in, and absent by default.
- Header, byte-determinism, locked SHA-256 golden hashes at 1d and 7d.
- ``--components`` / ``--metrics-per-component`` filter passthrough.
- Chronological ordering and parity with ``_iter_component_rows`` (the same
  source the OTEL gauge stream consumes).
- ``_pre_clean_output_dir`` removes a stale ``gauges.csv`` when ``gauges`` is
  dropped from the next run's emit-selection.
- ``--combine-only`` does NOT regenerate ``gauges.csv``.
"""
import csv
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import SCRIPT_PATH, run_capture


# Short-run helper so most tests stay under a couple of seconds.
SHORT_RUN_ARGS = ("--interval-seconds", "60")


# Locked SHA-256 golden hashes for ``gauges.csv`` at the default --seed (42)
# and the default scenario / signal-level / metrics-per-component knobs at
# --duration-days 1 and 7. Both hashes were captured against the merged
# main commit a571426 + this VER-138 patch and protect against silent drift
# in:
# - the per-component CSV bytes (already locked by DEFAULT_*_DAY_HASHES),
# - the chronological merge tiebreaker (sorted-component order on ties),
# - the per-row metric column order,
# - the dropped-cell skip behavior on ``--drop-rate`` survivors.
GAUGES_ONE_DAY_HASH = (
    "f1b760f0cf1da0dc3eaeb55a4278cd56b024f758c26cdd5fc9693b6f3a5e9c08"
)
GAUGES_SEVEN_DAY_HASH = (
    "1076e0ac35a6b4e2bc3fc0532f4308eab684aadaa59243a47a52048f59045747"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------
@pytest.fixture(scope="module")
def one_day_gauges_run(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("ver138_one_day_gauges")
    return run_capture(
        amc, out, days=1, extra_args=["--emit-selection", "metrics,gauges"]
    )


@pytest.fixture(scope="module")
def seven_day_gauges_run(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("ver138_seven_day_gauges")
    return run_capture(
        amc, out, days=7, extra_args=["--emit-selection", "metrics,gauges"]
    )


# ------------------------------------------------------------------
# parse_args validation
# ------------------------------------------------------------------
def test_emit_selection_accepts_gauges_token(amc, tmp_path):
    args = amc.parse_args([
        "--output-dir", str(tmp_path),
        "--duration-days", "1",
        "--emit-selection", "metrics,gauges",
    ])
    assert "gauges" in args.emit_selection
    assert "metrics" in args.emit_selection


def test_emit_selection_gauges_requires_metrics(capsys, amc, tmp_path):
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--output-dir", str(tmp_path),
            "--duration-days", "1",
            "--emit-selection", "gauges",
        ])
    err = capsys.readouterr().err
    assert "gauges" in err and "metrics" in err


def test_emit_selection_gauges_with_logs_traces_still_requires_metrics(
    capsys, amc, tmp_path
):
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--output-dir", str(tmp_path),
            "--duration-days", "1",
            "--emit-selection", "gauges,logs,traces",
        ])
    err = capsys.readouterr().err
    assert "gauges" in err and "metrics" in err


def test_emit_selection_rejects_unknown_token(capsys, amc, tmp_path):
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--output-dir", str(tmp_path),
            "--duration-days", "1",
            "--emit-selection", "metrics,bogus",
        ])
    err = capsys.readouterr().err
    # Existing message lists valid tokens; assert ``gauges`` is now in it so
    # the help text stays in sync.
    assert "gauges" in err


def test_emit_selection_help_advertises_gauges(amc):
    # parse_args() builds the parser and returns the parsed namespace; the
    # parser instance isn't exposed, so probe through --help output instead.
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--help"],
        capture_output=True, text=True,
    )
    assert "gauges" in result.stdout


def test_emit_selection_gauges_rejects_dst_artifact_combo(amc, capsys, tmp_path):
    """The DST artifact splice (``_splice_dst_artifact``) makes per-component
    CSV timestamps non-monotonic, which breaks the ``heapq.merge`` inside
    ``write_gauges_csv`` (the file peer of ``stream_otel_gauges``). The
    parse-time guard must reject the combination just as it does for the
    OTEL gauge stream (`test_otel_emit_gauges_rejects_dst_artifact_combo`)."""
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--output-dir", str(tmp_path),
            "--duration-days", "2",
            "--emit-selection", "metrics,gauges",
            "--inject-dst-artifact-day", "1",
        ])
    err = capsys.readouterr().err
    assert "gauges" in err and "inject-dst-artifact-day" in err


def test_emit_selection_gauges_allows_dst_artifact_zero(amc, tmp_path):
    """``--inject-dst-artifact-day 0`` (the default, off) must coexist freely
    with ``--emit-selection gauges``."""
    args = amc.parse_args([
        "--output-dir", str(tmp_path),
        "--duration-days", "1",
        "--emit-selection", "metrics,gauges",
        "--inject-dst-artifact-day", "0",
    ])
    assert "gauges" in args.emit_selection
    assert args.inject_dst_artifact_day == 0


# ------------------------------------------------------------------
# File presence / header / determinism
# ------------------------------------------------------------------
def test_gauges_csv_written_when_opted_in(one_day_gauges_run):
    path = one_day_gauges_run.out_dir / "gauges.csv"
    assert path.exists(), "gauges.csv must be written when 'gauges' is in --emit-selection"


def test_gauges_csv_absent_by_default(one_day_run_a):
    # one_day_run_a uses the default --emit-selection (metrics,logs,traces).
    assert not (one_day_run_a.out_dir / "gauges.csv").exists(), (
        "default run must not write gauges.csv unless opted in via --emit-selection"
    )


def test_gauges_csv_header_locked(one_day_gauges_run):
    path = one_day_gauges_run.out_dir / "gauges.csv"
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
    assert header == ["timestamp", "component", "metric", "value"]


def test_gauges_csv_byte_deterministic_same_seed(amc, tmp_path):
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    run_capture(amc, out_a, days=1, extra_args=["--emit-selection", "metrics,gauges"])
    run_capture(amc, out_b, days=1, extra_args=["--emit-selection", "metrics,gauges"])
    assert _sha256(out_a / "gauges.csv") == _sha256(out_b / "gauges.csv")


def test_gauges_csv_byte_identical_default_one_day(one_day_gauges_run):
    path = one_day_gauges_run.out_dir / "gauges.csv"
    actual = _sha256(path)
    assert actual == GAUGES_ONE_DAY_HASH, (
        f"gauges.csv drifted from locked 1-day hash. "
        f"expected={GAUGES_ONE_DAY_HASH} actual={actual}"
    )


def test_gauges_csv_byte_identical_default_seven_day(seven_day_gauges_run):
    path = seven_day_gauges_run.out_dir / "gauges.csv"
    actual = _sha256(path)
    assert actual == GAUGES_SEVEN_DAY_HASH, (
        f"gauges.csv drifted from locked 7-day hash. "
        f"expected={GAUGES_SEVEN_DAY_HASH} actual={actual}"
    )


# ------------------------------------------------------------------
# Filter passthrough
# ------------------------------------------------------------------
def _read_rows(path: Path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def test_gauges_csv_respects_components(amc, tmp_path):
    pair = list(amc.COMPONENTS)[:2]
    keep = pair[0]
    drop = pair[1]
    out = tmp_path / "narrowed"
    run_capture(
        amc, out, days=1,
        extra_args=[
            "--emit-selection", "metrics,gauges",
            "--components", keep,
            "--interval-seconds", "60",
        ],
    )
    rows = _read_rows(out / "gauges.csv")
    components_seen = {r["component"] for r in rows}
    assert components_seen == {keep}, (
        f"gauges.csv must only include rows for --components={keep}; "
        f"saw components={sorted(components_seen)}"
    )
    assert drop not in components_seen


def test_gauges_csv_respects_metrics_per_component(amc, tmp_path):
    out_full = tmp_path / "full"
    out_trim = tmp_path / "trim"
    run_capture(
        amc, out_full, days=1,
        extra_args=[
            "--emit-selection", "metrics,gauges",
            "--interval-seconds", "60",
        ],
    )
    run_capture(
        amc, out_trim, days=1,
        extra_args=[
            "--emit-selection", "metrics,gauges",
            "--metrics-per-component", "1",
            "--interval-seconds", "60",
        ],
    )
    rows_full = _read_rows(out_full / "gauges.csv")
    rows_trim = _read_rows(out_trim / "gauges.csv")
    # Per-component trimmed metric set must be exactly the first MetricSpec
    # of each component's catalog when --metrics-per-component=1.
    metrics_by_component_trim = {}
    for r in rows_trim:
        metrics_by_component_trim.setdefault(r["component"], set()).add(r["metric"])
    for component, metrics in metrics_by_component_trim.items():
        first_spec_name = amc.COMPONENTS[component][0].name
        assert metrics == {first_spec_name}, (
            f"--metrics-per-component=1 must restrict {component} to "
            f"{first_spec_name!r}; saw {sorted(metrics)}"
        )
    assert len(rows_trim) < len(rows_full), (
        "trimmed run must produce strictly fewer gauge rows than the full run"
    )


# ------------------------------------------------------------------
# Chronological ordering + OTEL parity
# ------------------------------------------------------------------
def test_gauges_csv_chronological_order(one_day_gauges_run, amc):
    path = one_day_gauges_run.out_dir / "gauges.csv"
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        prev_dt = None
        for row in reader:
            dt = amc._parse_csv_timestamp(row["timestamp"])
            if prev_dt is not None:
                assert dt >= prev_dt, (
                    "gauges.csv rows must be in non-decreasing timestamp order "
                    f"(got {row['timestamp']} after a later timestamp)"
                )
            prev_dt = dt


def test_gauges_csv_matches_iter_component_rows(one_day_gauges_run, amc):
    """The long-form file artifact must be the row-by-row equivalent of
    ``_iter_component_rows`` — the same source the OTEL gauge stream consumes.

    Tests parity at the ``(timestamp, component, metric, value-as-float)``
    level (the OTLP data-point payload). If this asserts but the byte hash
    drifts, the format changed but the underlying data didn't; if both
    fail together, the data drifted.
    """
    out_dir = one_day_gauges_run.out_dir
    # Reproduce what stream_otel_gauges sees: walk every per-component CSV
    # via _iter_component_rows and assemble the same (ts, comp, metric, value)
    # set the gauge stream would post as OTLP data points.
    expected = set()
    for component in amc.COMPONENTS:
        csv_path = out_dir / f"{component}.csv"
        if not csv_path.exists():
            continue
        for ts, comp, values in amc._iter_component_rows(component, csv_path):
            for metric_name, value in values:
                expected.add((ts, comp, metric_name, value))
    actual = set()
    with open(out_dir / "gauges.csv", "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            actual.add(
                (row["timestamp"], row["component"], row["metric"], float(row["value"]))
            )
    assert actual == expected, (
        "gauges.csv data points must match _iter_component_rows exactly "
        f"(diff: missing={len(expected - actual)}, extra={len(actual - expected)})"
    )


# ------------------------------------------------------------------
# Pre-clean / combine-only behavior
# ------------------------------------------------------------------
def test_pre_clean_removes_stale_gauges_csv(amc, tmp_path):
    # First run emits gauges.csv ...
    run_capture(
        amc, tmp_path, days=1,
        extra_args=[
            "--emit-selection", "metrics,gauges",
            "--interval-seconds", "60",
        ],
    )
    assert (tmp_path / "gauges.csv").exists()
    # ... second run drops the gauges token, so pre-clean must remove it.
    run_capture(
        amc, tmp_path, days=1,
        extra_args=[
            "--emit-selection", "metrics",
            "--interval-seconds", "60",
        ],
    )
    assert not (tmp_path / "gauges.csv").exists(), (
        "_pre_clean_output_dir must remove gauges.csv when gauges is dropped "
        "from --emit-selection on a re-run"
    )


def test_pre_clean_removes_stale_gauges_csv_on_logs_only_rerun(amc, tmp_path):
    run_capture(
        amc, tmp_path, days=1,
        extra_args=[
            "--emit-selection", "metrics,gauges",
            "--interval-seconds", "60",
        ],
    )
    assert (tmp_path / "gauges.csv").exists()
    run_capture(
        amc, tmp_path, days=1,
        extra_args=[
            "--emit-selection", "logs,traces",
            "--interval-seconds", "60",
        ],
    )
    assert not (tmp_path / "gauges.csv").exists()


def test_combine_only_does_not_regenerate_gauges_csv(amc, tmp_path):
    # Seed the directory with a metrics-only run so combine-only has inputs;
    # gauges.csv is absent by design at this point.
    run_capture(
        amc, tmp_path, days=1,
        extra_args=[
            "--emit-selection", "metrics",
            "--interval-seconds", "60",
        ],
    )
    assert not (tmp_path / "gauges.csv").exists()
    # combine-only is the explicit exception to the pre-clean path; it must
    # not regenerate gauges.csv even when 'gauges' would be in --emit-selection.
    # Run it via subprocess so SystemExit / sys.argv parsing don't interfere
    # with the in-process amc fixture.
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT_PATH),
            "--output-dir", str(tmp_path),
            "--duration-days", "1",
            "--combine-only",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "gauges.csv").exists(), (
        "--combine-only must not generate gauges.csv (it's a derived artifact "
        "of a fresh generation run only)"
    )


def test_combine_only_preserves_existing_gauges_csv(amc, tmp_path):
    # If a previous run wrote gauges.csv into --output-dir, combine-only must
    # leave it alone (mirrors anomalies.csv behavior on combine-only).
    run_capture(
        amc, tmp_path, days=1,
        extra_args=[
            "--emit-selection", "metrics,gauges",
            "--interval-seconds", "60",
        ],
    )
    gauges_path = tmp_path / "gauges.csv"
    pre_hash = _sha256(gauges_path)
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT_PATH),
            "--output-dir", str(tmp_path),
            "--duration-days", "1",
            "--combine-only",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert gauges_path.exists()
    assert _sha256(gauges_path) == pre_hash


def test_done_summary_names_gauges_csv(amc, tmp_path, capsys):
    run_capture(
        amc, tmp_path, days=1,
        extra_args=[
            "--emit-selection", "metrics,gauges",
            "--interval-seconds", "60",
        ],
    )
    captured = capsys.readouterr()
    done_lines = [
        line for line in captured.out.splitlines() if line.startswith("Done -")
    ]
    assert len(done_lines) == 1
    assert "gauges.csv" in done_lines[0]


def test_done_summary_omits_gauges_csv_by_default(amc, tmp_path, capsys):
    run_capture(
        amc, tmp_path, days=1,
        extra_args=["--interval-seconds", "60"],
    )
    captured = capsys.readouterr()
    done_lines = [
        line for line in captured.out.splitlines() if line.startswith("Done -")
    ]
    assert len(done_lines) == 1
    assert "gauges.csv" not in done_lines[0]


# ------------------------------------------------------------------
# Registry shape (defends against accidental key drift)
# ------------------------------------------------------------------
def test_emit_artifact_files_registry_has_gauges(amc):
    assert "gauges" in amc._EMIT_ARTIFACT_FILES
    assert amc._EMIT_ARTIFACT_FILES["gauges"] == ("gauges.csv",)


def test_non_component_files_excludes_gauges_csv_from_combine_discovery(amc):
    # combine_logs autodiscovery must not treat gauges.csv as a component CSV.
    assert "gauges.csv" in amc._NON_COMPONENT_FILES


# ------------------------------------------------------------------
# Phase 5 (VER-148): long-form gauges.csv with dimension columns.
#
# When --instances-per-component N > 1, per-component CSVs carry a
# six-column dimension prefix (id, host, pod, az, region, tenant). The
# long-form gauges.csv header expands from 4 columns to 10 columns so
# every (component, instance, metric) data point is reproducible from
# the row alone. N=1 stays byte-identical to today (locked by the 4-
# column hashes above) — the dimensioned 10-column shape only applies
# when the per-component CSVs have dimension columns in their header.
# ------------------------------------------------------------------
N3_GAUGES_ONE_DAY_HASH = (
    "71164965eb8ad036ff6e0cf1ce52dfadff00406b094f39ebf49c4808c108684c"
)


_LONG_FORM_HEADER = [
    "timestamp", "component", "id", "host", "pod", "az",
    "region", "tenant", "metric", "value",
]


@pytest.fixture(scope="module")
def n3_one_day_gauges_run(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("ver148_n3_one_day_gauges")
    return run_capture(
        amc, out, days=1,
        extra_args=[
            "--emit-selection", "metrics,gauges",
            "--instances-per-component", "3",
        ],
    )


def test_n3_gauges_csv_has_long_form_header(n3_one_day_gauges_run):
    path = n3_one_day_gauges_run.out_dir / "gauges.csv"
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
    assert header == _LONG_FORM_HEADER, (
        f"N=3 gauges.csv header must be the 10-column long form; got {header}"
    )


def test_n3_gauges_csv_byte_identical_one_day(n3_one_day_gauges_run):
    """Lock the N=3 1-day gauges.csv bytes. Captured against the Phase 5
    landing; future changes to the long-form header, the
    (timestamp, component, instance_id, metric) tie-break order, the
    per-(component, instance) iterator construction, or the dropped-cell
    skip behavior under the dimensioned path must regenerate this hash
    and document the cause in the PR description."""
    path = n3_one_day_gauges_run.out_dir / "gauges.csv"
    actual = _sha256(path)
    assert actual == N3_GAUGES_ONE_DAY_HASH, (
        f"N=3 gauges.csv drifted from locked 1-day hash. "
        f"expected={N3_GAUGES_ONE_DAY_HASH} actual={actual}"
    )


def test_n3_gauges_csv_tie_break_within_timestamp(n3_one_day_gauges_run):
    """At any single timestamp the long-form rows must walk components in
    sorted order, then instance ids in sorted order within each component,
    then metric columns in MetricSpec order (i.e. the per-component CSV's
    declared column order). A regression in the source-sort key or the
    per-instance block iterator would trip this assertion before the
    byte-identity test."""
    from itertools import groupby
    rows = _read_rows(n3_one_day_gauges_run.out_dir / "gauges.csv")
    saw_groups = 0
    for ts, ts_group in groupby(rows, key=lambda r: r["timestamp"]):
        ts_group = list(ts_group)
        # Component-level: first appearance of each component must be sorted.
        component_order = []
        for comp, _ in groupby(ts_group, key=lambda r: r["component"]):
            component_order.append(comp)
        assert component_order == sorted(component_order), (
            f"timestamp {ts!r}: components appeared in {component_order}, "
            f"expected sorted order {sorted(component_order)}"
        )
        # Within each component, instance ids must be in sorted order.
        for comp, comp_group in groupby(ts_group, key=lambda r: r["component"]):
            instance_order = []
            for inst_id, _ in groupby(
                comp_group, key=lambda r: r["id"]
            ):
                instance_order.append(inst_id)
            assert instance_order == sorted(instance_order), (
                f"timestamp {ts!r} component {comp!r}: instance ids "
                f"appeared in {instance_order}, expected sorted order "
                f"{sorted(instance_order)}"
            )
        saw_groups += 1
        if saw_groups >= 60:  # bound runtime — first minute of 1-day run
            break
    assert saw_groups > 0, "no rows in N=3 long-form gauges.csv"


def test_n3_gauges_csv_dimension_values_match_per_component_csvs(
    n3_one_day_gauges_run, amc,
):
    """Every (component, id, host, pod, az, region, tenant) tuple in
    gauges.csv must appear as a row in the corresponding per-component
    CSV's dimensioned block. A regression that swapped the instance
    block boundary detection (the ``has_dims`` branch in
    ``_iter_component_instance_rows``) or smuggled in stale dim values
    from the closure would surface here even before the byte hash."""
    out_dir = n3_one_day_gauges_run.out_dir
    rows = _read_rows(out_dir / "gauges.csv")
    assert rows, "N=3 gauges.csv must contain rows"

    # Tuples observed in gauges.csv.
    gauge_tuples: set[tuple[str, ...]] = set()
    for r in rows:
        gauge_tuples.add(
            (r["component"], r["id"], r["host"], r["pod"], r["az"],
             r["region"], r["tenant"])
        )

    # Tuples observed in the per-component CSVs themselves.
    expected_tuples: set[tuple[str, ...]] = set()
    for component in amc.COMPONENTS:
        path = out_dir / f"{component}.csv"
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                expected_tuples.add(
                    (component, row["id"], row["host"], row["pod"],
                     row["az"], row["region"], row["tenant"])
                )
    assert len(expected_tuples) > 0, (
        "per-component CSVs surfaced no dimension tuples — "
        "test cannot exercise the long-form parity"
    )
    assert gauge_tuples == expected_tuples, (
        f"gauges.csv dimension tuples must match per-component CSVs "
        f"exactly (missing={len(expected_tuples - gauge_tuples)}, "
        f"extra={len(gauge_tuples - expected_tuples)})"
    )


def test_n3_gauges_csv_chronological_order(n3_one_day_gauges_run, amc):
    """Long-form rows are chronologically merged; consecutive rows must be
    in non-decreasing timestamp order despite the per-component CSVs
    holding non-monotonic block-then-block-then-block sequences."""
    path = n3_one_day_gauges_run.out_dir / "gauges.csv"
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        prev_dt = None
        for row in reader:
            dt = amc._parse_csv_timestamp(row["timestamp"])
            if prev_dt is not None:
                assert dt >= prev_dt, (
                    "N=3 gauges.csv rows must be in non-decreasing "
                    "timestamp order across instance blocks"
                )
            prev_dt = dt


def test_classify_component_csv_header_detects_dimensions(amc):
    """The header inspector must classify both shapes:
    dimensionless ``timestamp,m0,m1`` → no dim cols, all metrics; and
    dimensioned ``timestamp,id,host,pod,az,region,tenant,m0,m1`` → six dim
    cols + the remaining metric tail. An ambiguous header that only
    partially matches the dim prefix must be treated as no-dim so a
    user-staged CSV with a column literally named ``id`` cannot smuggle
    in dimensioned parsing."""
    flat = ["timestamp", "metric_a", "metric_b"]
    dim_cols, metric_cols = amc._classify_component_csv_header(flat)
    assert dim_cols == ()
    assert metric_cols == ["metric_a", "metric_b"]

    full = [
        "timestamp", "id", "host", "pod", "az", "region", "tenant",
        "metric_a", "metric_b",
    ]
    dim_cols, metric_cols = amc._classify_component_csv_header(full)
    assert dim_cols == amc._INSTANCE_DIMENSION_COLUMNS
    assert metric_cols == ["metric_a", "metric_b"]

    partial = ["timestamp", "id", "metric_a"]
    dim_cols, metric_cols = amc._classify_component_csv_header(partial)
    assert dim_cols == ()
    assert metric_cols == ["id", "metric_a"]

    empty: list[str] = []
    dim_cols, metric_cols = amc._classify_component_csv_header(empty)
    assert dim_cols == ()
    assert metric_cols == []


def test_n3_gauges_csv_with_metrics_per_component_trim(amc, tmp_path):
    """``--instances-per-component 3 --metrics-per-component 1`` exercises
    the n_metrics slicing in ``_iter_component_instance_rows`` (the
    per-block iterator slices each row to ``1 + dim_count + n_metrics``).
    A regression in the slice would either drop the metric value or
    pull in the next instance's dimension columns. Verified by:
    1) the long-form 10-column header is unchanged (dim columns always
       present when any CSV is dimensioned), and
    2) the set of metric names in gauges.csv per component equals the
       first MetricSpec of that component's catalog."""
    out = tmp_path / "n3_trim"
    run_capture(
        amc, out, days=1,
        extra_args=[
            "--emit-selection", "metrics,gauges",
            "--instances-per-component", "3",
            "--metrics-per-component", "1",
            "--interval-seconds", "60",
        ],
    )
    gauges = out / "gauges.csv"
    assert gauges.exists()
    rows = _read_rows(gauges)
    assert rows, "trim run must emit some gauge rows"
    metrics_by_component: dict[str, set[str]] = {}
    for r in rows:
        metrics_by_component.setdefault(r["component"], set()).add(r["metric"])
    assert metrics_by_component, "no components surfaced in the trim run"
    for component, metrics in metrics_by_component.items():
        first_spec_name = amc.COMPONENTS[component][0].name
        assert metrics == {first_spec_name}, (
            f"--metrics-per-component=1 must restrict {component} to "
            f"{first_spec_name!r}; saw {sorted(metrics)}"
        )


def test_scan_instance_block_layout_records_seekable_offsets(amc, tmp_path):
    """The scan helper must record seek cookies (the opaque positions
    Python's text-mode ``tell()`` returns) that ``seek()`` can hand to
    ``csv.reader`` cleanly — anything else would corrupt the long-form
    merge, because ``_iter_component_instance_rows`` ``seek()``s
    straight to the recorded cookie and then parses with
    ``csv.reader``. We assert the cookies land on row starts by
    seeking + reading and comparing to a manual line-by-line walk
    against a handle opened with the same ``encoding="utf-8"`` /
    ``newline=""`` settings the implementation uses."""
    csv_path = tmp_path / "synth_inst.csv"
    csv_path.write_text(
        "timestamp,id,host,pod,az,region,tenant,m_a\n"
        "2026-03-10 00:00:00,i0,,pod-0,,,,1.0\n"
        "2026-03-10 00:00:01,i0,,pod-0,,,,1.1\n"
        "2026-03-10 00:00:00,i1,,pod-1,,,,2.0\n"
        "2026-03-10 00:00:01,i1,,pod-1,,,,2.1\n"
        "2026-03-10 00:00:00,i2,,pod-2,,,,3.0\n"
    )
    blocks = amc._scan_instance_block_layout(csv_path, has_dims=True)
    assert [dims[0] for dims, _ in blocks] == ["i0", "i1", "i2"]

    # Manually compute the byte offset of every non-header line and
    # confirm the scan recorded those exact positions for each block's
    # first row.
    with open(csv_path, "r", encoding="utf-8", newline="") as fh:
        fh.readline()  # header
        row0_pos = fh.tell()
        fh.readline()  # i0 row 0
        fh.readline()  # i0 row 1
        i1_pos = fh.tell()
        fh.readline()  # i1 row 0
        fh.readline()  # i1 row 1
        i2_pos = fh.tell()

    assert blocks[0] == (("i0", "", "pod-0", "", "", ""), row0_pos)
    assert blocks[1] == (("i1", "", "pod-1", "", "", ""), i1_pos)
    assert blocks[2] == (("i2", "", "pod-2", "", "", ""), i2_pos)

    # End-to-end: passing the recorded offset to the iterator must yield
    # exactly the matching block's rows in order. A future regression
    # that drifted the offset by even one byte (e.g. forgot
    # ``newline=""``, or hand-rolled a parser that miscounted ``\r\n``)
    # would surface here as a parse error or an off-by-one row count.
    i1_rows = list(
        amc._iter_component_instance_rows(
            csv_path, i1_pos, has_dims=True, n_metrics=1,
        )
    )
    assert len(i1_rows) == 2
    assert i1_rows[0][1] == "2026-03-10 00:00:00"
    assert i1_rows[0][2] == ["2.0"]
    assert i1_rows[1][2] == ["2.1"]


def test_scan_instance_block_layout_dimensionless(amc, tmp_path):
    """The has_dims=False path returns a single conceptual block at the
    byte offset right after the header line. The iterator's
    has_dims=False branch then yields every data row without comparing
    dim tuples."""
    csv_path = tmp_path / "synth_flat.csv"
    csv_path.write_text(
        "timestamp,m_a,m_b\n"
        "2026-03-10 00:00:00,1.0,2.0\n"
        "2026-03-10 00:00:01,1.1,2.1\n"
    )
    blocks = amc._scan_instance_block_layout(csv_path, has_dims=False)
    assert len(blocks) == 1
    dims, offset = blocks[0]
    # Six empty-string dim slots — the dimensionless sentinel.
    assert dims == tuple("" for _ in amc._INSTANCE_DIMENSION_COLUMNS)

    with open(csv_path, "r", encoding="utf-8", newline="") as fh:
        fh.readline()
        expected_offset = fh.tell()
    assert offset == expected_offset

    rows = list(
        amc._iter_component_instance_rows(
            csv_path, offset, has_dims=False, n_metrics=2,
        )
    )
    assert [r[1] for r in rows] == [
        "2026-03-10 00:00:00", "2026-03-10 00:00:01",
    ]
    assert [r[2] for r in rows] == [["1.0", "2.0"], ["1.1", "2.1"]]


def test_ensure_long_form_fd_capacity_fits_under_default_limit(amc):
    """At the documented N=3 default fan-out (13 components × 3 instances
    = 39 sources), the FD pre-flight is a no-op on every platform we
    support — even macOS's default 256 soft limit has plenty of room.
    A regression that lowered the margin or capped the rlimit raise
    aggressively would surface here before the merge ran. On Windows
    the helper is a no-op regardless (``resource`` is POSIX-only), so
    the assertion that it doesn't raise still holds."""
    # No-op path: helper must not raise for a fan-out far inside the
    # default soft limit.
    amc._ensure_long_form_fd_capacity(0)
    amc._ensure_long_form_fd_capacity(
        len(amc.COMPONENTS) * 3
    )


def test_ensure_long_form_fd_capacity_raises_systemexit_when_hard_limit_too_low(
    amc, monkeypatch
):
    """A future fan-out beyond the hard limit must fail with an
    actionable ``SystemExit`` that names the needed FD count and the
    user-facing levers (``--instances-per-component``, ``--components``,
    or ``ulimit -n``) rather than crashing inside ``heapq.merge`` with
    ``OSError: [Errno 24] Too many open files``. We monkey-patch
    ``resource.getrlimit`` to simulate a constrained environment so the
    test stays deterministic across CI hosts.

    The implementation explicitly no-ops on Windows when ``resource`` is
    unavailable, so this test skips there — the constrained-rlimit
    branch can't be exercised without POSIX ``RLIMIT_NOFILE``."""
    resource = pytest.importorskip("resource")
    monkeypatch.setattr(
        resource, "getrlimit",
        lambda which: (32, 32) if which == resource.RLIMIT_NOFILE else (0, 0),
    )
    with pytest.raises(SystemExit) as excinfo:
        amc._ensure_long_form_fd_capacity(260)
    msg = str(excinfo.value)
    assert "260" in msg, msg
    assert "FD" in msg or "file handle" in msg.lower(), msg
    assert (
        "--instances-per-component" in msg
        or "ulimit" in msg
        or "--components" in msg
    ), msg


# ------------------------------------------------------------------
# Coverage gaps — sub-second interval, tie-break order, --combine,
# --drop-rate parity. Recommended additions from the Code Reviewer
# hand-back on PR #38.
# ------------------------------------------------------------------
def test_gauges_csv_sub_second_interval(amc, tmp_path):
    """Millisecond timestamps (``--interval-seconds`` < 1.0) flow through the
    ``"." in timestamp`` branch of ``_parse_csv_timestamp``. Confirm the
    chronological ordering invariant holds when the per-component CSVs
    contain millisecond-precision timestamps."""
    out = tmp_path / "sub_second"
    run_capture(
        amc, out, days=1,
        extra_args=[
            "--emit-selection", "metrics,gauges",
            # 0.5s cadence renders millisecond-suffixed timestamps and
            # exercises ``_parse_csv_timestamp``'s "." branch. Restrict to
            # one component / one metric to keep the row count modest.
            "--interval-seconds", "0.5",
            "--components", list(amc.COMPONENTS)[0],
            "--metrics-per-component", "1",
        ],
    )
    path = out / "gauges.csv"
    assert path.exists()
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert rows, "sub-second gauges.csv must contain at least one data row"
    assert any("." in r["timestamp"] for r in rows), (
        "0.5s --interval-seconds must render millisecond timestamps "
        "(timestamp must contain a '.' separator)"
    )
    prev_dt = None
    for row in rows:
        dt = amc._parse_csv_timestamp(row["timestamp"])
        if prev_dt is not None:
            assert dt >= prev_dt, (
                "gauges.csv rows must stay in non-decreasing timestamp order "
                "even when --interval-seconds < 1.0"
            )
        prev_dt = dt


def test_gauges_csv_tie_break_follows_sorted_component_order(amc, tmp_path):
    """At any single timestamp, multiple components emit data points. The
    file artifact's tiebreaker is sorted-component order; assert that
    explicitly so a future caller reordering ``component_csv_paths`` (or a
    regression in ``write_gauges_csv``'s internal sort) is caught with a
    readable failure rather than only a golden-hash drift."""
    out = tmp_path / "tie_break"
    run_capture(
        amc, out, days=1,
        extra_args=[
            "--emit-selection", "metrics,gauges",
            "--interval-seconds", "60",
        ],
    )
    rows = _read_rows(out / "gauges.csv")
    # Group rows by timestamp. Within a single timestamp, gather component
    # names in the order they appear in gauges.csv (preserving consecutive
    # duplicates so we see the per-component block boundaries) — then
    # collapse to a per-block "first appearance" list and verify it's
    # sorted.
    from itertools import groupby
    for ts, group in groupby(rows, key=lambda r: r["timestamp"]):
        components_in_order = []
        for comp, _ in groupby(group, key=lambda r: r["component"]):
            components_in_order.append(comp)
        assert components_in_order == sorted(components_in_order), (
            f"timestamp {ts!r}: components appeared in {components_in_order} "
            f"but must appear in sorted order {sorted(components_in_order)}"
        )


def test_gauges_csv_drop_rate_skips_dropped_rows(amc, tmp_path):
    """The docstring promises dropped CSV rows are absent from ``gauges.csv``
    (mirroring ``stream_otel_gauges``'s behavior on dropped rows). Pin that
    invariant: with ``--drop-rate > 0`` the gauges.csv timestamps must be a
    subset of the per-component CSV timestamps."""
    out = tmp_path / "drop_rate"
    component = list(amc.COMPONENTS)[0]
    run_capture(
        amc, out, days=1,
        drop_rate=0.5,
        extra_args=[
            "--emit-selection", "metrics,gauges",
            "--interval-seconds", "60",
            "--components", component,
            "--metrics-per-component", "1",
        ],
    )
    component_csv = out / f"{component}.csv"
    gauges_csv = out / "gauges.csv"
    # Collect surviving timestamps from the per-component CSV.
    survivors = set()
    with open(component_csv, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        next(reader)  # header
        for row in reader:
            if not row:
                continue
            survivors.add(row[0])
    # Every gauge row's timestamp must be a survivor; never a dropped row.
    with open(gauges_csv, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        gauge_timestamps = {r["timestamp"] for r in reader}
    assert gauge_timestamps, "gauges.csv must contain at least one data row"
    assert gauge_timestamps <= survivors, (
        f"gauges.csv emitted {len(gauge_timestamps - survivors)} timestamps "
        "that don't exist in the per-component CSV (dropped rows must be "
        "absent from the file peer of the gauge stream)"
    )
    # Sanity: --drop-rate=0.5 over a full day at 60s cadence should drop
    # *some* rows, so survivors should be a strict subset of the expected
    # row count (otherwise the test isn't actually exercising drops).
    full_row_count = 24 * 60  # 1 day at 60s cadence
    assert len(survivors) < full_row_count, (
        "--drop-rate=0.5 must drop at least one row at 1d / 60s — "
        f"got {len(survivors)} survivors out of {full_row_count}"
    )


def test_gauges_csv_works_with_combine_flag(amc, tmp_path):
    """``--combine`` and ``--emit-selection gauges`` together: both
    artifacts must be written, and the combine autodiscovery must NOT
    treat ``gauges.csv`` as a per-component CSV (the ``_NON_COMPONENT_FILES``
    guard, validated at set-membership level by
    ``test_non_component_files_excludes_gauges_csv_from_combine_discovery``,
    must also hold end-to-end)."""
    out = tmp_path / "combine_and_gauges"
    run_capture(
        amc, out, days=1,
        extra_args=[
            "--emit-selection", "metrics,gauges",
            "--combine",
            "--interval-seconds", "60",
        ],
    )
    gauges_path = out / "gauges.csv"
    combined_path = out / "combined_metrics_unified.csv"
    assert gauges_path.exists(), "gauges.csv must exist when 'gauges' is emitted"
    assert combined_path.exists(), "combined_metrics_unified.csv must exist when --combine is set"
    # Read the combined CSV header. ``combine_logs`` uses a wide
    # ``timestamp + <component>_<metric>...`` schema; if gauges.csv had
    # leaked into autodiscovery we'd see ``gauges_component``,
    # ``gauges_metric``, ``gauges_value`` columns. Assert their absence.
    with open(combined_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
    assert header[0] == "timestamp"
    leaked = [
        h for h in header
        if h in ("gauges_component", "gauges_metric", "gauges_value")
    ]
    assert not leaked, (
        f"combined_metrics_unified.csv contains leaked gauges.csv columns "
        f"{leaked}: --combine autodiscovery must filter gauges.csv via "
        "_NON_COMPONENT_FILES"
    )

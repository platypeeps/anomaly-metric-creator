"""Combine-step integration: autodiscovery, llm_analytics inclusion,
row preservation, and the synthetic-extra-component case.
"""

import csv
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import COMPONENTS, run_capture, sha256_path


@pytest.fixture(scope="module")
def combined_dir(amc, one_day_run_a, tmp_path_factory):
    """Stage a copy of the 1-day fixture into tmp/iot_logs, drop in a synthetic
    extra component, then run the inlined combine step against it exactly once.

    Module-scoped so the three assertions below share one combine pass.
    """
    iot_logs = tmp_path_factory.mktemp("combine") / "iot_logs"
    iot_logs.mkdir()
    for src in one_day_run_a.out_dir.iterdir():
        shutil.copy2(src, iot_logs / src.name)

    extra = iot_logs / "synthetic_widget.csv"
    extra.write_text(
        "timestamp,widget_count,widget_health\n"
        "2026-03-10 00:00:00,42,99.9\n"
        "2026-03-10 00:00:01,43,99.8\n"
    )

    components = amc.discover_components(iot_logs)
    amc.combine_logs_unified(components, iot_logs)
    return iot_logs, components


def test_autodiscovery_includes_all_components_and_extra(combined_dir):
    iot_logs, components = combined_dir
    for c in COMPONENTS:
        assert c in components, f"autodiscovery missed {c}"
    assert "synthetic_widget" in components, "synthetic extra component not autodiscovered"
    assert "anomalies" not in components, "anomalies manifest leaked into components"
    assert not any(c.startswith("combined_metrics_") for c in components), (
        "combine output leaked into components"
    )


def test_unified_csv_has_llm_analytics_columns(combined_dir):
    iot_logs, _ = combined_dir
    out_file = iot_logs / "combined_metrics_unified.csv"
    assert out_file.exists()
    with open(out_file) as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
    llm_cols = [f for f in fieldnames if f.startswith("llm_analytics_")]
    assert len(llm_cols) == 8, f"expected 8 llm_analytics_* columns, got {llm_cols}"
    assert "synthetic_widget_widget_count" in fieldnames
    assert "synthetic_widget_widget_health" in fieldnames


def test_unified_row_count_matches_timestamp_union(amc, combined_dir, one_day_run_a):
    """Unified row count equals the union of timestamps present across all source
    CSVs. With the default drop rate at zero, every component contributes every
    second in the day. No llm_analytics data is lost.
    """
    iot_logs, components = combined_dir
    out_file = iot_logs / "combined_metrics_unified.csv"
    with open(out_file) as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    expected_timestamps = set()
    for component in components:
        with open(iot_logs / f"{component}.csv") as f:
            r = csv.reader(f)
            next(r)
            for row in r:
                if row:
                    expected_timestamps.add(row[0])

    assert len(rows) == len(expected_timestamps), (
        f"unified rows={len(rows)} but union of source timestamps={len(expected_timestamps)}"
    )
    # llm_analytics data preserved row-for-row at the timestamps llm_analytics emitted.
    llm_source_count = sum(
        1 for _ in csv.reader(open(iot_logs / "llm_analytics.csv"))
    ) - 1  # minus header
    rows_with_llm = sum(1 for r in rows if r["llm_analytics_avg_llm_latency_ms"] != "")
    assert rows_with_llm == llm_source_count, (
        f"unified has {rows_with_llm} llm_analytics rows; source has {llm_source_count}"
    )


def test_combine_subcommand_produces_unified_csv(amc, one_day_run_a, tmp_path):
    """End-to-end ``combine DIR`` subcommand run produces the unified file
    against an existing run directory without re-generating component CSVs.
    """
    staged = tmp_path / "iot_logs"
    staged.mkdir()
    for src in one_day_run_a.out_dir.iterdir():
        shutil.copy2(src, staged / src.name)

    component_mtimes_before = {
        p.name: p.stat().st_mtime_ns for p in staged.iterdir()
    }
    combined = staged / "combined_metrics_unified.csv"
    assert not combined.exists()

    amc.main(["combine", str(staged)])

    assert combined.exists(), "combine subcommand did not write the unified CSV"
    with open(combined) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames
    assert rows, "unified CSV is empty"
    assert any(f.startswith("llm_analytics_") for f in fieldnames), (
        "unified CSV missing llm_analytics_* columns"
    )
    # No regeneration: source component files must not have been rewritten.
    for name, mtime in component_mtimes_before.items():
        assert (staged / name).stat().st_mtime_ns == mtime, (
            f"the combine subcommand rewrote source file {name}"
        )


def _run_combine_subprocess(out_dir, *extra_args):
    """Run anomaly-metric-creator.py in a subprocess so generation-level
    side-effects (notably ``cascading_anomalies`` filtering by
    ``--metrics-per-component``) don't pollute the session-scoped ``amc``
    module that other tests share."""
    import subprocess
    import sys as _sys
    from conftest import SCRIPT_PATH
    result = subprocess.run(
        [_sys.executable, str(SCRIPT_PATH),
         "--seed", "42",
         "--duration-days", "1",
         "--interval-seconds", "60",
         "--emit", "metrics,logs,traces,combined",
         "--output-dir", str(out_dir),
         *extra_args],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _read_unified_fieldnames(path):
    with open(path) as f:
        reader = csv.DictReader(f)
        return reader.fieldnames


def test_combine_with_metrics_per_component_full_catalog(amc, tmp_path):
    """``--emit ...,combined --metrics-per-component 10`` includes every supplemental
    metric column in the unified CSV, in COMPONENTS-declared order.

    Guards regressions where the combine step drops the supplemental tail or
    reorders trimmed schemas — the existing default-only combine tests don't
    cover either case because they only see the historic per-component count.
    """
    out = tmp_path / "iot_logs"
    _run_combine_subprocess(out, "--metrics-per-component", "10")
    unified = out / "combined_metrics_unified.csv"
    assert unified.exists(), "combine did not write the unified CSV"
    fieldnames = _read_unified_fieldnames(unified)

    # Every component should contribute 10 prefixed columns in the unified CSV.
    for component, specs in amc.COMPONENTS.items():
        expected = [f"{component}_{s.name}" for s in specs[: amc.MAX_METRICS_PER_COMPONENT]]
        observed = [f for f in fieldnames if f.startswith(f"{component}_")]
        assert observed == expected, (
            f"{component}: unified columns {observed} != schema-derived "
            f"order {expected}"
        )


def test_combine_with_metrics_per_component_trims_unified_columns(amc, tmp_path):
    """``--emit ...,combined --metrics-per-component 3`` produces a unified CSV whose
    per-component column set is exactly the first 3 schema metrics, with no
    leftover columns from the historic default catalog."""
    out = tmp_path / "iot_logs"
    _run_combine_subprocess(out, "--metrics-per-component", "3")
    unified = out / "combined_metrics_unified.csv"
    assert unified.exists(), "combine did not write the unified CSV"
    fieldnames = _read_unified_fieldnames(unified)

    for component, specs in amc.COMPONENTS.items():
        expected = [f"{component}_{s.name}" for s in specs[:3]]
        observed = [f for f in fieldnames if f.startswith(f"{component}_")]
        assert observed == expected, (
            f"{component}: unified columns {observed} != expected first-3 "
            f"{expected}"
        )


def test_combine_subcommand_components_filters_unified_columns(amc, one_day_run_a, tmp_path):
    """``combine DIR --components authservice,database`` writes a unified CSV
    whose columns are exactly ``timestamp`` plus every ``authservice_*`` and
    ``database_*`` column — no other component prefixes leak in even though
    the staged --output-dir holds CSVs for every component."""
    staged = tmp_path / "iot_logs"
    staged.mkdir()
    for src in one_day_run_a.out_dir.iterdir():
        shutil.copy2(src, staged / src.name)

    amc.main([
        "combine", str(staged),
        "--components", "authservice,database",
    ])

    unified = staged / "combined_metrics_unified.csv"
    assert unified.exists(), "the combine subcommand did not write the unified CSV"
    fieldnames = _read_unified_fieldnames(unified)

    auth_specs = amc.COMPONENTS["authservice"]
    db_specs = amc.COMPONENTS["database"]
    auth_default = amc.DEFAULT_METRICS_PER_COMPONENT["authservice"]
    db_default = amc.DEFAULT_METRICS_PER_COMPONENT["database"]
    expected_auth = [f"authservice_{s.name}" for s in auth_specs[:auth_default]]
    expected_db = [f"database_{s.name}" for s in db_specs[:db_default]]
    expected = ["timestamp", *expected_auth, *expected_db]

    assert fieldnames == expected, (
        f"unified columns {fieldnames} != expected {expected}"
    )


def test_combine_with_components_filters_unified_columns(amc, tmp_path):
    """``--emit ...,combined --components authservice,database`` writes a unified CSV
    containing only those two components' columns, even if a leftover
    component CSV from a previous run is sitting in --output-dir.
    """
    out = tmp_path / "iot_logs"
    out.mkdir()
    leftover = out / "scheduler.csv"
    leftover.write_text(
        "timestamp,scheduler_jobs_in_queue\n"
        "2026-03-10 00:00:00,7\n"
    )

    _run_combine_subprocess(out, "--components", "authservice,database")
    unified = out / "combined_metrics_unified.csv"
    assert unified.exists(), "combine did not write the unified CSV"
    fieldnames = _read_unified_fieldnames(unified)

    component_prefixes = {
        c for c in amc.COMPONENTS.keys() if any(f.startswith(f"{c}_") for f in fieldnames)
    }
    assert component_prefixes == {"authservice", "database"}, (
        f"unified contained columns for {component_prefixes}, "
        f"expected only {{'authservice', 'database'}}"
    )
    assert fieldnames[0] == "timestamp"


def test_generated_default_combined_ignores_foreign_csv(amc, tmp_path):
    """Generation combines the selected component set, not every CSV on disk.

    ``_pre_clean_output_dir`` deliberately preserves unknown files in
    ``--output-dir``. The generated ``combined`` artifact must still be a pure
    view of the component CSVs written by this run; autodiscovery belongs to
    standalone ``combine DIR``.
    """
    out = tmp_path / "iot_logs"
    out.mkdir()
    foreign = out / "synthetic_widget.csv"
    foreign.write_text(
        "timestamp,widget_count,widget_health\n"
        "2026-03-10 00:00:00,42,99.9\n",
        encoding="utf-8",
    )

    run_capture(
        amc,
        out,
        days=1,
        interval_seconds=3600,
        extra_args=["--emit", "metrics,schema,combined"],
    )

    unified = out / "combined_metrics_unified.csv"
    fieldnames = _read_unified_fieldnames(unified)
    assert foreign.exists(), "generation should not delete user-owned files"
    assert not any(f.startswith("synthetic_widget_") for f in fieldnames), (
        f"foreign CSV columns leaked into generated combined output: {fieldnames}"
    )

    schema = amc._load_schema_document(out / "schema.json")
    assert "synthetic_widget.csv" not in schema["files"]
    assert "synthetic_widget" not in schema["metadata"]["components"]


def test_combine_subcommand_components_missing_csv_errors(amc, one_day_run_a, tmp_path):
    """``combine DIR --components a,b`` errors clearly when one of the
    requested component CSVs is missing from DIR, naming the
    missing file."""
    staged = tmp_path / "iot_logs"
    staged.mkdir()
    for src in one_day_run_a.out_dir.iterdir():
        shutil.copy2(src, staged / src.name)
    (staged / "database.csv").unlink()

    with pytest.raises(SystemExit) as excinfo:
        amc.main([
            "combine", str(staged),
            "--components", "authservice,database",
        ])
    message = str(excinfo.value)
    assert "database.csv" in message, (
        f"SystemExit message {message!r} did not name database.csv"
    )


def test_combine_with_dst_artifact_preserves_all_rows(amc, tmp_path):
    """'combined' emission + --inject-dst-artifact-day must NOT silently drop rows.

    DST fall-back duplicates the 02:00–02:59 wall-clock hour in each
    per-component CSV. The unified output must include both copies.
    """
    out_dir = tmp_path / "dst_combine"
    run_capture(
        amc,
        out_dir,
        days=1,
        drop_rate=0,
        extra_args=[
            "--emit", "metrics,logs,traces,combined",
            "--inject-dst-artifact-day", "1",
        ],
        interval_seconds=600,
    )

    unified = out_dir / "combined_metrics_unified.csv"
    assert unified.exists()

    per_component_counts = []
    for csv_path in sorted(out_dir.glob("*.csv")):
        if csv_path.name == "anomalies.csv" or csv_path.name.startswith("combined_metrics_"):
            continue
        with open(csv_path) as f:
            per_component_counts.append(sum(1 for _ in f) - 1)  # minus header

    with open(unified) as f:
        unified_rows = sum(1 for _ in f) - 1

    # With drop_rate=0, every per-component CSV has the same row count and
    # the unified output must preserve every row.
    assert len(set(per_component_counts)) == 1, (
        f"per-component row counts differ at drop_rate=0: {per_component_counts}"
    )
    assert unified_rows == per_component_counts[0], (
        f"unified rows {unified_rows} != per-component rows "
        f"{per_component_counts[0]}; DST duplicates dropped"
    )

    # The DST hour at 10-minute sampling has 6 slots, each appearing twice.
    import csv as _csv
    with open(unified) as f:
        reader = _csv.DictReader(f)
        timestamps = [row["timestamp"] for row in reader]
    from collections import Counter
    counts = Counter(timestamps)
    dst_slots = [ts for ts in counts if " 02:" in ts and ts.endswith(":00")]
    assert len(dst_slots) == 6, f"expected 6 distinct 02:XX:00 slots, got {dst_slots}"
    for slot in dst_slots:
        assert counts[slot] == 2, f"slot {slot} occurs {counts[slot]} times, expected 2"


def test_combine_without_dst_artifact_unchanged(amc, tmp_path):
    """Combine output for a default (non-DST) run is unchanged by the
    occurrence-keyed combine — every row has occurrence 0 and the sort
    key (timestamp, 0) collapses to timestamp-only ordering."""
    out_dir = tmp_path / "no_dst_combine"
    run_capture(amc, out_dir, days=1, extra_args=["--emit", "metrics,logs,traces,combined"])
    unified = out_dir / "combined_metrics_unified.csv"
    assert unified.exists()

    with open(unified) as f:
        rows = f.readlines()
    timestamps = [line.split(",", 1)[0] for line in rows[1:]]
    assert timestamps == sorted(set(timestamps)), (
        "non-DST combine output should be timestamp-sorted with no duplicates"
    )
    assert len(timestamps) == len(set(timestamps)), (
        "non-DST combine should produce no duplicate timestamps"
    )


def test_generated_non_dst_combined_skips_known_component_prescan(
    amc, tmp_path, monkeypatch,
):
    """Freshly-generated non-DST CSVs are already chronological, so combined
    emission should avoid the extra full-file monotonic pre-scan for those
    known component files.
    """
    scanned = []

    def fail_if_prescanned(path):
        scanned.append(Path(path).name)
        raise AssertionError(
            f"unexpected monotonic pre-scan for generated file {path}"
        )

    monkeypatch.setattr(amc, "_wide_component_rows_are_monotonic", fail_if_prescanned)
    out_dir = tmp_path / "generated_fast_combine"

    run_capture(
        amc,
        out_dir,
        days=1,
        drop_rate=0,
        interval_seconds=3600,
        extra_args=[
            "--emit", "metrics,combined",
            "--components", "authservice,database",
        ],
    )

    assert not scanned
    assert (out_dir / "combined_metrics_unified.csv").exists()


def test_combine_subcommand_prescans_external_wide_inputs(
    amc, tmp_path, monkeypatch,
):
    """The external ``combine DIR`` path cannot assume staged CSV ordering,
    so it keeps the defensive monotonic scan before using the streaming merge.
    """
    staged = tmp_path / "external_combine"
    staged.mkdir()
    (staged / "authservice.csv").write_text(
        "timestamp,requests\n"
        "2026-03-10 00:00:00,1\n"
        "2026-03-10 00:01:00,2\n",
        encoding="utf-8",
    )
    (staged / "database.csv").write_text(
        "timestamp,queries\n"
        "2026-03-10 00:00:00,3\n"
        "2026-03-10 00:01:00,4\n",
        encoding="utf-8",
    )
    scanned = []

    def record_prescan(path):
        scanned.append(Path(path).name)
        return True

    monkeypatch.setattr(amc, "_wide_component_rows_are_monotonic", record_prescan)

    amc.main([
        "combine", str(staged),
        "--components", "authservice,database",
    ])

    assert set(scanned) == {"authservice.csv", "database.csv"}
    assert (staged / "combined_metrics_unified.csv").exists()


# ---------------------------------------------------------------------------
# Phase 5: long-form combined_metrics_unified.csv with dimensions.
#
# When the per-component CSVs carry the multi-instance dimension prefix
# (``--instances-per-component N > 1``), the combine step switches from
# the wide ``timestamp,component_a_m0,component_a_m1,...`` layout to the
# same 10-column long layout the gauges.csv writer emits. Empty cells
# from drops are absent (long form encodes "this measurement was emitted"
# explicitly via row presence). N=1 keeps today's wide layout byte-
# identically, guarded by the existing tests above.
# ---------------------------------------------------------------------------

N3_COMBINED_ONE_DAY_HASH = (
    "511f455075c8f82ab765dea783230a5a23404607958c4b9da93bcb6005368c5c"
)




@pytest.fixture(scope="module")
def n3_one_day_combine_run(amc, n3_one_day_dataset_dir, tmp_path_factory):
    """Run ``combine_logs`` against the shared session-scoped N=3
    dataset (per-component CSVs generated once in ``conftest.py``).
    Avoids re-running the ~25-second N=3 generation pass per test
    module — ``combine_logs`` is a pure function of the per-component
    CSV bytes, so the locked golden hash holds byte-identically.

    Materializes the per-component CSVs as **hardlinks** into a
    module-scoped temp directory rather than copying them, so we
    don't double the ~1.3 GB disk footprint of the shared dataset.
    The hardlinked entries appear as normal files to every reader
    in this module, and ``combine_logs``'s autodiscovery (the
    ``components=None`` call below) walks them in sorted order — the
    same input the original combined-emission invocation produced. The
    new ``combined_metrics_unified.csv`` writes into this module's
    temp dir; the shared dataset is read-only as far as this fixture
    is concerned. Hardlinks require the temp dir on the same
    filesystem as the dataset; pytest's ``tmp_path_factory`` honors
    that (both sit under the same ``pytest-of-<user>`` root)."""
    out = tmp_path_factory.mktemp("ver148_n3_one_day_combine")
    for src in n3_one_day_dataset_dir.iterdir():
        os.link(src, out / src.name)
    amc.combine_logs(out)
    return SimpleNamespace(out_dir=out, stderr="")


def test_n3_combined_has_long_form_header(n3_one_day_combine_run):
    """With ``--instances-per-component 3 --emit ...,combined`` the unified CSV
    switches from the wide layout to the long
    ``timestamp,component,id,host,pod,az,region,tenant,metric,value``
    layout. The wide layout cannot represent N instances per metric
    without exploding the column count to N×M, so long form is the only
    sound choice for dimensioned input."""
    path = n3_one_day_combine_run.out_dir / "combined_metrics_unified.csv"
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
    expected = [
        "timestamp", "component", "id", "host", "pod", "az",
        "region", "tenant", "metric", "value",
    ]
    assert header == expected, (
        f"N=3 combined_metrics_unified.csv header must be the long form; "
        f"got {header}"
    )


def test_n3_combined_byte_identical_one_day(n3_one_day_combine_run):
    """Lock the N=3 1-day combined_metrics_unified.csv bytes. The long-
    form combine consumes the same per-(component, instance) iterators
    and tie-break order as ``write_gauges_csv``, so this hash captures
    the same coverage on the combine writer side."""
    path = n3_one_day_combine_run.out_dir / "combined_metrics_unified.csv"
    actual = sha256_path(path)
    assert actual == N3_COMBINED_ONE_DAY_HASH, (
        f"N=3 combined drifted from locked 1-day hash. "
        f"expected={N3_COMBINED_ONE_DAY_HASH} actual={actual}"
    )


def test_n3_combined_dimension_values_match_per_component_csvs(
    n3_one_day_combine_run, amc,
):
    """Every (component, id, host, pod, az, region, tenant) tuple in the
    combined long-form CSV must appear in the corresponding per-component
    CSV. Guards against a regression in the per-(component, instance)
    iterator key construction inside ``_write_combined_long_form``."""
    out_dir = n3_one_day_combine_run.out_dir
    path = out_dir / "combined_metrics_unified.csv"
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        combined_tuples = {
            (r["component"], r["id"], r["host"], r["pod"], r["az"],
             r["region"], r["tenant"])
            for r in reader
        }
    expected: set[tuple[str, ...]] = set()
    for component in amc.COMPONENTS:
        comp_path = out_dir / f"{component}.csv"
        if not comp_path.exists():
            continue
        with open(comp_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                expected.add(
                    (component, row["id"], row["host"], row["pod"],
                     row["az"], row["region"], row["tenant"])
                )
    assert len(expected) > 0
    assert combined_tuples == expected, (
        f"combined long-form dimension tuples must match per-component "
        f"CSVs exactly (missing={len(expected - combined_tuples)}, "
        f"extra={len(combined_tuples - expected)})"
    )


def test_n3_combined_chronological_order(n3_one_day_combine_run, amc):
    """Long-form combine rows must be in non-decreasing timestamp order —
    the same merge contract as gauges.csv."""
    path = n3_one_day_combine_run.out_dir / "combined_metrics_unified.csv"
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        prev_dt = None
        for row in reader:
            dt = amc._parse_csv_timestamp(row["timestamp"])
            if prev_dt is not None:
                assert dt >= prev_dt, (
                    "combined_metrics_unified.csv long form must be in "
                    "non-decreasing timestamp order"
                )
            prev_dt = dt


def test_n3_combined_no_empty_value_cells(n3_one_day_combine_run):
    """The long form encodes presence via row existence; dropped cells
    must not appear as rows with ``value=""``. Guards against a future
    refactor that forgets the ``if raw == "": continue`` skip in
    ``_write_combined_long_form``."""
    path = n3_one_day_combine_run.out_dir / "combined_metrics_unified.csv"
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            assert row["value"] != "", (
                "long-form combine rows must never have an empty value; "
                "dropped cells should be omitted entirely"
            )

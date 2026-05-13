"""Combine-step integration: autodiscovery, llm_analytics inclusion,
row preservation, and the synthetic-extra-component case.
"""

import csv
import shutil

import pytest

from conftest import COMPONENTS


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
    CSVs. With the per-component drop_rate at 0.05% and 6+ components, the chance
    every component drops the same second is effectively zero, so the union is
    essentially every second in the day. No llm_analytics data is lost.
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


def test_combine_only_cli_produces_unified_csv(amc, one_day_run_a, tmp_path):
    """End-to-end ``--combine-only`` CLI run produces the unified file against
    an existing --output-dir without re-generating component CSVs.
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

    amc.main(["--combine-only", "--output-dir", str(staged)])

    assert combined.exists(), "combine-only did not write the unified CSV"
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
            f"--combine-only rewrote source file {name}"
        )

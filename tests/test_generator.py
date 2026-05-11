import csv
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import COMPONENT_FIELDS, SCRIPT_PATH, declared_specs


COMPONENTS = list(COMPONENT_FIELDS.keys())


def _count_lines(path: Path) -> int:
    with open(path, "rb") as f:
        return sum(1 for _ in f)


def _count_blank_lines(path: Path) -> int:
    with open(path) as f:
        return sum(1 for line in f if line.strip() == "")


def _read_manifest(out_dir: Path):
    with open(out_dir / "anomalies.csv") as f:
        return list(csv.DictReader(f))


def _read_component_rows(out_dir: Path, component: str):
    """Return a dict mapping timestamp -> raw row (list[str]). Blank lines are skipped."""
    rows = {}
    with open(out_dir / f"{component}.csv") as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            if not row:
                continue
            rows[row[0]] = row
    return rows, header


def test_determinism_byte_identical(one_day_run_a, one_day_run_b):
    """Two seed=42 runs into separate dirs must produce byte-identical CSVs."""
    files = [f"{c}.csv" for c in COMPONENTS] + ["anomalies.csv"]
    for name in files:
        a = (one_day_run_a.out_dir / name).read_bytes()
        b = (one_day_run_b.out_dir / name).read_bytes()
        assert a == b, f"{name} differs between two seed=42 runs"


def test_row_count_one_day(amc, one_day_run_a):
    """Each component CSV has exactly TOTAL_SECONDS + 1 lines (header + N data-or-blank rows)."""
    expected = amc.SECONDS_PER_DAY * 1 + 1
    for component in COMPONENTS:
        path = one_day_run_a.out_dir / f"{component}.csv"
        assert _count_lines(path) == expected, f"{component}: expected {expected} lines"


def test_row_count_seven_day(amc, seven_day_run):
    """7-day runs scale linearly to 7 * SECONDS_PER_DAY + 1 lines per component."""
    expected = amc.SECONDS_PER_DAY * 7 + 1
    for component in COMPONENTS:
        path = seven_day_run.out_dir / f"{component}.csv"
        assert _count_lines(path) == expected, f"{component}: expected {expected} lines"


def test_manifest_csv_cross_check(one_day_run_a):
    """Every manifest entry has a corresponding non-empty CSV row at that timestamp.

    Gates VER-5 (manifest-vs-CSV drop inconsistency). With seed=42 and the default
    drop_rate, no anomaly row should be coincidentally dropped — but the bug means
    any seed where they collide silently desyncs the manifest from the data.
    """
    manifest = _read_manifest(one_day_run_a.out_dir)
    assert manifest, "Expected at least one manifest entry for a 1-day run"

    rows_by_component = {}
    headers_by_component = {}
    for c in COMPONENTS:
        rows, header = _read_component_rows(one_day_run_a.out_dir, c)
        rows_by_component[c] = rows
        headers_by_component[c] = header

    missing = []
    for entry in manifest:
        rows = rows_by_component[entry["component"]]
        header = headers_by_component[entry["component"]]
        ts = entry["timestamp"]
        row = rows.get(ts)
        if row is None:
            missing.append((entry["component"], ts, entry["metric"], "row dropped"))
            continue
        metric_idx = header.index(entry["metric"])
        if row[metric_idx] == "":
            missing.append((entry["component"], ts, entry["metric"], "empty cell"))
    assert not missing, f"Manifest entries without backing CSV rows: {missing}"


def test_spec_coverage_one_day(amc, one_day_run_a):
    """At duration=1 day, every in-range spec produces >=1 manifest entry; out-of-range specs do not.

    Also gates VER-4's loud-failure assertion: out-of-range specs must trigger a
    stderr WARNING naming the duration required to reach them.
    """
    manifest = _read_manifest(one_day_run_a.out_dir)
    seen = {(e["component"], e["metric"], e["description"]) for e in manifest}

    in_range_missing = []
    out_of_range_leaked = []
    has_out_of_range = False
    for component, offset, metric, description in declared_specs(amc):
        key = (component, metric, description)
        if offset < amc.SECONDS_PER_DAY:
            if key not in seen:
                in_range_missing.append((component, offset, metric, description))
        else:
            has_out_of_range = True
            if key in seen:
                out_of_range_leaked.append((component, offset, metric, description))

    assert not in_range_missing, f"In-range specs missing from 1-day manifest: {in_range_missing}"
    assert not out_of_range_leaked, f"Out-of-range specs leaked into 1-day manifest: {out_of_range_leaked}"

    if has_out_of_range:
        assert "WARNING" in one_day_run_a.stderr
        assert "--duration-days 7" in one_day_run_a.stderr, (
            f"Expected loud failure naming --duration-days 7; got:\n{one_day_run_a.stderr}"
        )


def test_spec_coverage_seven_day(amc, seven_day_run):
    """At duration=7 days, every declared spec produces >=1 manifest entry."""
    manifest = _read_manifest(seven_day_run.out_dir)
    seen = {(e["component"], e["metric"], e["description"]) for e in manifest}

    missing = [
        (c, o, m, d)
        for (c, o, m, d) in declared_specs(amc)
        if (c, m, d) not in seen
    ]
    assert not missing, f"Specs missing from 7-day manifest: {missing}"


def test_drop_rate_within_tolerance(amc, one_day_run_a):
    """Blank-line count across all component CSVs is within ~3σ of drop_rate * total_seconds."""
    drop_rate = amc.DEFAULT_DROP_RATE
    n_per_component = amc.SECONDS_PER_DAY
    total_observed = sum(_count_blank_lines(one_day_run_a.out_dir / f"{c}.csv") for c in COMPONENTS)
    n_total = n_per_component * len(COMPONENTS)
    mean = drop_rate * n_total
    std = math.sqrt(n_total * drop_rate * (1 - drop_rate))
    tolerance = 3 * std

    assert mean - tolerance <= total_observed <= mean + tolerance, (
        f"Observed {total_observed} dropped lines; expected {mean:.1f} ± {tolerance:.1f} "
        f"(drop_rate={drop_rate}, n={n_total})"
    )


def test_import_does_not_run_generation(tmp_path):
    """Importing the module in a fresh interpreter must not trigger main() / generation."""
    script = (
        "import importlib.util, os, sys\n"
        "spec = importlib.util.spec_from_file_location('amc', os.environ['SCRIPT_PATH'])\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "assert m.anomalies == [], 'anomalies registry populated on import'\n"
        "assert m.cascading_anomalies == {}, 'cascading registry populated on import'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "SCRIPT_PATH": str(SCRIPT_PATH)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    # No iot_logs default dir should be created by import alone.
    assert not (tmp_path / "iot_logs").exists()

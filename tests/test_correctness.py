"""Correctness invariants: row counts, manifest/CSV coherence, spec coverage,
value-band sanity, and schema-driven plumbing introduced by VER-4/5/8.
"""

import csv
import datetime
import importlib.util
import math
from pathlib import Path

import pytest

from conftest import (
    COMPONENTS,
    SCRIPT_PATH,
    count_blank_lines,
    count_lines,
    declared_specs,
    natural_band,
    primary_spec_lookup,
    read_component_rows,
    read_manifest,
    run_capture,
)


# ------------------------------------------------------------------
# Row count / drop-rate / no-empty-rows
# ------------------------------------------------------------------
def _assert_row_count_within_tolerance(amc, run, days):
    """Each component CSV has 1 header + (total_seconds - dropped) data rows, with
    dropped count within 5σ of drop_rate * total_seconds (post VER-5: dropped
    seconds emit no row at all, so file line count == 1 + unique-timestamp rows).
    """
    drop_rate = amc.DEFAULT_DROP_RATE
    n = amc.SECONDS_PER_DAY * days
    mean = drop_rate * n
    std = math.sqrt(n * drop_rate * (1 - drop_rate))
    tolerance = 5 * std

    for component in COMPONENTS:
        path = run.out_dir / f"{component}.csv"
        lines = count_lines(path)
        rows, _ = read_component_rows(run.out_dir, component)
        dropped = n - len(rows)
        assert lines == 1 + len(rows), (
            f"{component}: lines={lines} but unique data rows={len(rows)} "
            f"(blank lines or duplicate timestamps present)"
        )
        assert mean - tolerance <= dropped <= mean + tolerance, (
            f"{component}: dropped={dropped} outside {mean:.1f} ± {tolerance:.1f}"
        )


def test_row_count_one_day(amc, one_day_run_a):
    _assert_row_count_within_tolerance(amc, one_day_run_a, days=1)


def test_row_count_seven_day(amc, seven_day_run):
    _assert_row_count_within_tolerance(amc, seven_day_run, days=7)


def test_drop_rate_within_tolerance(amc, one_day_run_a):
    """Missing-timestamp count across all components is within 3σ of drop_rate * n."""
    drop_rate = amc.DEFAULT_DROP_RATE
    n_per = amc.SECONDS_PER_DAY
    observed = sum(n_per - len(read_component_rows(one_day_run_a.out_dir, c)[0]) for c in COMPONENTS)
    n_total = n_per * len(COMPONENTS)
    mean = drop_rate * n_total
    std = math.sqrt(n_total * drop_rate * (1 - drop_rate))
    tol = 3 * std
    assert mean - tol <= observed <= mean + tol, (
        f"Observed {observed} missing timestamps; expected {mean:.1f} ± {tol:.1f}"
    )


def test_no_empty_csv_records(one_day_run_a, seven_day_run):
    """VER-5 AC: dropped samples produce no CSV record at all. Every emitted row
    has a timestamp + at least one non-empty data cell.
    """
    for run in (one_day_run_a, seven_day_run):
        for component in COMPONENTS:
            path = run.out_dir / f"{component}.csv"
            assert count_blank_lines(path) == 0, f"{path.name}: blank lines present"
            with open(path) as f:
                reader = csv.reader(f)
                next(reader)
                for row in reader:
                    assert row, f"{path.name}: empty row"
                    assert any(cell != "" for cell in row[1:]), (
                        f"{path.name}: row {row[0]} has all-empty data cells"
                    )


# ------------------------------------------------------------------
# Manifest ↔ CSV coherence (VER-5 joint gate)
# ------------------------------------------------------------------
CROSS_CHECK_SEEDS = [1, 7, 42, 99]


@pytest.mark.parametrize("seed", CROSS_CHECK_SEEDS)
def test_manifest_csv_cross_check(amc, tmp_path, seed):
    """Multi-seed: every (component, metric, timestamp) in anomalies.csv maps to
    a non-empty CSV cell. The pre-VER-5 bug silently desyncs whenever an anomaly
    second happens to coincide with a drop — passes by coincidence at a single
    seed, so the multi-seed sweep is the real gate.
    """
    out = tmp_path / f"seed_{seed}"
    out.mkdir()
    run = run_capture(amc, out, days=1, seed=seed)

    manifest = read_manifest(run.out_dir)
    assert manifest, f"seed={seed}: expected at least one manifest entry"

    rows_by_c = {}
    headers_by_c = {}
    for c in COMPONENTS:
        rows, header = read_component_rows(run.out_dir, c)
        rows_by_c[c] = rows
        headers_by_c[c] = header

    missing = []
    for entry in manifest:
        rows = rows_by_c[entry["component"]]
        header = headers_by_c[entry["component"]]
        row = rows.get(entry["timestamp"])
        if row is None:
            missing.append((entry["component"], entry["timestamp"], entry["metric"], "row dropped"))
            continue
        if row[header.index(entry["metric"])] == "":
            missing.append((entry["component"], entry["timestamp"], entry["metric"], "empty cell"))
    assert not missing, f"seed={seed}: manifest entries without backing CSV rows: {missing}"


# ------------------------------------------------------------------
# Spec coverage (VER-4 loud-failure + multi-day reachability)
# ------------------------------------------------------------------
def test_spec_coverage_one_day(amc, one_day_run_a):
    """Every in-range spec appears in the 1-day manifest, out-of-range ones do not,
    and the stderr WARNING names the duration required to reach them (VER-4 AC).
    """
    seen = {(e["component"], e["metric"], e["description"]) for e in read_manifest(one_day_run_a.out_dir)}
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
    seen = {(e["component"], e["metric"], e["description"]) for e in read_manifest(seven_day_run.out_dir)}
    missing = [
        (c, o, m, d)
        for (c, o, m, d) in declared_specs(amc)
        if (c, m, d) not in seen
    ]
    assert not missing, f"Specs missing from 7-day manifest: {missing}"


# ------------------------------------------------------------------
# Value-range sanity (per metric × per component)
# ------------------------------------------------------------------
def test_value_range_sanity(amc, one_day_run_a):
    """Natural rows for every metric fall inside the schema-derived plausible band.

    Excludes timestamps that appear in the manifest (those are anomalies and are
    expected out-of-band). Uses an 8σ envelope around the metric's daily shape so
    a healthy run won't trip; a regression that swaps a metric's base or std would.
    """
    manifest = read_manifest(one_day_run_a.out_dir)
    anomaly_ts_by_component = {}
    for e in manifest:
        anomaly_ts_by_component.setdefault(e["component"], set()).add(e["timestamp"])

    failures = []
    for component, specs in amc.COMPONENTS.items():
        rows, header = read_component_rows(one_day_run_a.out_dir, component)
        skip_ts = anomaly_ts_by_component.get(component, set())
        for col_idx, mspec in enumerate(specs):
            field_idx = header.index(mspec.name)
            lo, hi = natural_band(amc, mspec, amc.SECONDS_PER_DAY)
            out_of_band = 0
            sample_offender = None
            for ts, row in rows.items():
                if ts in skip_ts:
                    continue
                v = float(row[field_idx])
                if v < lo or v > hi:
                    out_of_band += 1
                    if sample_offender is None:
                        sample_offender = (ts, v)
            if out_of_band:
                failures.append((component, mspec.name, lo, hi, out_of_band, sample_offender))
    assert not failures, f"Metrics outside 8σ natural band: {failures}"


# ------------------------------------------------------------------
# Anomaly value coherence (catches wire-to-wrong-field)
# ------------------------------------------------------------------
def test_anomalies_match_declared_value(amc, seven_day_run):
    """For each primary (non-cascade) anomaly, the CSV cell at the declared
    (component, metric, timestamp) matches the spec's generator output. Cascade
    generators draw from numpy random state so we can't reproduce them bit-exact;
    the cross-check test above already proves cascade anomalies land in a cell.

    Catches "anomaly generator wired to wrong field" regressions: if the anomaly
    were written to a sibling metric, the declared column would carry its natural
    value instead of the injected constant.
    """
    lookup = primary_spec_lookup(amc)
    manifest = read_manifest(seven_day_run.out_dir)

    rows_by_c = {}
    headers_by_c = {}
    for c in COMPONENTS:
        rows, header = read_component_rows(seven_day_run.out_dir, c)
        rows_by_c[c] = rows
        headers_by_c[c] = header

    checked = 0
    failures = []
    for entry in manifest:
        key = (entry["component"], entry["metric"], entry["description"])
        spec = lookup.get(key)
        if spec is None:
            continue  # cascade — covered by manifest/CSV cross-check
        ts = datetime.datetime.strptime(entry["timestamp"], "%Y-%m-%d %H:%M:%S")
        header = headers_by_c[entry["component"]]
        col_idx = header.index(entry["metric"])
        spec_idx = col_idx - 1  # value generators are indexed without the timestamp column
        row = rows_by_c[entry["component"]].get(entry["timestamp"])
        assert row is not None, f"missing CSV row for manifest entry {entry}"
        actual = float(row[col_idx])
        expected = round(float(spec["generator"](ts, spec_idx)), 3)
        if actual != expected:
            failures.append((entry, expected, actual))
        checked += 1

    assert checked > 0, "expected at least one primary anomaly in the 7-day manifest"
    assert not failures, (
        f"Primary anomalies wired to wrong cell: {failures[:5]}"
        + (f" (+{len(failures) - 5} more)" if len(failures) > 5 else "")
    )


# ------------------------------------------------------------------
# Schema / refactor invariants (VER-8)
# ------------------------------------------------------------------
def test_schema_is_single_source_of_truth(amc, one_day_run_a):
    """COMPONENTS drives the CSV columns — adding a metric edits exactly one list."""
    for component, specs in amc.COMPONENTS.items():
        _, header = read_component_rows(one_day_run_a.out_dir, component)
        expected = ["timestamp"] + [s.name for s in specs]
        assert header == expected, f"{component}: header {header} != schema {expected}"


def test_no_legacy_va_generators(amc):
    """The va_* generator ladder is gone; the schema replaces it."""
    leftovers = [name for name in dir(amc) if name.startswith("va_")]
    assert not leftovers, f"Legacy va_* generators still present: {leftovers}"


def test_duplicate_anomaly_specs_raise(tmp_path):
    """Two specs with the same (metric, time_offset) must fail loudly."""
    spec = importlib.util.spec_from_file_location("amc_dup", SCRIPT_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.anoms_auth.append({
        "time_offset": 2 * 3600 + 15 * 60,
        "metric": "error_rate",
        "description": "Duplicate (test injection)",
        "generator": lambda ts, idx: 0.99,
    })
    with pytest.raises(ValueError, match="Duplicate anomaly specs"):
        m.main(["--seed", "42", "--duration-days", "1", "--output-dir", str(tmp_path)])


def test_active_sessions_has_daily_variation(amc, one_day_run_a):
    """active_sessions must sweep the full ±20 daily-sine amplitude — the legacy
    ``np.sin(0 + ts.second / 60) * 20`` cycled within each minute and flattened
    daily seasonality.
    """
    rows, header = read_component_rows(one_day_run_a.out_dir, "authservice")
    idx = header.index("active_sessions")
    values = [float(r[idx]) for r in rows.values() if r[idx]]
    spread = max(values) - min(values)
    assert spread > 35, (
        f"active_sessions spread {spread:.2f} too small for a daily sine; "
        f"values in [{min(values):.2f}, {max(values):.2f}]"
    )

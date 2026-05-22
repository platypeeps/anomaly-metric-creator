"""Correctness invariants: row counts, manifest/CSV coherence, spec coverage,
value-band sanity, and schema-driven plumbing.
"""

import csv
import datetime
import hashlib
import importlib.util
import math
from pathlib import Path

import numpy as np
import pytest

from conftest import (
    COMPONENTS,
    DEFAULT_METRIC_COUNT,
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
    dropped count within 5σ of drop_rate * total_seconds. Dropped seconds emit
    no row at all, so file line count == 1 + unique-timestamp rows.
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
    """Dropped samples produce no CSV record at all. Every emitted row has a
    timestamp + at least one non-empty data cell.
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
# Manifest ↔ CSV coherence (joint gate)
# ------------------------------------------------------------------
CROSS_CHECK_SEEDS = [1, 7, 42, 99]


@pytest.mark.parametrize("seed", CROSS_CHECK_SEEDS)
def test_manifest_csv_cross_check(amc, tmp_path, seed):
    """Multi-seed: every (component, metric, timestamp) in anomalies.csv maps to
    a non-empty CSV cell. A naive implementation silently desyncs whenever an
    anomaly second happens to coincide with a drop — it passes by coincidence
    at a single seed, so the multi-seed sweep is the real gate.
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
# Spec coverage (loud-failure + multi-day reachability)
# ------------------------------------------------------------------
def test_spec_coverage_one_day(amc, one_day_run_a):
    """Every in-range spec from active (medium-severity, 1-day) scenarios appears in
    the 1-day manifest, out-of-scope scenarios' specs do not leak in, and the stderr
    WARNING names a scenario that requires a larger ``--duration-days`` value.
    """
    seen = {(e["component"], e["metric"], e["description"]) for e in read_manifest(one_day_run_a.out_dir)}

    # 1) Active scenarios for this run must contribute all their in-range specs.
    #    declared_specs(days=1, signal_level="medium") drops out-of-scope scenarios
    #    (multi-day or high-severity) so we only assert on what the run could emit.
    in_range_missing = [
        (c, o, m, d)
        for (c, o, m, d) in declared_specs(amc, days=1, signal_level="medium")
        if o < amc.SECONDS_PER_DAY and (c, m, d) not in seen
    ]
    assert not in_range_missing, f"In-range specs missing from 1-day manifest: {in_range_missing}"

    # 2) Out-of-scope scenarios (gated by signal_level or days_required) must not
    #    leak into the manifest. Using the *unfiltered* declared list and excluding
    #    the active subset gives us specs that should be absent.
    active_keys = {
        (c, m, d) for (c, _, m, d) in declared_specs(amc, days=1, signal_level="medium")
    }
    out_of_scope_leaked = [
        (c, o, m, d)
        for (c, o, m, d) in declared_specs(amc)
        if (c, m, d) not in active_keys and (c, m, d) in seen
    ]
    assert not out_of_scope_leaked, (
        f"Out-of-scope specs leaked into 1-day manifest: {out_of_scope_leaked}"
    )

    # 3) At least one scenario in the unfiltered catalog needs --duration-days >= 2
    #    (every multi-day scenario), so the run must emit the corresponding
    #    scenario-gate WARNING on stderr. We don't pin a specific minimum day because
    #    different scenarios advertise different values; we just require the warning
    #    template fired at least once.
    multi_day_present = any(
        amc.SCENARIOS[slug].days_required > 1
        for slug in amc.SCENARIOS
    )
    if multi_day_present:
        assert "WARNING: scenario" in one_day_run_a.stderr, (
            "Expected at least one scenario-gate WARNING on a 1-day run "
            f"(multi-day scenarios should be soft-skipped); got:\n{one_day_run_a.stderr}"
        )
        assert "requires --duration-days" in one_day_run_a.stderr, (
            f"Expected --duration-days requirement in WARNING; got:\n{one_day_run_a.stderr}"
        )


def test_spec_coverage_seven_day(amc, seven_day_run):
    """At duration=7 days, every declared spec produces >=1 manifest entry."""
    seen = {(e["component"], e["metric"], e["description"]) for e in read_manifest(seven_day_run.out_dir)}
    missing = [
        (c, o, m, d)
        for (c, o, m, d) in declared_specs(amc, days=7, signal_level="medium")
        if (c, m, d) not in seen
    ]
    assert not missing, f"Specs missing from 7-day manifest: {missing}"


# ------------------------------------------------------------------
# Value-range sanity (per metric × per component)
# ------------------------------------------------------------------
def _assert_value_band_sanity(amc, run, emitted_count):
    """Each emitted metric column stays inside an 8σ band of its MetricSpec.

    ``emitted_count(component) -> int`` returns how many metrics that
    component's CSV should expose for this run. Anomaly rows (and full
    anomaly spans) are excluded from the band check.
    """
    manifest = read_manifest(run.out_dir)
    lookup = primary_spec_lookup(amc)
    # {component: {metric: set(timestamps_to_skip)}}
    skip_by_cm = {}
    for e in manifest:
        comp = e["component"]
        met = e["metric"]
        ts_start_str = e["timestamp"]
        ts_start = datetime.datetime.strptime(ts_start_str, "%Y-%m-%d %H:%M:%S")

        # Always skip the start timestamp
        skip_by_cm.setdefault(comp, {}).setdefault(met, set()).add(ts_start_str)

        # If it's a primary span anomaly, skip the entire duration
        spec = lookup.get((comp, met, e["description"]))
        if spec and int(spec.get("duration_seconds", 0) or 0) > 0:
            dur = int(spec["duration_seconds"])
            for offset in range(1, dur):
                ts_skip = (ts_start + datetime.timedelta(seconds=offset)).strftime("%Y-%m-%d %H:%M:%S")
                skip_by_cm[comp][met].add(ts_skip)

    failures = []
    for component, specs in amc.COMPONENTS.items():
        rows, header = read_component_rows(run.out_dir, component)
        emitted_specs = specs[: emitted_count(component)]
        for mspec in emitted_specs:
            if (component, mspec.name) in amc.DERIVED_METRICS:
                continue
            field_idx = header.index(mspec.name)
            skip_ts = skip_by_cm.get(component, {}).get(mspec.name, set())
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


def test_value_range_sanity(amc, one_day_independent_run):
    """Independent-mode metrics stay inside their 8σ natural band.

    The natural band is derived from each MetricSpec's ``base``/``std``/
    ``multiplier`` — it characterizes the independent Gaussian baseline.
    After VER-156 phase 6 the default mode is ``realistic``, which
    intentionally drives downstream load-metric and latency baselines
    outside that natural band via topology coupling and saturation
    feedback. We therefore pin this band check to ``--topology-mode
    independent`` (the deprecation alias whose retirement is tracked
    with the alias itself, post-VER-141 phase 9); realistic-mode
    behaviour is validated by the dedicated coupling/saturation tests
    in ``tests/test_topology_*``."""
    _assert_value_band_sanity(
        amc,
        one_day_independent_run,
        emitted_count=lambda c: DEFAULT_METRIC_COUNT[c],
    )


def test_value_range_sanity_full_catalog(amc, one_day_full_metrics_independent_run):
    """With --metrics-per-component 10 every supplemental metric is exercised
    too. Without this gate, a regression in a supplemental MetricSpec's base /
    std / multiplier would slip through the default-only check. Pinned to
    ``--topology-mode independent`` for the same reason as
    ``test_value_range_sanity`` above."""
    _assert_value_band_sanity(
        amc,
        one_day_full_metrics_independent_run,
        emitted_count=lambda c: amc.MAX_METRICS_PER_COMPONENT,
    )


def _assert_clip_min_invariant(amc, run, emitted_count):
    """Every emitted value of a ``clip_min``-bearing metric stays at or above
    its declared floor — across natural rows AND anomaly rows.

    The existing 8σ band test excludes anomaly rows and uses a soft band, so
    a future anomaly generator that drove a clip_min-declared metric below
    its floor would slip through. This test treats the MetricSpec.clip_min
    field as a hard contract on the emitted CSV: declaring it is a promise
    that no row, anomalous or otherwise, breaches it.
    """
    failures = []
    for component, specs in amc.COMPONENTS.items():
        clip_specs = [
            s for s in specs[: emitted_count(component)]
            if s.clip_min is not None
        ]
        if not clip_specs:
            continue
        rows, header = read_component_rows(run.out_dir, component)
        for spec in clip_specs:
            field_idx = header.index(spec.name)
            offenders = 0
            sample = None
            min_seen = float("inf")
            for ts, row in rows.items():
                v = float(row[field_idx])
                if v < spec.clip_min:
                    offenders += 1
                    if v < min_seen:
                        min_seen = v
                        sample = (ts, v)
            if offenders:
                failures.append(
                    (component, spec.name, spec.clip_min, offenders, min_seen, sample)
                )
    assert not failures, (
        "Metrics breached their declared clip_min floor in emitted CSV "
        f"(component, metric, clip_min, count, min, sample): {failures}"
    )


def test_clip_min_invariant_one_day(amc, one_day_run_a):
    """Default 1-day run: no row breaches any MetricSpec.clip_min floor."""
    _assert_clip_min_invariant(
        amc, one_day_run_a, emitted_count=lambda c: DEFAULT_METRIC_COUNT[c]
    )


def test_clip_min_invariant_seven_day(amc, seven_day_run):
    """Default 7-day run: the longer window exercises every multi-day spec
    that could otherwise drive a clip_min-declared metric below its floor.
    Lock-step with the 1-day case so a regression surfaces at either
    duration."""
    _assert_clip_min_invariant(
        amc, seven_day_run, emitted_count=lambda c: DEFAULT_METRIC_COUNT[c]
    )


def test_clip_min_invariant_full_catalog(amc, one_day_full_metrics_run):
    """With --metrics-per-component 10 every supplemental clip_min-declared
    metric is also covered, so a supplemental-zone regression cannot hide
    behind the default-only check."""
    _assert_clip_min_invariant(
        amc,
        one_day_full_metrics_run,
        emitted_count=lambda c: amc.MAX_METRICS_PER_COMPONENT,
    )


# ------------------------------------------------------------------
# Derived metric consistency (catches drift in the derivation pass)
# ------------------------------------------------------------------
def _assert_cacheservice_hit_ratio_consistent(run):
    """``cacheservice.hit_ratio`` must equal ``100 * cache_hits /
    (cache_hits + cache_misses)`` on every emitted row, with the zero-
    denominator rule (``hit_ratio == 0`` when both counters are zero).
    Tolerance accounts for the post-derivation 3-decimal rounding of the
    source columns: small rounding error in hits/misses can shift the
    computed ratio by < 0.01.
    """
    rows, header = read_component_rows(run.out_dir, "cacheservice")
    hits_idx = header.index("cache_hits")
    misses_idx = header.index("cache_misses")
    ratio_idx = header.index("hit_ratio")

    failures = []
    for ts, row in rows.items():
        hits = float(row[hits_idx])
        misses = float(row[misses_idx])
        ratio = float(row[ratio_idx])
        assert hits >= 0.0, f"{ts}: cache_hits={hits} negative"
        assert misses >= 0.0, f"{ts}: cache_misses={misses} negative"
        assert 0.0 <= ratio <= 100.0, f"{ts}: hit_ratio={ratio} out of [0,100]"
        denom = hits + misses
        expected = 0.0 if denom == 0 else 100.0 * hits / denom
        if abs(ratio - expected) > 0.01:
            failures.append((ts, hits, misses, ratio, expected))
    assert not failures, (
        f"cacheservice.hit_ratio diverges from 100*hits/(hits+misses) on "
        f"{len(failures)} rows; first: {failures[0]}"
    )


def test_derived_hit_ratio_consistency_one_day(one_day_run_a):
    """Locks the derived-metric invariant on the default 1-day run: every
    cacheservice row's hit_ratio agrees with its hits/misses counters. A
    regression in the derivation pass (wrong column index, skipped recompute,
    forgotten zero-denominator rule, or a new anomaly that drives hits/misses
    negative) would land here loudly rather than silently shipping
    physically-inconsistent telemetry."""
    _assert_cacheservice_hit_ratio_consistent(one_day_run_a)


def test_derived_hit_ratio_consistency_seven_day(seven_day_run):
    """7-day variant: exercises every multi-day cacheservice anomaly spec
    that touches cache_misses/hit_ratio so the consistency invariant holds
    across the full anomaly catalog, not just the medium-severity 1-day
    subset."""
    _assert_cacheservice_hit_ratio_consistent(seven_day_run)


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
        if int(spec.get("duration_seconds", 0) or 0) > 0:
            # Span anomalies use shape-driven values rather than the generator
            # output at the start row. Per-shape value coverage lives in the
            # dedicated shape tests below.
            continue
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
# Schema / refactor invariants
# ------------------------------------------------------------------
def test_schema_is_single_source_of_truth(amc, one_day_run_a):
    """COMPONENTS drives the CSV columns — at default --metrics-per-component
    each component emits the first DEFAULT_METRIC_COUNT[name] metrics from its
    ordered MetricSpec list, preserving today's byte-for-byte CSV layout."""
    for component, specs in amc.COMPONENTS.items():
        _, header = read_component_rows(one_day_run_a.out_dir, component)
        limit = DEFAULT_METRIC_COUNT[component]
        expected = ["timestamp"] + [s.name for s in specs[:limit]]
        assert header == expected, f"{component}: header {header} != schema {expected}"


def test_no_legacy_va_generators(amc):
    """The va_* generator ladder is gone; the schema replaces it."""
    leftovers = [name for name in dir(amc) if name.startswith("va_")]
    assert not leftovers, f"Legacy va_* generators still present: {leftovers}"


def test_duplicate_anomaly_specs_raise(tmp_path):
    """Two specs with the same (metric, time_offset) must fail loudly.

    The session-scoped ``amc`` fixture is intentionally not used here:
    this test monkey-patches ``_apply_scenarios`` on the module, and
    sharing the session module would leak the patch into every other
    test that runs after it. The fresh module copy is the isolation
    boundary, so the VER-197 lint exempts the load. See
    ``tools/check_amc_module_load.py`` for the lint."""
    spec = importlib.util.spec_from_file_location("amc_dup", SCRIPT_PATH)  # noqa: amc-load
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # Inject a duplicate by patching _apply_scenarios to append an extra spec
    orig_apply = m._apply_scenarios
    def apply_with_dup(comp_anoms, cascade_reg, active):
        orig_apply(comp_anoms, cascade_reg, active)
        comp_anoms["authservice"].append({
            "time_offset": 2 * 3600 + 15 * 60,
            "metric": "error_rate",
            "description": "Duplicate (test injection)",
            "generator": lambda ts, idx: 0.99,
        })
    m._apply_scenarios = apply_with_dup
    with pytest.raises(ValueError, match="Overlapping anomaly specs"):
        m.main(["--seed", "42", "--duration-days", "1", "--output-dir", str(tmp_path)])


def test_unknown_primary_anomaly_metric_raises(tmp_path):
    """A typo in a primary spec metric must fail loudly, not be silently dropped
    by the metrics-per-component filter.

    A fresh module copy isolates the ``_apply_scenarios`` monkey-patch
    from the session-scoped ``amc`` fixture. The VER-197 lint exempts
    the load (``tools/check_amc_module_load.py``)."""
    spec = importlib.util.spec_from_file_location("amc_unknown_primary", SCRIPT_PATH)  # noqa: amc-load
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # Inject a typo primary via _apply_scenarios patch
    orig_apply = m._apply_scenarios
    def apply_with_typo(comp_anoms, cascade_reg, active):
        orig_apply(comp_anoms, cascade_reg, active)
        comp_anoms["authservice"].append({
            "time_offset": 7 * 3600,
            "metric": "not_a_real_metric",
            "description": "Typo (test injection)",
            "generator": lambda ts, idx: 0.0,
        })
    m._apply_scenarios = apply_with_typo
    with pytest.raises(ValueError, match="missing from COMPONENTS"):
        m.main(["--seed", "42", "--duration-days", "1", "--output-dir", str(tmp_path)])


def test_unknown_cascade_metric_raises(tmp_path):
    """A typo in a cascade metric must fail loudly. Without the typo-vs-trim
    distinction this would be silently swallowed by the filter.

    The test patches _apply_scenarios to inject the typo cascade after the
    registry walk, mirroring how register_cascade was tested pre-VER-104.
    A fresh module copy keeps the monkey-patch from leaking into the
    session-scoped ``amc`` fixture; the VER-197 lint exempts the load."""
    spec = importlib.util.spec_from_file_location("amc_unknown_cascade", SCRIPT_PATH)  # noqa: amc-load
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    original_apply = m._apply_scenarios

    def apply_with_typo(comp_anoms, cascade_reg, active):
        original_apply(comp_anoms, cascade_reg, active)
        m.register_cascade(
            "database",
            7 * 3600,
            "not_a_db_metric",
            "Typo cascade (test injection)",
            lambda ts, idx: 0.0,
            cascade_registry=cascade_reg,
        )

    m._apply_scenarios = apply_with_typo
    with pytest.raises(ValueError, match="missing from COMPONENTS"):
        m.main(["--seed", "42", "--duration-days", "1", "--output-dir", str(tmp_path)])


def test_duration_shape_ramp_linear_and_sine(amc, tmp_path):
    """Span anomalies should apply per-row shaped values across duration."""
    out = tmp_path / "shape_span"
    out.mkdir()
    specs = [amc.MetricSpec(name="m0", base=10.0, std=0.0)]
    anomaly_specs = [
        {
            "time_offset": 10,
            "duration_seconds": 4,
            "metric": "m0",
            "description": "linear ramp",
            "shape": "ramp_linear",
            "shape_params": {"start": 100.0, "end": 160.0},
            "generator": lambda ts, idx: 0.0,
        },
        {
            "time_offset": 20,
            "duration_seconds": 4,
            "metric": "m0",
            "description": "sine",
            "shape": "sine",
            "shape_params": {"period_s": 4.0, "amplitude": 10.0, "midline": 200.0},
            "generator": lambda ts, idx: 0.0,
        },
    ]

    ts_array, ts_strings = amc._build_timestamp_arrays(40, 1.0)
    amc.generate_component(
        "shape_component",
        specs,
        anomaly_specs,
        base_dir=out,
        total_seconds=40,
        drop_rate=0.0,
        interval=1.0,
        ts_array=ts_array,
        ts_strings=ts_strings,
        ctx=amc.RunContext(rng=np.random.RandomState(0)),
    )
    rows, header = read_component_rows(out, "shape_component")
    idx = header.index("m0")

    assert float(rows["2026-03-10 00:00:10"][idx]) == 100.0
    assert float(rows["2026-03-10 00:00:11"][idx]) == 115.0
    assert float(rows["2026-03-10 00:00:12"][idx]) == 130.0
    assert float(rows["2026-03-10 00:00:13"][idx]) == 145.0

    assert float(rows["2026-03-10 00:00:20"][idx]) == 200.0
    assert float(rows["2026-03-10 00:00:21"][idx]) == 210.0
    assert float(rows["2026-03-10 00:00:22"][idx]) == 200.0
    assert float(rows["2026-03-10 00:00:23"][idx]) == 190.0


def test_duration_step_passes_t_within_to_generator(amc, tmp_path):
    """Step spans call generator with row-local t when provided."""
    out = tmp_path / "step_span_t"
    out.mkdir()
    specs = [amc.MetricSpec(name="m0", base=0.0, std=0.0)]
    anomaly_specs = [
        {
            "time_offset": 5,
            "duration_seconds": 3,
            "metric": "m0",
            "description": "step span with t",
            "shape": "step",
            "generator": lambda ts, idx, t, s, rng: 100.0 + t,
        }
    ]
    ts_array, ts_strings = amc._build_timestamp_arrays(20, 1.0)
    amc.generate_component(
        "step_component",
        specs,
        anomaly_specs,
        base_dir=out,
        total_seconds=20,
        drop_rate=0.0,
        interval=1.0,
        ts_array=ts_array,
        ts_strings=ts_strings,
        ctx=amc.RunContext(rng=np.random.RandomState(0)),
    )
    rows, header = read_component_rows(out, "step_component")
    idx = header.index("m0")
    assert float(rows["2026-03-10 00:00:05"][idx]) == 100.0
    assert float(rows["2026-03-10 00:00:06"][idx]) == 101.0
    assert float(rows["2026-03-10 00:00:07"][idx]) == 102.0


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


def test_multiplier_scales_jitter_variance(amc):
    """Jitter is applied before multiplier so variance scales too."""
    n_rows = 20_000
    ts_array = np.array([np.datetime64(amc.START)] * n_rows)
    elapsed = np.arange(n_rows, dtype=np.float64)

    def stepped_multiplier(_ts, sec):
        return np.where(sec < (n_rows // 2), 1.0, 2.0)

    spec = amc.MetricSpec(name="m0", base=0.0, std=1.0, multiplier=stepped_multiplier)
    rng = np.random.RandomState(1234)
    col = amc._natural_column(spec, ts_array, elapsed, rng)

    first = col[: n_rows // 2]
    second = col[n_rows // 2 :]
    std_ratio = float(np.std(second) / np.std(first))
    assert 1.9 <= std_ratio <= 2.1, (
        f"expected post-shift std to be ~2x (got ratio={std_ratio:.3f})"
    )


# ------------------------------------------------------------------
# --interval-seconds sampling-density knob.
# ------------------------------------------------------------------
@pytest.fixture(scope="session")
def one_day_interval5_run(amc, tmp_path_factory):
    """1-day run at --interval-seconds 5 (17,280 rows per component)."""
    out = tmp_path_factory.mktemp("one_day_interval5")
    import io
    import sys as _sys
    args = [
        "--seed", "42",
        "--duration-days", "1",
        "--interval-seconds", "5",
        "--output-dir", str(out),
    ]
    stderr_buf = io.StringIO()
    real_stderr = _sys.stderr
    _sys.stderr = stderr_buf
    try:
        amc.main(args)
    finally:
        _sys.stderr = real_stderr
    from types import SimpleNamespace
    return SimpleNamespace(out_dir=out, stderr=stderr_buf.getvalue())


def test_interval_seconds_row_count(amc, one_day_interval5_run):
    """At --interval-seconds 5 for one day, each component should have
    floor(86400 / 5) = 17,280 rows minus the small drop-rate count."""
    drop_rate = amc.DEFAULT_DROP_RATE
    expected = amc.SECONDS_PER_DAY // 5  # 17,280
    mean_dropped = drop_rate * expected
    std = math.sqrt(expected * drop_rate * (1 - drop_rate))
    tolerance = 5 * std
    for component in COMPONENTS:
        rows, _ = read_component_rows(one_day_interval5_run.out_dir, component)
        dropped = expected - len(rows)
        assert mean_dropped - tolerance <= dropped <= mean_dropped + tolerance, (
            f"{component} @ interval=5: rows={len(rows)} dropped={dropped} "
            f"outside expected drop window {mean_dropped:.1f} ± {tolerance:.1f}"
        )


def test_interval_seconds_timestamps_step_by_interval(one_day_interval5_run):
    """Consecutive emitted timestamps must differ by exactly 5 seconds — except
    across a dropped row, where the gap is a multiple of 5."""
    for component in COMPONENTS:
        rows, _ = read_component_rows(one_day_interval5_run.out_dir, component)
        timestamps = [datetime.datetime.fromisoformat(ts) for ts in rows]
        deltas = {(b - a).total_seconds() for a, b in zip(timestamps, timestamps[1:])}
        assert deltas, f"{component} produced no emitted rows"
        for d in deltas:
            assert d > 0 and d % 5 == 0, (
                f"{component}: gap of {d}s is not a multiple of the 5s interval"
            )


def test_interval_seconds_anomalies_at_correct_seconds(amc, one_day_interval5_run):
    """Manifest entries at interval=5 must report the same wall-clock seconds
    as their declared ``time_offset`` (rounded to nearest 5s row). Most one-day
    primary/cascade specs sit at minute or hour boundaries → divisible by 5,
    so the rounded row's timestamp equals the spec's exact time_offset."""
    manifest = read_manifest(one_day_interval5_run.out_dir)
    by_key = {(m["component"], m["metric"], m["description"]): m for m in manifest}
    declared = declared_specs(amc, days=1, signal_level="medium")

    # All declared one-day specs land within 17,280 rows at interval=5 except
    # those whose rounded index would equal 17,280 — none of the current
    # one-day specs hit that boundary, so every spec should be present unless
    # dropped (drop_rate ~0.05% → at most a couple). Assert most are present
    # and every present one's timestamp matches the rounded row.
    interval = 5
    n_rows = amc.SECONDS_PER_DAY // interval
    expected_present = 0
    matched = 0
    for component, time_offset, metric, description in declared:
        if time_offset >= amc.SECONDS_PER_DAY:
            continue  # multi-day spec; not reachable on a 1-day run
        idx = int(round(time_offset / interval))
        if idx >= n_rows:
            continue
        expected_present += 1
        manifest_entry = by_key.get((component, metric, description))
        if manifest_entry is None:
            continue  # almost certainly drop_rate; verified by overall count below
        expected_ts = amc.START + datetime.timedelta(seconds=idx * interval)
        actual_ts = datetime.datetime.fromisoformat(manifest_entry["timestamp"])
        assert actual_ts == expected_ts, (
            f"{component}/{metric} @ time_offset={time_offset}: manifest ts "
            f"{actual_ts} != expected nearest-row ts {expected_ts}"
        )
        matched += 1

    # Drop rate is ~0.05% — almost every declared spec should appear.
    assert expected_present >= 15, "sanity: at least 15 one-day specs should be in range"
    assert matched >= expected_present - 2, (
        f"too many specs missing from manifest at interval=5: "
        f"matched={matched} / expected={expected_present}"
    )


def test_interval_seconds_default_is_one(amc):
    """The flag's CLI default must remain 1.0 so existing callers keep their
    behavior — the rest of the existing suite asserts the resulting output is
    unchanged at that default."""
    args = amc.parse_args(["--output-dir", "ignored"])
    assert args.interval_seconds == 1.0


# ------------------------------------------------------------------
# VER-111: sub-second --interval-seconds must keep per-row timestamp
# strings unique and combine_logs_unified must preserve every row.
# Pre-fix, _build_timestamp_arrays formatted every row at second
# precision, so adjacent rows at interval=0.5 collided on the same
# timestamp string. That collapsed per-component CSVs (duplicate keys)
# and silently dropped half the rows from the unified combine output.
# ------------------------------------------------------------------
def test_sub_second_interval_unique_timestamps_and_lossless_combine(amc, tmp_path):
    """At ``--interval-seconds 0.5`` every per-component CSV row must have a
    unique timestamp string, and ``combine_logs_unified`` must preserve every
    generated row (no silent ``(timestamp, component)`` collisions)."""
    out = tmp_path / "sub_second"
    out.mkdir()
    run_capture(
        amc,
        out,
        days=1,
        drop_rate=0.0,
        extra_args=[
            "--interval-seconds", "0.5",
            "--combine",
        ],
    )

    skip_names = {"anomalies.csv", "combined_metrics_unified.csv"}
    component_csvs = [p for p in sorted(out.glob("*.csv")) if p.name not in skip_names]
    assert component_csvs, "no per-component CSVs produced"

    component_row_counts = []
    for comp_csv in component_csvs:
        with open(comp_csv) as f:
            rows = list(csv.DictReader(f))
        timestamps = [r["timestamp"] for r in rows]
        assert timestamps, f"{comp_csv.name} produced no data rows"
        assert len(timestamps) == len(set(timestamps)), (
            f"Duplicate timestamps in {comp_csv.name}: "
            f"{len(timestamps) - len(set(timestamps))} duplicates "
            f"(sub-second rows collapsed)"
        )
        component_row_counts.append(len(rows))

    combined = out / "combined_metrics_unified.csv"
    assert combined.exists(), "--combine did not produce combined_metrics_unified.csv"
    with open(combined) as f:
        combined_rows = list(csv.DictReader(f))
    # With drop_rate=0.0 every component has the same row count, and the
    # unified output must keep one row per timestamp. Any silent collapse
    # would shrink the unified count below the per-component count.
    assert len(combined_rows) == max(component_row_counts), (
        f"combined row count {len(combined_rows)} != max per-component row count "
        f"{max(component_row_counts)} — combine silently dropped sub-second rows"
    )


def test_sub_second_interval_timestamps_have_fractional_resolution(amc, tmp_path):
    """Timestamp strings at interval=0.5 must carry fractional precision so the
    step between adjacent rows is observable in the rendered string."""
    out = tmp_path / "sub_second_fmt"
    out.mkdir()
    run_capture(
        amc,
        out,
        days=1,
        drop_rate=0.0,
        extra_args=[
            "--interval-seconds", "0.5",
        ],
    )
    # Pick any component CSV and inspect the first two timestamp strings.
    component_csvs = [
        p for p in sorted(out.glob("*.csv"))
        if p.name not in {"anomalies.csv", "combined_metrics_unified.csv"}
    ]
    with open(component_csvs[0]) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) >= 2, "need at least two rows to compare adjacent timestamps"
    assert rows[0]["timestamp"] != rows[1]["timestamp"], (
        f"adjacent sub-second rows still share a timestamp: {rows[0]['timestamp']!r}"
    )
    # Fractional component is present (a "." after the seconds).
    assert "." in rows[0]["timestamp"], (
        f"sub-second interval produced second-precision timestamp: {rows[0]['timestamp']!r}"
    )


# ------------------------------------------------------------------
# VER-105: --scenarios all is exactly equivalent to no flag (default).
# Pre-VER-102 byte hashes are locked separately in test_scenarios.py
# (DEFAULT_ONE_DAY_HASHES / DEFAULT_SEVEN_DAY_HASHES); this test adds
# the complementary parity check: passing --scenarios all explicitly
# produces the same per-component CSV + manifest bytes as omitting
# the flag, for both 1-day and 7-day runs at the documented seed 42.
# ------------------------------------------------------------------
def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _all_artifact_filenames():
    return [f"{c}.csv" for c in COMPONENTS] + ["anomalies.csv"]


@pytest.mark.parametrize("days", [1, 7])
def test_scenarios_all_matches_no_flag_byte_for_byte(amc, tmp_path, days):
    """``--scenarios all`` must produce the same per-component CSV and
    ``anomalies.csv`` bytes as the default (no flag). This is the
    default-equivalence regression for VER-102: any drift between the
    two paths would indicate a divergence in scenario resolution that
    the existing byte-hash lock in ``test_scenarios.py`` cannot detect
    on its own (the lock only covers no-flag).
    """
    out_default = tmp_path / f"default_{days}d"
    out_explicit = tmp_path / f"explicit_all_{days}d"
    out_default.mkdir()
    out_explicit.mkdir()
    run_capture(amc, out_default, days=days)
    run_capture(amc, out_explicit, days=days, extra_args=["--scenarios", "all"])

    for filename in _all_artifact_filenames():
        default_path = out_default / filename
        explicit_path = out_explicit / filename
        assert default_path.exists(), f"default run missing {filename}"
        assert explicit_path.exists(), f"--scenarios all run missing {filename}"
        assert _sha256(default_path) == _sha256(explicit_path), (
            f"{filename}: --scenarios all diverged from the default run bytes "
            f"at --duration-days {days}"
        )


def test_otel_emit_gauges_does_not_change_csv_output(amc, tmp_path):
    """VER-124: toggling --otel-emit-gauges on must not perturb any CSV byte.

    The gauge stream reads CSVs after they're written; flipping the flag adds
    network I/O but no value computation. Two runs against the same seed —
    one with the flag off, one with it on against a live mock collector —
    must produce byte-identical per-component CSVs and anomalies.csv. This is
    the regression that guards the "off by default" promise.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    out_off = tmp_path / "off"
    out_on = tmp_path / "on"
    out_off.mkdir()
    out_on.mkdir()

    # Off-path run: no streaming at all, no mock server needed.
    run_capture(amc, out_off, days=1, extra_args=["--interval-seconds", "600"])

    # On-path run: stream to a mock collector that always returns 200.
    received = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            received.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        run_capture(amc, out_on, days=1, extra_args=[
            "--interval-seconds", "600",
            "--otel-enabled",
            "--otel-emit-gauges",
            "--otel-metrics-endpoint", f"{base}/v1/metrics",
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-gauge-batch-seconds", "21600",
            "--otel-activity-log", str(tmp_path / "amc-activity-on.log"),
        ])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert received, "expected the gauge stream to post at least one request"

    for filename in _all_artifact_filenames():
        off_path = out_off / filename
        on_path = out_on / filename
        assert off_path.exists(), f"flag-off run missing {filename}"
        assert on_path.exists(), f"flag-on run missing {filename}"
        assert _sha256(off_path) == _sha256(on_path), (
            f"{filename}: --otel-emit-gauges on/off CSV bytes diverged"
        )


# ------------------------------------------------------------------
# Enriched anomalies.csv schema (VER-132): chronological sort,
# 12-column schema, cascade->primary linkage via parent_event_id.
# ------------------------------------------------------------------
_ENRICHED_MANIFEST_COLUMNS = [
    "timestamp", "component", "metric", "description",
    "scenario_id", "severity", "is_cascade",
    "event_id", "parent_event_id",
    "span_start", "span_end", "shape",
]


@pytest.mark.parametrize("days", [1, 7])
def test_manifest_sorted_and_cascade_parents_resolve(amc, tmp_path, days):
    """anomalies.csv must (a) carry the enriched 12-column schema, (b) be
    sorted by (span_start, component, metric), and (c) every cascade row
    must reference a primary row in the same file via parent_event_id.

    The 1-day run exercises the default selector matrix; the 7-day run
    surfaces multi-day scenarios so jwks_rotation_chaos and the storage
    scenarios contribute cascades to the linkage check.
    """
    out_dir = tmp_path / f"ver132_{days}d"
    extra = ["--signal-level", "high"] if days == 7 else None
    run_capture(amc, out_dir, days=days, extra_args=extra)

    rows = read_manifest(out_dir)
    assert rows, f"manifest empty for {days}-day run"

    # 1. Header has exactly the 12 enriched columns in the locked order.
    with open(out_dir / "anomalies.csv") as f:
        header = next(csv.reader(f))
    assert header == _ENRICHED_MANIFEST_COLUMNS, (
        f"{days}-day manifest header drift: {header}"
    )

    # 2. Sorted by (span_start, component, metric).
    sort_keys = [(r["span_start"], r["component"], r["metric"]) for r in rows]
    assert sort_keys == sorted(sort_keys), (
        f"{days}-day manifest not sorted by (span_start, component, metric)"
    )

    # 3. event_id is unique per row and matches the deterministic helper.
    event_ids = [r["event_id"] for r in rows]
    assert len(event_ids) == len(set(event_ids)), (
        f"{days}-day manifest has duplicate event_id values"
    )
    for r in rows:
        expected = amc._anomaly_event_id({
            "timestamp": r["timestamp"],
            "component": r["component"],
            "metric": r["metric"],
            "description": r["description"],
        })
        assert r["event_id"] == expected, (
            f"event_id mismatch on row {r}: expected {expected}"
        )

    # 4. is_cascade is the lowercase string vocabulary and primaries have no parent.
    primaries_by_event = {}
    cascade_rows = []
    for r in rows:
        assert r["is_cascade"] in {"true", "false"}, (
            f"is_cascade not in vocabulary: {r['is_cascade']!r}"
        )
        if r["is_cascade"] == "true":
            cascade_rows.append(r)
        else:
            assert r["parent_event_id"] == "", (
                f"primary row should have empty parent_event_id: {r}"
            )
            primaries_by_event[r["event_id"]] = r

    # 5. span_start/span_end and shape are populated and internally consistent.
    allowed_shapes = {"step", "ramp_linear", "ramp_exp", "sustained", "sawtooth", "sine"}
    for r in rows:
        assert r["span_start"] == r["timestamp"], (
            f"span_start must equal timestamp on row {r}"
        )
        assert r["span_end"] >= r["span_start"], (
            f"span_end {r['span_end']} < span_start {r['span_start']} on row {r}"
        )
        assert r["shape"] in allowed_shapes, (
            f"shape {r['shape']!r} not in {allowed_shapes}"
        )

    # 5b. span_end always names a timestamp that exists in the component CSV,
    #     even when ``--drop-rate`` would have dropped the nominal end row of
    #     a shaped span. Build one timestamp set per component, then look up
    #     each manifest row.
    component_ts: dict[str, set[str]] = {}
    for component_name in {r["component"] for r in rows}:
        csv_path = out_dir / f"{component_name}.csv"
        with open(csv_path) as f:
            reader = csv.reader(f)
            next(reader)  # header
            component_ts[component_name] = {row[0] for row in reader if row}
    for r in rows:
        comp_ts = component_ts[r["component"]]
        assert r["span_start"] in comp_ts, (
            f"span_start {r['span_start']} missing from {r['component']}.csv on row {r}"
        )
        assert r["span_end"] in comp_ts, (
            f"span_end {r['span_end']} missing from {r['component']}.csv on row {r} "
            f"(nominal end row likely fell on a --drop-rate gap)"
        )

    # 5c. At least one shaped row has span_end > span_start so the
    #     end-of-span code path is exercised by this test rather than relying
    #     on the smoke-run evidence alone.
    assert any(r["span_end"] > r["span_start"] for r in rows), (
        f"{days}-day manifest has no shaped specs with span_end > span_start; "
        f"end-of-span code path is not exercised"
    )

    # 6. Every cascade with a scenario_id resolves to a primary row of the
    #    same scenario in this same file.
    for r in cascade_rows:
        parent_id = r["parent_event_id"]
        assert parent_id, (
            f"cascade row missing parent_event_id (scenario_id={r['scenario_id']}): {r}"
        )
        parent = primaries_by_event.get(parent_id)
        assert parent is not None, (
            f"cascade row parent_event_id={parent_id} does not resolve to any "
            f"primary row in the same manifest"
        )
        assert parent["scenario_id"] == r["scenario_id"], (
            f"cascade scenario_id={r['scenario_id']} does not match parent "
            f"scenario_id={parent['scenario_id']} (event_id={parent_id})"
        )


def test_span_end_walks_back_when_nominal_end_row_is_dropped(amc, tmp_path):
    """Deterministic regression test for the ``span_end`` walk-back fix.

    Under the default ``--drop-rate 0.0005`` the seed-42 run happens to
    keep every shaped span's nominal end row, so a revert of the walk-back
    fix to ``end_idx = end_idx_nominal`` would silently pass
    ``test_manifest_sorted_and_cascade_parents_resolve``. Re-run at
    ``--drop-rate 0.7 --signal-level high``: many shaped spans survive
    (anchor row retained), and per-span the nominal end is dropped with
    70% probability, so a regression would (a) emit a ``span_end``
    timestamp absent from the component CSV (caught by the membership
    invariant) and (b) leave no span with ``span_end < nominal_end``
    (caught by the strict walk-back invariant below).
    """
    out_dir = tmp_path / "walk_back_high_drop"
    run_capture(
        amc, out_dir, days=1, drop_rate=0.7,
        extra_args=["--signal-level", "high"],
    )

    rows = read_manifest(out_dir)
    shaped_rows = [r for r in rows if r["span_end"] > r["span_start"]]
    assert shaped_rows, (
        "no shaped rows produced at drop_rate=0.7 high; cannot exercise walk-back"
    )

    # Membership invariant: span_end must exist in its component CSV.
    component_ts: dict[str, set[str]] = {}
    for r in shaped_rows:
        component_name = r["component"]
        if component_name not in component_ts:
            with open(out_dir / f"{component_name}.csv") as f:
                reader = csv.reader(f)
                next(reader)
                component_ts[component_name] = {row[0] for row in reader if row}
        assert r["span_end"] in component_ts[component_name], (
            f"span_end {r['span_end']} missing from {component_name}.csv at "
            f"drop_rate=0.7; walk-back fix appears reverted"
        )

    # Strong walk-back invariant: at least one shaped row must have
    # ``span_end`` strictly before the nominal end timestamp implied by
    # the scenario's ``duration_seconds``. Match the manifest row to its
    # source spec by (component, metric, time_offset) — disambiguates
    # scenarios that emit multiple specs at the same (component, metric)
    # but different time_offsets (e.g. db_stall's two error_rate ramps).
    interval = 1.0  # default --interval-seconds for this fixture
    n_rows = 86400  # days=1, interval=1.0
    start_of_run = datetime.datetime(2026, 3, 10)
    walked_back_at_least_once = False
    for r in shaped_rows:
        scenario = amc.SCENARIOS.get(r["scenario_id"])
        if scenario is None:
            continue
        start_dt = datetime.datetime.fromisoformat(r["span_start"])
        time_offset = int((start_dt - start_of_run).total_seconds())
        spec_pairs = list(scenario.primary_specs) + list(scenario.cascade_specs)
        match = next(
            (
                spec_d for comp, spec_d in spec_pairs
                if comp == r["component"]
                and spec_d["metric"] == r["metric"]
                and spec_d.get("time_offset") == time_offset
            ),
            None,
        )
        if match is None:
            continue
        duration_seconds = float(match.get("duration_seconds", 0) or 0)
        if duration_seconds <= 0:
            continue
        # End-row offset matches generate_component: ceil(duration/interval) - 1
        duration_rows = max(1, int(math.ceil(duration_seconds / interval)))
        nominal_end_row = min(time_offset + duration_rows - 1, n_rows - 1)
        nominal_end_dt = start_of_run + datetime.timedelta(seconds=nominal_end_row)
        actual_end_dt = datetime.datetime.fromisoformat(r["span_end"])
        if actual_end_dt < nominal_end_dt:
            walked_back_at_least_once = True
            break

    assert walked_back_at_least_once, (
        "no shaped row has span_end strictly before its nominal end at "
        "drop_rate=0.7; walk-back code path is never exercised (regression?)"
    )

"""VER-159 acceptance: every scenario row must produce a visibly anomalous
metric value under ``--topology-mode realistic`` (the post-VER-156 default).

The test compares two runs per scenario:

* **Active run** — ``--scenarios <slug> --signal-level <severity>``. Fires
  every primary spec and cascade spec the scenario declares.
* **Baseline run** — same flags plus ``--exclude-scenarios <slug>``.
  Exclusion wins over allowlist in ``_resolve_scenarios``, so the resolved
  set is empty: no anomaly overrides, but the RNG draw order, topology
  coupling, and saturation feedback machinery still run.

Because no anomalies fire in the baseline run, the component CSV columns
provide a representative "natural baseline" for comparison. While not
strictly identical to the active run's baseline (as anomaly overrides can
shift the shared ``ctx.rng`` and realistic-mode saturation feedback can
propagate effects outside the targeted cell), the deviation at an anomaly
row remains a direct measurement of the spec's primary effect, and the
column-wide std of the baseline remains a fair noise floor.

Acceptance per VER-159: ``max|active[span] - baseline[span]| > std(
baseline_column)`` for every (component, metric, span) recorded in the
active run's ``anomalies.csv``.

This test was added in VER-159 after the realistic-mode saturation
feedback (VER-154/VER-155) raised the std of ``apigateway.error_rate``
from ~0.018 to ~0.040 and ``authservice.error_rate`` from ~0.018 to
~0.050, which sank eight hand-tuned cascade generators below the noise
floor. The test prevents the same class of silent no-op from reappearing
when later phases re-tune saturation parameters or add new edges.
"""

import contextlib
import csv
import io
import json
import shutil
from pathlib import Path

import numpy as np
import pytest


def _run_scenario(amc, out_dir: Path, *, scenario: str, days: int,
                  signal_level: str, exclude: bool,
                  extra_args: list[str] | None = None) -> None:
    """Drive ``amc.main`` for one scenario run into ``out_dir``.

    Uses ``contextlib.redirect_stderr`` to scope the stderr capture to the
    ``amc.main`` call, which is safer than globally reassigning
    ``sys.stderr`` (the global swap is not thread-safe and can swallow
    output from concurrent loggers).

    Explicitly sets ``--drop-rate 0`` so that active and baseline runs
    have perfectly aligned row counts (no stochastic packet loss to
    jitter the timestamp lists).
    """
    if out_dir.exists():
        shutil.rmtree(out_dir)
    argv = [
        "--seed", "42",
        "--duration-days", str(days),
        "--drop-rate", "0",
        "--output-dir", str(out_dir),
        "--scenarios", scenario,
        "--signal-level", signal_level,
    ]
    if extra_args:
        argv += list(extra_args)
    if exclude:
        argv += ["--exclude-scenarios", scenario]
    stderr_buf = io.StringIO()
    with contextlib.redirect_stderr(stderr_buf):
        amc.main(argv)


def _load_component_column(out_dir: Path, component: str, metric: str):
    """Return (list_of_row_timestamps, np.ndarray_of_values) for the named
    metric.

    While the CLI default has a non-zero ``--drop-rate`` (omitting rows
    entirely from the CSV), this test explicitly sets it to 0 in
    ``_run_scenario`` to ensure row alignment between runs. We still check
    for empty rows defensively.

    Opens with ``newline=""`` and ``encoding="utf-8"`` so the ``csv``
    module sees the file's raw line terminators (CSV's universal-newlines
    handling is its own responsibility) and so the decode is
    platform-independent.
    """
    fp = out_dir / f"{component}.csv"
    timestamps: list[str] = []
    values: list[float] = []
    with open(fp, newline="", encoding="utf-8") as fh:
        rdr = csv.reader(fh)
        header = next(rdr)
        try:
            col_idx = header.index(metric)
        except ValueError:
            return None, None
        for row in rdr:
            if not row:
                continue
            timestamps.append(row[0])
            values.append(float(row[col_idx]))
    return timestamps, np.array(values)


def _signal_level_for(scenario) -> str:
    sev = scenario.severity
    return sev if sev in ("medium", "high") else "low"


def _scenario_uses_id_filter(scenario) -> bool:
    """Return True if any spec declares an id-based instance_filter."""
    return any(
        "instance_filter" in spec and not callable(spec["instance_filter"])
        for _, spec in (*scenario.primary_specs, *scenario.cascade_specs)
    )


def _scenario_uses_callable_filter(scenario) -> bool:
    """Return True if any spec declares a callable instance_filter."""
    return any(
        "instance_filter" in spec and callable(spec["instance_filter"])
        for _, spec in (*scenario.primary_specs, *scenario.cascade_specs)
    )


def _extra_args_for_scenario(amc, scenario, slug: str, base_tmp: Path) -> list[str]:
    """Return CLI args needed for instance-filtered scenarios to manifest.

    The default single anonymous ``Instance()`` deliberately does not match
    id-based filters like ``["i0"]`` or dimension predicates like
    ``inst.az == "us-east-1a"``.  Those scenarios need a non-anonymous
    topology in both the active and baseline runs, otherwise the generator
    correctly warns and skips the filtered specs before they can reach
    ``anomalies.csv``.
    """
    uses_callable_filter = _scenario_uses_callable_filter(scenario)
    if uses_callable_filter:
        cfg: dict = {"components": {}}
        for component in amc.COMPONENTS:
            cfg["components"][component] = [
                {"id": "i0", "pod": "pod-0", "az": "us-east-1a"},
                {"id": "i1", "pod": "pod-1", "az": "us-west-2a"},
            ]
        cfg_path = base_tmp / f"_instance_cfg_{slug}.json"
        cfg_path.write_text(json.dumps(cfg, sort_keys=True), encoding="utf-8")
        return ["--instance-config", str(cfg_path)]

    if _scenario_uses_id_filter(scenario):
        return ["--instances-per-component", "3"]

    return []


def _timestamp_index_map(timestamps: list[str]) -> dict[str, list[int]]:
    """Map each timestamp to all matching row indexes.

    Multi-instance component CSVs repeat the same timestamp once per instance
    block.  Keeping every index lets the deviation check compare every matching
    instance window instead of accidentally selecting only the last instance.
    """
    out: dict[str, list[int]] = {}
    for i, ts in enumerate(timestamps):
        out.setdefault(ts, []).append(i)
    return out


def _max_span_deviation(
    vals_a, vals_b, starts_a, ends_a, starts_b, ends_b
) -> float | None:
    """Return max absolute active-vs-baseline deviation over matching spans."""
    max_dev: float | None = None
    for i_a_start, i_a_end, i_b_start, i_b_end in zip(
        starts_a, ends_a, starts_b, ends_b
    ):
        a_window = vals_a[i_a_start:i_a_end + 1]
        b_window = vals_b[i_b_start:i_b_end + 1]
        m = min(len(a_window), len(b_window))
        if m == 0:
            continue
        dev = float(np.max(np.abs(a_window[:m] - b_window[:m])))
        max_dev = dev if max_dev is None else max(max_dev, dev)
    return max_dev


@pytest.fixture(scope="session")
def scenario_deviation_results(amc, tmp_path_factory):
    """Run every scenario twice (active + exclude-as-baseline) and return a
    dict mapping scenario slug → list of per-row result dicts.

    Each per-row dict carries: ``component``, ``metric``, ``ts``, ``span``,
    ``is_cascade``, ``max_dev``, ``base_std``, ``ratio``, ``note``. ``ratio``
    is ``max_dev / base_std`` normally; ``inf`` when the baseline column is
    constant and ``max_dev > 0`` (a non-zero deviation against a constant
    floor is unambiguously above the >1σ gate); ``0.0`` when both
    ``base_std`` and ``max_dev`` are zero (a silent no-op on a constant
    baseline must still fail the gate). The acceptance check is
    ``ratio > 1.0``.
    """
    base_tmp = tmp_path_factory.mktemp("scenario_deviation")
    results: dict[str, list[dict]] = {}
    for slug, scenario in amc.SCENARIOS.items():
        days = scenario.days_required
        signal_level = _signal_level_for(scenario)
        extra_args = _extra_args_for_scenario(amc, scenario, slug, base_tmp)
        active_dir = base_tmp / f"{slug}_active"
        baseline_dir = base_tmp / f"{slug}_baseline"
        _run_scenario(amc, active_dir, scenario=slug, days=days,
                      signal_level=signal_level, exclude=False,
                      extra_args=extra_args)
        _run_scenario(amc, baseline_dir, scenario=slug, days=days,
                      signal_level=signal_level, exclude=True,
                      extra_args=extra_args)

        with open(active_dir / "anomalies.csv", newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

        # column_cache stores everything that only depends on
        # (component, metric): the loaded timestamp lists and value
        # arrays for both runs, the baseline column std, and the
        # timestamp→row-index maps. Recomputing these per anomaly row
        # was wasted work — a scenario with 50 cascades on the same
        # metric otherwise rebuilds the index dict 50 times.
        column_cache: dict[tuple[str, str], tuple] = {}
        per_scenario: list[dict] = []
        for r in rows:
            comp = r["component"]
            metric = r["metric"]
            key = (comp, metric)
            if key not in column_cache:
                ts_a, vals_a = _load_component_column(active_dir, comp, metric)
                ts_b, vals_b = _load_component_column(baseline_dir, comp, metric)
                if vals_a is None or vals_b is None:
                    column_cache[key] = (ts_a, vals_a, ts_b, vals_b,
                                         None, None, None)
                else:
                    base_std = float(np.std(vals_b))
                    ts_to_idx_a = _timestamp_index_map(ts_a)
                    ts_to_idx_b = _timestamp_index_map(ts_b)
                    column_cache[key] = (ts_a, vals_a, ts_b, vals_b,
                                         base_std, ts_to_idx_a, ts_to_idx_b)
            (ts_a, vals_a, ts_b, vals_b,
             base_std, ts_to_idx_a, ts_to_idx_b) = column_cache[key]

            if vals_a is None or vals_b is None:
                per_scenario.append({
                    "component": comp, "metric": metric,
                    "ts": r["timestamp"], "span": (r["span_start"], r["span_end"]),
                    "is_cascade": r["is_cascade"], "max_dev": None,
                    "base_std": None, "ratio": None,
                    "note": "metric column not emitted by default trim",
                })
                continue

            span_start = r["span_start"] or r["timestamp"]
            span_end = r["span_end"] or r["timestamp"]
            i_a_starts = ts_to_idx_a.get(span_start)
            i_a_ends = ts_to_idx_a.get(span_end)
            i_b_starts = ts_to_idx_b.get(span_start)
            i_b_ends = ts_to_idx_b.get(span_end)
            if None in (i_a_starts, i_a_ends, i_b_starts, i_b_ends):
                per_scenario.append({
                    "component": comp, "metric": metric,
                    "ts": r["timestamp"], "span": (span_start, span_end),
                    "is_cascade": r["is_cascade"], "max_dev": None,
                    "base_std": base_std, "ratio": None,
                    "note": "anomaly row dropped from CSV",
                })
                continue

            dev = _max_span_deviation(
                vals_a, vals_b, i_a_starts, i_a_ends, i_b_starts, i_b_ends
            )
            if dev is None:
                per_scenario.append({
                    "component": comp, "metric": metric,
                    "ts": r["timestamp"], "span": (span_start, span_end),
                    "is_cascade": r["is_cascade"], "max_dev": None,
                    "base_std": base_std, "ratio": None,
                    "note": "anomaly row dropped from CSV",
                })
                continue
            # When the baseline column is perfectly constant
            # (base_std == 0), a non-zero deviation is "infinite
            # signal" and passes the >1σ gate; but a zero deviation
            # on a constant baseline is still a silent no-op, so we
            # must not let `inf` mask `0/0`. Require dev > 0 to
            # promote to inf; otherwise ratio stays at 0.0 and the
            # gate correctly fails.
            if base_std > 0:
                ratio = dev / base_std
            elif dev > 0:
                ratio = float("inf")
            else:
                ratio = 0.0
            per_scenario.append({
                "component": comp, "metric": metric,
                "ts": r["timestamp"], "span": (span_start, span_end),
                "is_cascade": r["is_cascade"], "max_dev": dev,
                "base_std": base_std, "ratio": ratio,
                "note": "",
            })
        results[slug] = per_scenario
    return results


def _scenario_slugs():
    # Reuse the memoized loader from conftest so collection-time
    # parametrize() and the session-scoped ``amc`` fixture share a single
    # ``exec_module`` build of the registry rather than paying for two.
    from conftest import _load_amc  # type: ignore[import-not-found]
    return sorted(_load_amc().SCENARIOS.keys())


@pytest.mark.parametrize("slug", _scenario_slugs())
def test_scenario_every_recorded_anomaly_fires_above_baseline_sigma(
    slug, scenario_deviation_results
):
    """Every (component, metric, span) recorded in the scenario's
    ``anomalies.csv`` must deviate from the baseline column by more than
    one column-wide standard deviation under realistic mode.

    This is the VER-159 acceptance gate: it catches the class of silent
    no-op where a hand-tuned cascade generator returns a value that the
    realistic-mode saturation floor has lifted the baseline above. The
    error message names every offending row with its deviation, std, and
    ratio so the re-tune target is obvious.
    """
    rows = scenario_deviation_results[slug]
    assert rows, (
        f"Scenario {slug!r} recorded no anomaly rows under the test's "
        f"fixture flags (--seed 42 --drop-rate 0 --duration-days "
        f"<scenario.days_required> --signal-level <scenario.severity> "
        f"--scenarios {slug}). Either the scenario silently no-ops or the "
        f"resolution filters (--signal-level / --duration-days / "
        f"--components) dropped it."
    )
    weak = [r for r in rows if r["ratio"] is None or r["ratio"] <= 1.0]
    if weak:
        lines = [f"Scenario {slug!r}: {len(weak)} row(s) fail the >1σ gate:"]
        for r in weak:
            cascade = " (cascade)" if r["is_cascade"] == "true" else ""
            note = f" [{r['note']}]" if r["note"] else ""
            lines.append(
                f"  {r['component']}.{r['metric']} @ {r['ts']}{cascade}: "
                f"max_dev={r['max_dev']!r} base_std={r['base_std']!r} "
                f"ratio={r['ratio']!r}{note}"
            )
        pytest.fail("\n".join(lines))

import csv
import datetime
import importlib.util
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "anomaly-metric-creator.py"


_AMC_MODULE_CACHE = None


def _load_amc():
    # Memoized so test-collection helpers (e.g. parametrize keys) and the
    # session-scoped ``amc`` fixture can share a single module load instead
    # of paying for two full ``exec_module`` builds of the registry.
    global _AMC_MODULE_CACHE
    if _AMC_MODULE_CACHE is None:
        spec = importlib.util.spec_from_file_location("anomaly_metric_creator", SCRIPT_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _AMC_MODULE_CACHE = module
    return _AMC_MODULE_CACHE


_CHEAP_INTERVAL_SECONDS_DEFAULT = 60.0


def run_capture(
    amc,
    out_dir,
    *,
    days,
    seed=42,
    drop_rate=None,
    extra_args=None,
    interval_seconds=_CHEAP_INTERVAL_SECONDS_DEFAULT,
):
    """Run ``main()`` end-to-end into ``out_dir`` with the cheap-default
    interval.

    ``interval_seconds`` defaults to ``60.0`` so tests that don't
    explicitly care about per-second resolution match the script's
    current one-minute default. Two opt-outs exist:

    - ``interval_seconds=None`` skips the ``--interval-seconds`` flag
      entirely, so the script's own default applies.
    - ``interval_seconds=1.0`` (or any explicit float) passes that
      value via ``--interval-seconds``. Tests that need 1s rows for
      sub-second timestamp checks, 86,400-row sweeps, or legacy locked
      hashes should pair the explicit value with
      ``@pytest.mark.full_resolution`` so the intent is auditable.

    ``--interval-seconds`` in ``extra_args`` raises ``ValueError`` —
    in either the standalone ``--interval-seconds VALUE`` form or the
    ``--interval-seconds=VALUE`` form ``argparse`` also accepts — so
    the flag has a single source of truth and the ``full_resolution``
    audit can recognize opt-out sites by inspecting kwargs alone.
    """
    extra_args_list = list(extra_args or [])
    if any(
        arg == "--interval-seconds" or arg.startswith("--interval-seconds=")
        for arg in extra_args_list
    ):
        raise ValueError(
            "run_capture: --interval-seconds must be passed via the "
            "interval_seconds keyword argument, not extra_args "
            "(neither the standalone '--interval-seconds VALUE' form "
            "nor the '--interval-seconds=VALUE' form is allowed in "
            "extra_args). Use interval_seconds=None for the script's "
            "current default."
        )
    args = [
        "--seed", str(seed),
        "--duration-days", str(days),
        "--output-dir", str(out_dir),
    ]
    if drop_rate is not None:
        args += ["--drop-rate", str(drop_rate)]
    if interval_seconds is not None:
        args += ["--interval-seconds", str(interval_seconds)]
    args += extra_args_list
    stderr_buf = io.StringIO()
    real_stderr = sys.stderr
    sys.stderr = stderr_buf
    try:
        amc.main(args)
    finally:
        sys.stderr = real_stderr
    return SimpleNamespace(out_dir=out_dir, stderr=stderr_buf.getvalue())


_OTEL_ENV_VARS = (
    "MEZMO_OTEL_LOGS_ENDPOINT",
    "MEZMO_OTEL_LOGS_AUTH_TOKEN",
    "MEZMO_OTEL_METRICS_ENDPOINT",
    "MEZMO_OTEL_METRICS_AUTH_TOKEN",
    "MEZMO_OTEL_TRACES_ENDPOINT",
    "MEZMO_OTEL_TRACES_AUTH_TOKEN",
    "MEZMO_OTEL_STREAM_AUTH_SCHEME",
    "MEZMO_OTEL_STREAM_PROTOCOL",
)


@pytest.fixture(scope="session", autouse=True)
def _isolate_otel_env_session():
    # Argparse defaults for OTEL endpoints/tokens read from MEZMO_OTEL_* env
    # vars at parse time, so a developer shell with those exported (or a CI
    # runner with them in the job env) makes --otel-enabled look valid even
    # without explicit endpoint flags. Strip them at session start so every
    # session-scoped fixture (one_day_run_a, seven_day_run, ...) sees a clean
    # slate before its first parse_args call, and so subprocess _invoke()
    # runs inherit the cleared parent env. Function-scoped tests using
    # monkeypatch.setenv still work — their setenv runs after the session pop
    # and pytest restores to the popped (unset) state on test teardown.
    mp = pytest.MonkeyPatch()
    for name in _OTEL_ENV_VARS:
        mp.delenv(name, raising=False)
    yield
    mp.undo()


@pytest.fixture(scope="session", autouse=True)
def _guard_cwd_otel_activity_log():
    # --otel-activity-log defaults to ./otel-activity.log relative to the
    # process CWD, and subprocess _invoke() helpers inherit pytest's CWD —
    # so a streaming test that forgets to pass --otel-activity-log (or set
    # cwd=) appends transport diagnostics into whatever directory pytest
    # was launched from, typically the repo root. That violates the
    # "tests write only into tmp_path" rule and creates a shared append
    # target across xdist workers. Snapshot the CWD file's state at
    # session start and fail the session teardown if anything touched it.
    leak_target = Path.cwd() / "otel-activity.log"
    before = leak_target.stat().st_mtime_ns if leak_target.exists() else None
    yield
    after = leak_target.stat().st_mtime_ns if leak_target.exists() else None
    assert before == after, (
        f"{leak_target} was created or modified during the test run. A "
        "streaming test invoked the CLI without --otel-activity-log (or "
        "an explicit cwd=), leaking the activity log outside tmp_path. "
        "Pass --otel-activity-log str(tmp_path / 'otel-activity.log') in "
        "the test's _invoke() call."
    )


@pytest.fixture(scope="session")
def amc():
    return _load_amc()


@pytest.fixture(scope="session")
def one_day_run_a(amc, tmp_path_factory):
    # Explicit 1s cadence preserves the legacy full-resolution hashes in
    # tests/test_scenarios.py even though the CLI default is now 60s.
    out = tmp_path_factory.mktemp("one_day_a")
    return run_capture(amc, out, days=1, interval_seconds=1.0)


@pytest.fixture(scope="session")
def one_day_run_b(amc, tmp_path_factory):
    # Explicit 1s cadence: paired byte-identity check with one_day_run_a.
    out = tmp_path_factory.mktemp("one_day_b")
    return run_capture(amc, out, days=1, interval_seconds=1.0)


@pytest.fixture(scope="session")
def seven_day_run(amc, tmp_path_factory):
    # Explicit 1s cadence preserves the legacy full-resolution hashes in
    # tests/test_scenarios.py even though the CLI default is now 60s.
    out = tmp_path_factory.mktemp("seven_day")
    return run_capture(amc, out, days=7, interval_seconds=1.0)


@pytest.fixture(scope="session")
def one_day_full_metrics_run(amc, tmp_path_factory):
    """1-day run with --metrics-per-component 10 so ``test_value_range_sanity_full_catalog``
    can exercise every supplemental metric column. Shares ``run_capture`` so default
    and full-metric runs go through one execution path. ``--combine`` coverage for
    the new flag lives in ``tests/test_combine.py`` and uses its own subprocess
    fixtures rather than this in-process run. Explicit ``interval_seconds=1.0``
    keeps the full-catalog value-range assertions on the 86,400-row sweep."""
    out = tmp_path_factory.mktemp("one_day_full_metrics")
    return run_capture(
        amc, out, days=1,
        extra_args=["--metrics-per-component", "10"],
        interval_seconds=1.0,
    )


@pytest.fixture(scope="session")
def one_day_independent_run(amc, tmp_path_factory):
    """1-day run with ``--topology-mode independent`` so ``test_value_range_sanity``
    can validate the natural-baseline statistical model (the 8σ band derived
    from each MetricSpec's base/std/multiplier) without being thrown off by
    realistic-mode topology coupling or saturation feedback. Pinning to the
    deprecation alias is intentional: the natural-band invariant is a property
    of the independent baseline model, which is the building block of both
    modes; realistic-mode behavior is exercised by the topology-specific
    tests (coupling correlation, saturation lift). Schedule this fixture's
    retirement together with the alias removal after phase 9.
    Explicit ``interval_seconds=1.0`` keeps the natural-band check on the
    historic 86,400-row natural sample."""
    out = tmp_path_factory.mktemp("one_day_independent")
    return run_capture(
        amc, out, days=1,
        extra_args=["--topology-mode", "independent"],
        interval_seconds=1.0,
    )


@pytest.fixture(scope="session")
def one_day_full_metrics_independent_run(amc, tmp_path_factory):
    """1-day run with ``--metrics-per-component 10 --topology-mode independent``
    so ``test_value_range_sanity_full_catalog`` exercises every supplemental
    metric column without being thrown off by topology coupling / saturation.
    See the comment on ``one_day_independent_run`` for the rationale around
    pinning to the deprecation alias. Explicit ``interval_seconds=1.0`` keeps
    the value-range sanity sweep at full resolution."""
    out = tmp_path_factory.mktemp("one_day_full_metrics_independent")
    return run_capture(
        amc,
        out,
        days=1,
        extra_args=["--metrics-per-component", "10", "--topology-mode", "independent"],
        interval_seconds=1.0,
    )


@pytest.fixture(scope="session")
def n3_one_day_dataset_dir(amc, tmp_path_factory):
    """1-day ``--instances-per-component 3`` per-component CSVs +
    ``anomalies.csv`` + ``schema.json``, generated once and shared across
    the long-form writer tests (``tests/test_gauges_file.py`` and
    ``tests/test_combine.py``), the per-component hash locks in
    ``tests/test_instances_per_component.py``, and the N=3 schema
    assertions in ``tests/test_schema_file.py``.

    The generation pass costs ~25-30 seconds and produces ~1.3 GB of
    output, so running it once per consuming test module would
    multiply both the wall time and the disk pressure. The Phase 5
    writer tests instead invoke ``write_gauges_csv`` and
    ``combine_logs`` directly against the shared dataset (the
    writers are pure functions of the per-component CSV bytes), so
    the locked SHA-256 golden hashes hold byte-identically with no
    second generation pass.

    ``--emit-selection metrics,schema`` keeps the dataset narrow:
    per-component CSVs + ``anomalies.csv`` + ``schema.json``, no
    logs / traces artifacts that no consumer reads. Per-component CSV
    bytes are independent of ``--emit-selection`` (the writers consume
    no RNG), so the locked ``N3_ONE_DAY_HASHES`` are unaffected by the
    ``schema`` token; the ``schema.json`` bytes match the locked
    ``SCHEMA_N3_ONE_DAY_HASH`` because that hash was locked under the
    same ``metrics,schema`` selection. ``combine_logs`` autodiscovery
    globs ``*.csv`` only, so the extra ``schema.json`` is invisible to
    the hardlink-based writer fixtures.
    Explicit ``interval_seconds=1.0`` preserves the full-resolution locked
    ``N3_ONE_DAY_HASHES`` in
    ``tests/test_instances_per_component.py`` keep matching."""
    out = tmp_path_factory.mktemp("ver148_n3_one_day_dataset")
    return run_capture(
        amc, out, days=1,
        extra_args=[
            "--instances-per-component", "3",
            "--emit-selection", "metrics,schema",
        ],
        interval_seconds=1.0,
    ).out_dir


@pytest.fixture(scope="session")
def n3_seven_day_dataset_dir(amc, tmp_path_factory):
    """7-day ``--instances-per-component 3`` dataset, generated once and
    shared by the 7-day hash locks in
    ``tests/test_instances_per_component.py`` (``N3_SEVEN_DAY_HASHES``)
    and the N=3 schema hash in ``tests/test_schema_file.py``
    (``SCHEMA_N3_SEVEN_DAY_HASH``).

    This is the single most expensive generation in the suite (~7x the
    1-day N=3 pass; multiple minutes and ~9 GB at 1s resolution), so it
    must never be duplicated in a module-scoped fixture — the suite
    previously ran three independent copies of it across two modules
    (the PR #67 antipattern from the "Test resource cost" checklist).
    ``--emit-selection metrics,schema`` trims the logs / traces
    artifacts no consumer reads; per-component CSV bytes are
    independent of ``--emit-selection`` so ``N3_SEVEN_DAY_HASHES``
    are unaffected, and ``SCHEMA_N3_SEVEN_DAY_HASH`` was locked under
    the same ``metrics,schema`` selection. Explicit
    ``interval_seconds=1.0`` preserves the full-resolution locks."""
    out = tmp_path_factory.mktemp("n3_seven_day_dataset")
    return run_capture(
        amc, out, days=7,
        extra_args=[
            "--instances-per-component", "3",
            "--emit-selection", "metrics,schema",
        ],
        interval_seconds=1.0,
    ).out_dir


# total metric count in COMPONENTS (all catalogs are at the MAX_METRICS_PER_COMPONENT cap)
COMPONENT_FIELDS = {
    "authservice": 10,
    "cacheservice": 10,
    "apigateway": 10,
    "database": 10,
    "mqservice": 10,
    "llm_analytics": 10,
    "loadbalancer": 10,
    "objectstore": 10,
    "vectorstore": 10,
    "scheduler": 10,
    "paymentservice": 10,
    "identityprovider": 10,
    "observabilitypipeline": 10,
    "gpu_inference": 10,
}

# How many metrics each component emits when --metrics-per-component is unset.
# This is the historic per-component count and must stay stable for default
# CSV byte-for-byte compatibility.
DEFAULT_METRIC_COUNT = {
    "authservice": 6,
    "cacheservice": 6,
    "apigateway": 6,
    "database": 7,
    "mqservice": 6,
    "llm_analytics": 8,
    "loadbalancer": 7,
    "objectstore": 5,
    "vectorstore": 5,
    "scheduler": 5,
    "paymentservice": 5,
    "identityprovider": 5,
    "observabilitypipeline": 4,
    "gpu_inference": 10,
}

COMPONENTS = list(COMPONENT_FIELDS.keys())


def declared_specs(amc, *, days=None, signal_level=None):
    """Flatten declared anomaly specs into (component, time_offset, metric, description).

    When ``days`` and/or ``signal_level`` are provided, only scenarios that would
    be active for those run parameters are included (mirrors ``_resolve_scenarios``
    severity + duration gates). Pass matching values from the fixture under test
    so the declared set aligns with what actually appears in the manifest.
    """
    allowed_severities = amc.SIGNAL_LEVELS[signal_level] if signal_level else None
    out = []
    for scenario in amc.SCENARIOS.values():
        if allowed_severities is not None and scenario.severity not in allowed_severities:
            continue
        if days is not None and scenario.days_required > days:
            continue
        for component, s in scenario.primary_specs:
            out.append((component, s["time_offset"], s["metric"], s["description"]))
        for component, s in scenario.cascade_specs:
            out.append((component, s["time_offset"], s["metric"], s["description"]))
    return out


def primary_spec_lookup(amc, *, days=None, signal_level=None):
    """Map (component, metric, description) -> the declared primary spec dict.

    Accepts the same ``days``/``signal_level`` filters as ``declared_specs``.
    """
    allowed_severities = amc.SIGNAL_LEVELS[signal_level] if signal_level else None
    out = {}
    for scenario in amc.SCENARIOS.values():
        if allowed_severities is not None and scenario.severity not in allowed_severities:
            continue
        if days is not None and scenario.days_required > days:
            continue
        for component, s in scenario.primary_specs:
            out[(component, s["metric"], s["description"])] = s
    return out


def count_lines(path: Path) -> int:
    with open(path, "rb") as f:
        return sum(1 for _ in f)


def count_blank_lines(path: Path) -> int:
    with open(path) as f:
        return sum(1 for line in f if line.strip() == "")


def read_manifest(out_dir: Path):
    with open(out_dir / "anomalies.csv") as f:
        return list(csv.DictReader(f))


def read_component_rows(out_dir: Path, component: str):
    """Return (rows_by_timestamp, header). Blank/empty rows are filtered out."""
    rows = {}
    with open(out_dir / f"{component}.csv") as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            if not row:
                continue
            rows[row[0]] = row
    return rows, header


def natural_band(amc, spec, total_seconds, *, sigma_mult=8.0):
    """Plausible-value bounds [lo, hi] for the natural (non-anomaly) value of a metric.

    Samples the spec's multiplier/additive over a coarse grid of seconds so the
    bounds reflect the metric's daily shape, then pads by ``sigma_mult * spec.std``
    for noise. Honors ``spec.clip_min`` so clipped metrics aren't asserted below
    their floor.
    """
    sample_count = min(240, total_seconds)
    step = max(1, total_seconds // sample_count)
    sample_seconds = list(range(0, total_seconds, step))

    def _sample(fn, default):
        if fn is None:
            return [default]
        vals = []
        for sec in sample_seconds:
            ts = amc.START + datetime.timedelta(seconds=sec)
            vals.append(fn(ts, sec))
        return vals

    mults = _sample(spec.multiplier, 1.0)
    adds = _sample(spec.additive, 0.0)
    noise = sigma_mult * spec.std
    lo = spec.base * min(mults) + min(adds) - noise
    hi = spec.base * max(mults) + max(adds) + noise
    if spec.clip_min is not None:
        lo = max(lo, spec.clip_min)
    return lo, hi

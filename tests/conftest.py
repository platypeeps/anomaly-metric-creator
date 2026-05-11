import csv
import datetime
import importlib.util
import io
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "anomaly-metric-creator.py"
COMBINE_PATH = REPO_ROOT / "combine_logs.py"


def _load_amc():
    spec = importlib.util.spec_from_file_location("anomaly_metric_creator", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_combine_logs():
    """Fresh import of combine_logs.py (caller monkeypatches INPUT_DIR / OUTPUT_FILE_UNIFIED)."""
    spec = importlib.util.spec_from_file_location("combine_logs", COMBINE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_capture(amc, out_dir, *, days, seed=42, drop_rate=None):
    args = [
        "--seed", str(seed),
        "--duration-days", str(days),
        "--output-dir", str(out_dir),
    ]
    if drop_rate is not None:
        args += ["--drop-rate", str(drop_rate)]
    stderr_buf = io.StringIO()
    real_stderr = sys.stderr
    sys.stderr = stderr_buf
    try:
        amc.main(args)
    finally:
        sys.stderr = real_stderr
    return SimpleNamespace(out_dir=out_dir, stderr=stderr_buf.getvalue())


@pytest.fixture(scope="session")
def amc():
    return _load_amc()


@pytest.fixture(scope="session")
def one_day_run_a(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("one_day_a")
    return run_capture(amc, out, days=1)


@pytest.fixture(scope="session")
def one_day_run_b(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("one_day_b")
    return run_capture(amc, out, days=1)


@pytest.fixture(scope="session")
def seven_day_run(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("seven_day")
    return run_capture(amc, out, days=7)


COMPONENT_FIELDS = {
    "authservice": ("anoms_auth", 6),
    "cacheservice": ("anoms_cache", 6),
    "apigateway": ("anoms_api", 6),
    "database": ("anoms_db", 6),
    "mqservice": ("anoms_mq", 6),
    "llm_analytics": ("anoms_llm", 8),
}

COMPONENTS = list(COMPONENT_FIELDS.keys())


def declared_specs(amc):
    """Flatten every declared anomaly spec into (component, time_offset, metric, description)."""
    out = []
    for component, (attr, _) in COMPONENT_FIELDS.items():
        for s in getattr(amc, attr):
            out.append((component, s["time_offset"], s["metric"], s["description"]))
    for component, specs in amc.cascading_anomalies.items():
        for s in specs:
            out.append((component, s["time_offset"], s["metric"], s["description"]))
    return out


def primary_spec_lookup(amc):
    """Map (component, metric, description) -> the declared primary spec dict."""
    out = {}
    for component, (attr, _) in COMPONENT_FIELDS.items():
        for s in getattr(amc, attr):
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

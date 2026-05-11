import importlib.util
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "anomaly-metric-creator.py"


def _load_amc():
    spec = importlib.util.spec_from_file_location("anomaly_metric_creator", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_capture(amc, out_dir, *, days, seed=42, drop_rate=None):
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
    return _run_capture(amc, out, days=1)


@pytest.fixture(scope="session")
def one_day_run_b(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("one_day_b")
    return _run_capture(amc, out, days=1)


@pytest.fixture(scope="session")
def seven_day_run(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("seven_day")
    return _run_capture(amc, out, days=7)


COMPONENT_FIELDS = {
    "authservice": ("anoms_auth", 6),
    "cacheservice": ("anoms_cache", 6),
    "apigateway": ("anoms_api", 6),
    "database": ("anoms_db", 6),
    "mqservice": ("anoms_mq", 6),
    "llm_analytics": ("anoms_llm", 8),
}


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

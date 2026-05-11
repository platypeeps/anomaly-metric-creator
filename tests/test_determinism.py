"""Determinism invariants: same-seed reproducibility and that import is a no-op."""

import os
import subprocess
import sys

from conftest import COMPONENTS, SCRIPT_PATH


def test_determinism_byte_identical(one_day_run_a, one_day_run_b):
    """Two seed=42 runs into separate dirs produce byte-identical CSVs."""
    files = [f"{c}.csv" for c in COMPONENTS] + ["anomalies.csv"]
    for name in files:
        a = (one_day_run_a.out_dir / name).read_bytes()
        b = (one_day_run_b.out_dir / name).read_bytes()
        assert a == b, f"{name} differs between two seed=42 runs"


def test_import_does_not_run_generation(tmp_path):
    """Importing the module in a fresh interpreter must not trigger main()."""
    script = (
        "import importlib.util, os\n"
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
    assert not (tmp_path / "iot_logs").exists()

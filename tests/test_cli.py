"""CLI surface tests via subprocess.

Each test invokes ``anomaly-metric-creator.py`` as an external process so we
also exercise the ``if __name__ == "__main__"`` entry and prove no in-process
state is leaking determinism.
"""

import filecmp
import subprocess
import sys

from conftest import COMPONENTS, SCRIPT_PATH


def _invoke(*args, cwd=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def test_help_lists_every_flag():
    result = _invoke("--help")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for flag in ("--duration-days", "--seed", "--output-dir", "--drop-rate"):
        assert flag in out, f"--help missing flag {flag}"
        # Argparse renders the help text on the line following the flag; require
        # something non-trivial follows so the flag isn't just a bare token.
        after = out.split(flag, 1)[1]
        assert any(c.isalpha() for c in after[:200]), f"{flag} has empty help text"


def test_invalid_duration_days_fails(tmp_path):
    result = _invoke("--duration-days", "0", "--output-dir", str(tmp_path))
    assert result.returncode != 0, "expected non-zero exit for --duration-days 0"
    assert "duration-days" in (result.stderr + result.stdout)


def test_invalid_drop_rate_low_fails(tmp_path):
    result = _invoke("--drop-rate", "-0.1", "--output-dir", str(tmp_path))
    assert result.returncode != 0, "expected non-zero exit for --drop-rate -0.1"
    assert "drop-rate" in (result.stderr + result.stdout)


def test_invalid_drop_rate_high_fails(tmp_path):
    result = _invoke("--drop-rate", "1.5", "--output-dir", str(tmp_path))
    assert result.returncode != 0, "expected non-zero exit for --drop-rate 1.5"
    assert "drop-rate" in (result.stderr + result.stdout)


def test_output_dir_is_created(tmp_path):
    target = tmp_path / "deep" / "nested" / "iot"
    assert not target.exists()
    result = _invoke("--duration-days", "1", "--seed", "42", "--output-dir", str(target))
    assert result.returncode == 0, result.stderr
    assert target.is_dir()
    for component in COMPONENTS:
        assert (target / f"{component}.csv").exists(), f"{component}.csv not written"
    assert (target / "anomalies.csv").exists()


def test_cross_process_determinism(tmp_path):
    """Two subprocesses with the same seed produce byte-identical CSVs. Proves no
    hidden in-process state (module-level cache, lazy import side effects, etc.)
    is leaking determinism — a regression class the in-process determinism test
    can't catch.
    """
    a = tmp_path / "run_a"
    b = tmp_path / "run_b"
    for out in (a, b):
        result = _invoke("--seed", "7", "--duration-days", "1", "--output-dir", str(out))
        assert result.returncode == 0, result.stderr

    files = [f"{c}.csv" for c in COMPONENTS] + ["anomalies.csv"]
    differ = [name for name in files if not filecmp.cmp(a / name, b / name, shallow=False)]
    assert not differ, f"cross-process determinism broken for: {differ}"

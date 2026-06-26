"""Acceptance tests for `tools/check_trace_payload_antipatterns.py`.

The lint preserves the PR #140 trace import/export review lessons by blocking
direct casts and silent filtering in the trace boundary modules.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_trace_payload_antipatterns.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def _module(tmp_path: Path, body: str) -> str:
    path = tmp_path / "server_traces.py"
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_helper_based_validation_exits_zero(tmp_path: Path) -> None:
    path = _module(
        tmp_path,
        "def _trace_int_field(payload, key):\n"
        "    value = payload[key]\n"
        "    if isinstance(value, bool) or not isinstance(value, int):\n"
        "        raise ValueError(key)\n"
        "    return int(value)\n",
    )
    result = _run(path)
    assert result.returncode == 0, result.stderr


def test_direct_int_payload_exits_one(tmp_path: Path) -> None:
    path = _module(tmp_path, "def f(payload):\n    return int(payload['id'])\n")
    result = _run(path)
    assert result.returncode == 1
    assert "direct int" in result.stderr


def test_direct_int_payload_get_exits_one(tmp_path: Path) -> None:
    path = _module(tmp_path, "def f(payload):\n    return int(payload.get('id'))\n")
    result = _run(path)
    assert result.returncode == 1
    assert "direct int" in result.stderr


def test_direct_int_raw_variable_exits_one(tmp_path: Path) -> None:
    path = _module(
        tmp_path,
        "def f(payload):\n"
        "    raw_declared_count = payload.get('trace_count')\n"
        "    return int(raw_declared_count)\n",
    )
    result = _run(path)
    assert result.returncode == 1
    assert "direct int" in result.stderr


def test_direct_tuple_payload_exits_one(tmp_path: Path) -> None:
    path = _module(
        tmp_path,
        "def f(payload):\n    return tuple(payload.get('argv', ()))\n",
    )
    result = _run(path)
    assert result.returncode == 1
    assert "direct tuple" in result.stderr


def test_payload_named_locals_do_not_false_positive(tmp_path: Path) -> None:
    path = _module(
        tmp_path,
        "def f(payload_size, payload_count, payload_parts):\n"
        "    return int(payload_size) + int(payload_count) + len(tuple(payload_parts))\n",
    )
    result = _run(path)
    assert result.returncode == 0, result.stderr


def test_silent_dict_filter_exits_one(tmp_path: Path) -> None:
    path = _module(
        tmp_path,
        "def f(payload):\n"
        "    return [item for item in payload['traces'] if isinstance(item, dict)]\n",
    )
    result = _run(path)
    assert result.returncode == 1
    assert "silently filters" in result.stderr


def test_allow_marker_exempts_line(tmp_path: Path) -> None:
    path = _module(
        tmp_path,
        "def f(payload):\n"
        "    return int(payload['id'])  # trace-payload-lint: allow\n",
    )
    result = _run(path)
    assert result.returncode == 0, result.stderr


def test_no_args_exits_two() -> None:
    result = _run()
    assert result.returncode == 2


def test_syntax_error_exits_two(tmp_path: Path) -> None:
    path = _module(tmp_path, "def f(:\n    pass\n")
    result = _run(path)
    assert result.returncode == 2


def test_live_trace_boundary_modules_clean() -> None:
    files = [
        REPO_ROOT / "src" / "anomaly_metric_creator" / "server_traces.py",
        REPO_ROOT / "src" / "anomaly_metric_creator" / "trace_bundle.py",
    ]
    result = _run(*(str(path) for path in files))
    assert result.returncode == 0, result.stderr

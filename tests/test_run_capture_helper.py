"""VER-196 tests: shared ``run_capture`` helper defaults to
``--interval-seconds 60`` so new tests automatically take the cheap path,
with explicit ``interval_seconds=None`` (or ``=1.0``) for full-resolution
runs and a ``@pytest.mark.full_resolution`` marker for declaring intent."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import run_capture


def _schema_interval(out_dir: Path) -> float:
    """Read back ``interval_seconds`` from the run's ``schema.json``.

    schema.json is the most reliable post-run signal of the active
    interval — it is byte-deterministic and records the exact
    ``args.interval_seconds`` ``main()`` saw.
    """
    schema_path = out_dir / "schema.json"
    schema = json.loads(schema_path.read_text())
    return float(schema["metadata"]["interval_seconds"])


def test_default_interval_seconds_is_60(amc, tmp_path):
    """``run_capture`` defaults to ``--interval-seconds 60`` so a test
    that omits the kwarg pays the cheap 1440-rows/day cost instead of
    the historic 86400-rows/day."""
    run_capture(
        amc, tmp_path,
        days=1,
        extra_args=["--emit-selection", "metrics,schema"],
    )
    assert _schema_interval(tmp_path) == 60.0


def test_interval_seconds_none_uses_script_default(amc, tmp_path):
    """``interval_seconds=None`` skips the ``--interval-seconds`` flag so
    the script's own default (1s) applies. This is the opt-out path the
    session-scoped fixtures use to preserve their locked SHA-256 hashes."""
    run_capture(
        amc, tmp_path,
        days=1,
        extra_args=["--emit-selection", "metrics,schema"],
        interval_seconds=None,
    )
    assert _schema_interval(tmp_path) == 1.0


def test_interval_seconds_explicit_value_overrides_default(amc, tmp_path):
    """Callers can pass any positive interval. Non-default values reach
    the script through the kwarg, not the historic extra_args list."""
    run_capture(
        amc, tmp_path,
        days=1,
        extra_args=["--emit-selection", "metrics,schema"],
        interval_seconds=600,
    )
    assert _schema_interval(tmp_path) == 600.0


def test_interval_seconds_in_extra_args_raises(amc, tmp_path):
    """``--interval-seconds`` must travel via the kwarg, not extra_args,
    so the helper has a single source of truth and the
    ``@pytest.mark.full_resolution`` audit lint can recognize the opt-out
    site. Smuggling the flag through extra_args (in either the standalone
    ``--interval-seconds VALUE`` form or the ``--interval-seconds=VALUE``
    form) raises ``ValueError``."""
    with pytest.raises(ValueError, match="interval_seconds"):
        run_capture(
            amc, tmp_path,
            days=1,
            extra_args=["--interval-seconds", "5"],
        )


def test_interval_seconds_equals_form_in_extra_args_raises(amc, tmp_path):
    """The ``--interval-seconds=VALUE`` form (which ``argparse`` accepts
    as equivalent to ``--interval-seconds VALUE``) must also be rejected;
    otherwise a caller could bypass the single-source-of-truth guard by
    smuggling the flag through ``extra_args`` as a single attached
    token."""
    with pytest.raises(ValueError, match="interval_seconds"):
        run_capture(
            amc, tmp_path,
            days=1,
            extra_args=["--interval-seconds=5"],
        )


def test_full_resolution_marker_is_registered(pytestconfig):
    """The ``full_resolution`` marker is registered in pyproject.toml so
    ``pytest --strict-markers`` runs (and lint hooks) treat the marker as
    known. Tests use it to declare 'this case depends on 1s timestamps'
    alongside an explicit ``interval_seconds=None`` (or ``=1.0``)."""
    markers = pytestconfig.getini("markers")
    assert any(
        m.startswith("full_resolution") for m in markers
    ), "full_resolution marker must be registered in pyproject.toml"


@pytest.mark.full_resolution
def test_full_resolution_marker_applies_to_tests():
    """Sanity: the registered marker can be applied to a test without
    pytest emitting an 'unknown mark' warning. Pairs with the
    ``interval_seconds=None`` opt-out at call sites that need 1s rows."""
    # No assertion beyond decorator acceptance — pytest --strict-markers
    # would otherwise fail collection if the mark were unregistered.

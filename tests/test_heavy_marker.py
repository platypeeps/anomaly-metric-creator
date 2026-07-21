"""Unit coverage for the ``heavy`` auto-marker in ``conftest``.

The PR CI gate runs the light test set under real xdist
(``pytest -n 2 --dist loadfile -m "not heavy"``) and the GB-scale
heavy-fixture tests serially (``pytest -n 0 -m heavy``). That split keeps
the determinism / global-state ordering path exercised on pull requests
without parallelizing the N=3 / 7-day fixtures before the 16 GB standard
runner's memory and disk headroom has been measured.

These tests pin the marking *decision* so a regression that stops
classifying the GB-scale fixtures as heavy (which would let them run
under ``-n 2`` and reintroduce the OOM) fails here instead of only on a
CI runner. The CI ``-m heavy`` step is the second guard: if the hook
stops marking anything, that step collects zero tests and pytest exits
non-zero.
"""

from conftest import (
    _HEAVY_MODULE_FIXTURES,
    _HEAVY_SESSION_FIXTURES,
    _item_is_heavy,
)


def test_heavy_fixture_set_is_nonempty():
    # Guard the "Test path determinism" checklist rule: an empty set would
    # make `_item_is_heavy` always return False, silently routing every
    # heavy fixture into the parallel set.
    assert _HEAVY_SESSION_FIXTURES


def test_heavy_module_fixture_set_is_nonempty():
    # Same non-empty guard for the module-fixture registry: an empty set
    # would silently stop classifying the regenerating GB-scale module
    # fixtures as heavy.
    assert _HEAVY_MODULE_FIXTURES


def test_item_is_heavy_detects_each_declared_heavy_fixture():
    for fixture_name in _HEAVY_SESSION_FIXTURES | _HEAVY_MODULE_FIXTURES:
        assert _item_is_heavy(("amc", "tmp_path_factory", fixture_name)), (
            f"{fixture_name} is declared heavy but was not detected"
        )


def test_item_is_heavy_detects_gb_scale_module_fixtures():
    # The three GB-scale module fixtures the 07-06 review found escaping the
    # marker must all classify heavy: two via the module registry, and
    # seven_day_gauges_run transitively via its seven_day_run request.
    assert _item_is_heavy(("amc", "tmp_path_factory", "seven_day_schema_run"))
    assert _item_is_heavy(("amc", "tmp_path_factory", "synthetic_n3_run"))
    assert _item_is_heavy(
        ("amc", "seven_day_run", "tmp_path_factory", "seven_day_gauges_run")
    )


def test_item_is_heavy_false_for_light_fixtures():
    # one_day_run_a is a 1-day fixture that is intentionally NOT heavy: it
    # belongs in the parallel xdist smoke.
    assert not _item_is_heavy(("amc", "one_day_run_a", "tmp_path"))


def test_item_is_heavy_handles_empty_fixturenames():
    assert not _item_is_heavy(())

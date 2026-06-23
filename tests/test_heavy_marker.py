"""Unit coverage for the ``heavy`` auto-marker in ``conftest``.

The PR CI gate runs the light test set under real xdist
(``pytest -n 2 --dist loadfile -m "not heavy"``) and the GB-scale
heavy-fixture tests serially (``pytest -n 0 -m heavy``). That split keeps
the determinism / global-state ordering path exercised on pull requests
without OOM-ing the 7 GB standard runner on the N=3 / 7-day fixtures.

These tests pin the marking *decision* so a regression that stops
classifying the GB-scale fixtures as heavy (which would let them run
under ``-n 2`` and reintroduce the OOM) fails here instead of only on a
CI runner. The CI ``-m heavy`` step is the second guard: if the hook
stops marking anything, that step collects zero tests and pytest exits
non-zero.
"""

from conftest import _HEAVY_SESSION_FIXTURES, _item_is_heavy


def test_heavy_fixture_set_is_nonempty():
    # Guard the "Test path determinism" checklist rule: an empty set would
    # make `_item_is_heavy` always return False, silently routing every
    # heavy fixture into the parallel set.
    assert _HEAVY_SESSION_FIXTURES


def test_item_is_heavy_detects_each_declared_heavy_fixture():
    for fixture_name in _HEAVY_SESSION_FIXTURES:
        assert _item_is_heavy(("amc", "tmp_path_factory", fixture_name)), (
            f"{fixture_name} is declared heavy but was not detected"
        )


def test_item_is_heavy_false_for_light_fixtures():
    # one_day_run_a is a 1-day fixture that is intentionally NOT heavy: it
    # belongs in the parallel xdist smoke.
    assert not _item_is_heavy(("amc", "one_day_run_a", "tmp_path"))


def test_item_is_heavy_handles_empty_fixturenames():
    assert not _item_is_heavy(())

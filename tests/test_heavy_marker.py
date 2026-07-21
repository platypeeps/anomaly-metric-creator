"""Unit coverage for the ``heavy`` auto-marker in ``conftest``.

The PR CI gate runs both partitions under two-worker, loadfile-distributed
xdist after the isolated heavy lane cleared its hosted memory and disk
thresholds. The light selector excludes the GB-scale fixtures; the heavy
selector keeps each file's fixture work on one worker.

These tests pin the marking *decision* so a regression that stops
classifying the GB-scale fixtures as heavy (which would let them escape into
the light worker pool) fails here instead of only on a CI runner. The CI
``-m heavy`` step is the second guard: if the hook
stops marking anything, that step collects zero tests and pytest exits
non-zero.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

from conftest import (
    _HEAVY_MODULE_FIXTURES,
    _HEAVY_SESSION_FIXTURES,
    _item_is_heavy,
    _item_parametrizes_heavy_fixture,
)


def _module_fixture_definition_paths() -> dict[str, list[str]]:
    definitions: dict[str, list[str]] = {}
    tests_dir = Path(__file__).parent

    for path in tests_dir.glob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any(
                (
                    isinstance(decorator, ast.Attribute)
                    and isinstance(decorator.value, ast.Name)
                    and decorator.value.id == "pytest"
                    and decorator.attr == "fixture"
                )
                or (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name)
                    and decorator.func.value.id == "pytest"
                    and decorator.func.attr == "fixture"
                )
                for decorator in node.decorator_list
            ):
                definitions.setdefault(node.name, []).append(
                    path.relative_to(tests_dir.parent).as_posix()
                )

    return definitions


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


def test_declared_heavy_fixtures_resolve_to_real_fixtures(request):
    fixture_defs = request.session._fixturemanager._arg2fixturedefs
    for fixture_name in _HEAVY_SESSION_FIXTURES:
        assert fixture_name in fixture_defs and fixture_defs[fixture_name], (
            f"{fixture_name} is declared heavy but has no fixture definition"
        )

    module_definitions = _module_fixture_definition_paths()
    for fixture_name in _HEAVY_MODULE_FIXTURES:
        assert module_definitions.get(fixture_name), (
            f"{fixture_name} is declared heavy but has no fixture definition"
        )


def test_declared_heavy_fixture_names_have_single_definition(request):
    fixture_defs = request.session._fixturemanager._arg2fixturedefs
    module_definitions = _module_fixture_definition_paths()
    duplicates = {
        fixture_name: paths
        for fixture_name in _HEAVY_MODULE_FIXTURES
        if len(paths := module_definitions.get(fixture_name, ())) > 1
    }
    duplicates.update({
        fixture_name: sorted(fixture_def.baseid for fixture_def in definitions)
        for fixture_name in _HEAVY_SESSION_FIXTURES
        if len(definitions := fixture_defs.get(fixture_name, ())) > 1
    })

    assert not duplicates, (
        "heavy fixture registry names must resolve unambiguously; duplicate "
        f"definitions can escape fixture-closure marking: {duplicates}"
    )


def test_parametrized_heavy_fixture_names_are_marked_heavy(request):
    declared = _HEAVY_SESSION_FIXTURES | _HEAVY_MODULE_FIXTURES
    escaped = []

    for item in request.session.items:
        callspec = getattr(item, "callspec", None)
        if callspec is None:
            continue
        fixture_names = declared.intersection(
            value for value in callspec.params.values() if isinstance(value, str)
        )
        if fixture_names and item.get_closest_marker("heavy") is None:
            escaped.append((item.nodeid, sorted(fixture_names)))

    assert not escaped, (
        "parametrized heavy fixture names bypassed item.fixturenames and the "
        f"automatic marker: {escaped}"
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


def test_item_parametrizes_heavy_fixture_detects_indirect_lookup():
    item = SimpleNamespace(
        callspec=SimpleNamespace(params={"fixture_name": "seven_day_schema_run"})
    )

    assert _item_parametrizes_heavy_fixture(item)


def test_item_parametrizes_heavy_fixture_ignores_unregistered_values():
    light = SimpleNamespace(
        callspec=SimpleNamespace(params={"fixture_name": "one_day_schema_run"})
    )

    assert not _item_parametrizes_heavy_fixture(light)
    assert not _item_parametrizes_heavy_fixture(SimpleNamespace())

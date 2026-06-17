"""Tests for the installable package entrypoint."""

from __future__ import annotations

from anomaly_metric_creator import cli


def test_package_entrypoint_loads_legacy_main() -> None:
    legacy = cli._load_legacy_module()
    assert callable(legacy.main)
    assert legacy.__name__ == "anomaly_metric_creator.legacy"


def test_package_entrypoint_memoizes_legacy_module() -> None:
    # The loader exec's a ~600 KB script; functools.cache must return the
    # same module object on repeat calls instead of re-exec'ing it.
    assert cli._load_legacy_module() is cli._load_legacy_module()

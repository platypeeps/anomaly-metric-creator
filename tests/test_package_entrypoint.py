"""Tests for the installable package entrypoint."""

from __future__ import annotations

from anomaly_metric_creator import cli


def test_package_entrypoint_loads_legacy_main() -> None:
    legacy = cli._load_legacy_module()
    assert callable(legacy.main)

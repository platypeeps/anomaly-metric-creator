"""Package facade modules point at the canonical legacy implementation."""

from __future__ import annotations

import subprocess
import sys
import weakref


def test_package_facades_route_to_legacy():
    from anomaly_metric_creator import combine, legacy, models, otel, scenarios, schema

    assert combine.combine_logs is legacy.combine_logs
    assert combine.combine_logs_unified is legacy.combine_logs_unified
    assert combine.discover_components is legacy.discover_components

    assert models.MetricSpec is legacy.MetricSpec
    assert models.Instance is legacy.Instance
    assert models.RunContext is legacy.RunContext

    assert otel.stream_otel_gauges is legacy.stream_otel_gauges
    assert otel.stream_otel_signals is legacy.stream_otel_signals

    assert scenarios.Scenario is legacy.Scenario
    assert scenarios.SCENARIOS is legacy.SCENARIOS
    assert scenarios.register_cascade is legacy.register_cascade

    assert schema.SCHEMA_DOCUMENT_VERSION == legacy.SCHEMA_DOCUMENT_VERSION
    assert schema.write_schema_json is legacy.write_schema_json
    assert schema.validate_output is legacy.validate_output


def test_scenario_facade_routes_to_extracted_owners():
    from anomaly_metric_creator import legacy, scenarios
    from anomaly_metric_creator import (
        scenario_builders,
        scenario_catalog,
        scenario_validation,
        scenarios_impl,
    )

    assert scenarios.Scenario is scenario_builders.Scenario is legacy.Scenario
    assert scenarios.SCENARIOS is scenario_catalog.SCENARIOS is legacy.SCENARIOS
    assert (
        scenarios.register_cascade
        is scenario_builders.register_cascade
        is legacy.register_cascade
    )
    assert legacy._scenario_validate_registry is scenario_validation._validate_scenarios_registry
    assert legacy._scenarios_apply is scenarios_impl._apply_scenarios
    assert "legacy" not in scenario_builders.__dict__
    assert "legacy" not in scenario_catalog.__dict__
    assert "legacy" not in scenario_validation.__dict__
    assert "legacy" not in scenarios_impl.__dict__


def test_run_pipeline_uses_live_weak_legacy_namespace():
    from anomaly_metric_creator import legacy, models_impl, run_pipeline

    runtime_ref = run_pipeline._run_runtimes[legacy.__name__]
    assert isinstance(runtime_ref, weakref.ReferenceType)
    assert runtime_ref() is legacy._run_runtime_namespace
    assert legacy._EMIT_ARTIFACT_FILES is run_pipeline._EMIT_ARTIFACT_FILES
    assert legacy.RunContext is models_impl.RunContext
    assert "legacy" not in run_pipeline.__dict__


def test_scenario_registry_validation_runs_once_during_legacy_import():
    code = """
from anomaly_metric_creator import scenario_validation

calls = 0
original = scenario_validation._validate_scenarios_registry

def counting_validator(*args, **kwargs):
    global calls
    calls += 1
    return original(*args, **kwargs)

scenario_validation._validate_scenarios_registry = counting_validator
from anomaly_metric_creator import legacy  # noqa: F401
assert calls == 1, calls
"""
    subprocess.run([sys.executable, "-c", code], check=True)

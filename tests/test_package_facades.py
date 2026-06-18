"""Package facade modules point at the canonical legacy implementation."""

from __future__ import annotations


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

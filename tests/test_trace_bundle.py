import csv
import json

import pytest

from anomaly_metric_creator import trace_bundle
from anomaly_metric_creator.server_traces import (
    COMMAND_TRACE_EXPORT_VERSION,
    CommandTrace,
    unsupported_summary_from_traces,
)


def _trace(
    trace_id,
    raw_input,
    *,
    support_status="supported",
    command_family="kubectl",
    active_scenarios=("cache_leak_restart",),
    stdout="",
    stderr="",
    fingerprint="kubectl.get.pods",
    guessed_intent="Inspect pods",
    matched_rule_id="kubectl.get.pods",
):
    return CommandTrace(
        id=trace_id,
        received_at_wall_time=f"2026-06-25T12:0{trace_id}:00Z",
        simulated_time=f"2026-06-25T12:0{trace_id}:00Z",
        raw_input=raw_input,
        argv=tuple(raw_input.split()),
        client="debug-ui",
        command_family=command_family,
        verb="get",
        resource_kind="pods",
        resource_name="",
        namespace="saas-prod",
        parsed_flags={"namespace": "saas-prod"},
        support_status=support_status,
        matched_rule_id=matched_rule_id,
        active_scenarios=active_scenarios,
        exit_code=0 if support_status == "supported" else 1,
        stdout_preview=stdout[:80],
        stderr_preview=stderr[:80],
        stdout=stdout,
        stderr=stderr,
        latency_ms=12.5,
        fingerprint=fingerprint,
        guessed_intent=guessed_intent,
    )


@pytest.fixture()
def trace_bundle_path(tmp_path):
    traces = [
        _trace(
            1,
            "kubectl get pods -n saas-prod",
            stdout="apigateway-0 Running",
        ),
        _trace(
            2,
            "kubectl auth can-i get pods -n saas-prod",
            command_family="kubernetes-api",
            active_scenarios=("cache_leak_restart", "payment_latency_spike"),
            stdout="yes",
            fingerprint="selfsubjectaccessreview.create",
            matched_rule_id="kubernetes.api.authorization.selfsubjectaccessreview",
        ),
        _trace(
            3,
            "kubectl debug pod/apigateway-0 -n saas-prod",
            support_status="unsupported",
            stderr="unsupported debug command",
            fingerprint="kubectl.debug",
            guessed_intent="Open an ephemeral debug container",
            matched_rule_id="",
        ),
        _trace(
            4,
            "helm get manifest checkout -n saas-prod",
            support_status="partial",
            command_family="helm",
            active_scenarios=("payment_latency_spike",),
            stdout="partial manifest",
            fingerprint="helm.get.manifest",
            guessed_intent="Inspect rendered release manifest",
            matched_rule_id="helm.get.partial",
        ),
    ]
    payload = {
        "kind": "CommandTraceExport",
        "apiVersion": f"amc.simulator/v{COMMAND_TRACE_EXPORT_VERSION}",
        "schema_version": COMMAND_TRACE_EXPORT_VERSION,
        "trace_count": len(traces),
        "traces": [trace.to_dict() for trace in traces],
    }
    path = tmp_path / "command-traces.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_trace_bundle_and_summary_counts_exported_traces(trace_bundle_path):
    bundle = trace_bundle.load_trace_bundle(trace_bundle_path)

    summary = trace_bundle.summarize_trace_bundle(bundle)

    assert summary["trace_count"] == 4
    assert summary["support_status_counts"] == {
        "partial": 1,
        "supported": 2,
        "unsupported": 1,
    }
    assert summary["command_family_counts"] == {
        "helm": 1,
        "kubectl": 2,
        "kubernetes-api": 1,
    }
    assert summary["scenario_counts"] == {
        "cache_leak_restart": 3,
        "payment_latency_spike": 2,
    }
    assert summary["top_unsupported"][0]["fingerprint"] == "helm.get.manifest"
    assert summary["top_unsupported"][1]["fingerprint"] == "kubectl.debug"


def test_summarize_trace_bundle_uses_timestamp_bounds_for_reordered_traces(tmp_path):
    traces = [
        _trace(4, "helm get manifest checkout -n saas-prod").to_dict(),
        _trace(1, "kubectl get pods -n saas-prod").to_dict(),
    ]
    payload = {
        "kind": "CommandTraceExport",
        "apiVersion": f"amc.simulator/v{COMMAND_TRACE_EXPORT_VERSION}",
        "schema_version": COMMAND_TRACE_EXPORT_VERSION,
        "trace_count": len(traces),
        "traces": traces,
    }
    path = tmp_path / "reordered-command-traces.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    summary = trace_bundle.summarize_trace_bundle(trace_bundle.load_trace_bundle(path))

    assert summary["first_seen"] == "2026-06-25T12:01:00Z"
    assert summary["last_seen"] == "2026-06-25T12:04:00Z"


def test_search_trace_bundle_reuses_server_filters(trace_bundle_path):
    bundle = trace_bundle.load_trace_bundle(trace_bundle_path)

    search = trace_bundle.search_trace_bundle(
        bundle,
        query="auth can-i",
        support_status="supported",
        command_family="kubernetes-api",
        scenario_id="payment_latency_spike",
    )

    assert search["total"] == 1
    assert search["items"][0]["raw_input"] == (
        "kubectl auth can-i get pods -n saas-prod"
    )
    assert search["search_backend"] == "bundle"


def test_search_trace_bundle_applies_limit_and_offset_in_recent_first_order(
    trace_bundle_path,
):
    bundle = trace_bundle.load_trace_bundle(trace_bundle_path)

    search = trace_bundle.search_trace_bundle(bundle, limit=2, offset=1)

    assert search["total"] == 4
    assert [item["id"] for item in search["items"]] == [3, 2]


def test_unsupported_summary_groups_partial_and_unsupported_traces(trace_bundle_path):
    bundle = trace_bundle.load_trace_bundle(trace_bundle_path)

    summary = trace_bundle.unsupported_trace_summary(bundle)

    assert [group["fingerprint"] for group in summary] == [
        "helm.get.manifest",
        "kubectl.debug",
    ]
    assert summary[0]["support_statuses"] == {"partial": 1}
    assert summary[1]["examples"][0]["raw_input"] == (
        "kubectl debug pod/apigateway-0 -n saas-prod"
    )


def test_unsupported_summary_uses_timestamp_bounds_for_newest_first_traces():
    traces = [
        _trace(
            2,
            "kubectl debug pod/apigateway-0 -n saas-prod",
            support_status="unsupported",
            fingerprint="kubectl.debug",
        ),
        _trace(
            1,
            "kubectl debug pod/database-0 -n saas-prod",
            support_status="unsupported",
            fingerprint="kubectl.debug",
        ),
    ]

    summary = unsupported_summary_from_traces(traces)

    assert summary[0]["first_seen"] == "2026-06-25T12:01:00Z"
    assert summary[0]["last_seen"] == "2026-06-25T12:02:00Z"


def test_write_trace_bundle_csv_flattens_searchable_trace_fields(
    trace_bundle_path,
    tmp_path,
):
    bundle = trace_bundle.load_trace_bundle(trace_bundle_path)
    output = tmp_path / "traces.csv"

    written = trace_bundle.write_trace_bundle_csv(bundle, output)

    assert written == 4
    rows = list(csv.DictReader(output.open(encoding="utf-8")))
    assert rows[0]["id"] == "1"
    assert rows[0]["raw_input"] == "kubectl get pods -n saas-prod"
    assert rows[0]["active_scenarios"] == "cache_leak_restart"
    assert rows[2]["support_status"] == "unsupported"
    assert rows[3]["command_family"] == "helm"


def test_trace_bundle_cli_emits_json_summary(trace_bundle_path, capsys):
    trace_bundle.main(["summary", str(trace_bundle_path), "--format", "json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["trace_count"] == 4
    assert payload["top_unsupported"][0]["fingerprint"] == "helm.get.manifest"


def test_trace_bundle_cli_search_outputs_json_matches(trace_bundle_path, capsys):
    trace_bundle.main([
        "search",
        str(trace_bundle_path),
        "--status",
        "partial",
        "--format",
        "json",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 1
    assert payload["items"][0]["raw_input"] == "helm get manifest checkout -n saas-prod"


def test_trace_bundle_cli_rejects_non_export_payload(tmp_path):
    path = tmp_path / "not-a-bundle.json"
    path.write_text(json.dumps({"traces": []}), encoding="utf-8")

    with pytest.raises(SystemExit):
        trace_bundle.main(["summary", str(path)])


def test_load_trace_bundle_rejects_non_object_trace_entries(tmp_path):
    payload = {
        "kind": "CommandTraceExport",
        "apiVersion": f"amc.simulator/v{COMMAND_TRACE_EXPORT_VERSION}",
        "schema_version": COMMAND_TRACE_EXPORT_VERSION,
        "traces": [
            _trace(1, "kubectl get pods -n saas-prod").to_dict(),
            "not-a-trace-object",
        ],
    }
    path = tmp_path / "malformed-command-traces.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="trace entry 1 must be an object"):
        trace_bundle.load_trace_bundle(path)


def test_load_trace_bundle_rejects_invalid_trace_count(tmp_path):
    payload = {
        "kind": "CommandTraceExport",
        "apiVersion": f"amc.simulator/v{COMMAND_TRACE_EXPORT_VERSION}",
        "schema_version": COMMAND_TRACE_EXPORT_VERSION,
        "trace_count": None,
        "traces": [_trace(1, "kubectl get pods -n saas-prod").to_dict()],
    }
    path = tmp_path / "invalid-trace-count.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="trace bundle trace_count must be an integer"):
        trace_bundle.load_trace_bundle(path)


def test_load_trace_bundle_rejects_wrong_api_version(tmp_path):
    payload = {
        "kind": "CommandTraceExport",
        "apiVersion": "amc.simulator/v999",
        "schema_version": COMMAND_TRACE_EXPORT_VERSION,
        "trace_count": 1,
        "traces": [_trace(1, "kubectl get pods -n saas-prod").to_dict()],
    }
    path = tmp_path / "wrong-api-version.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported trace bundle apiVersion"):
        trace_bundle.load_trace_bundle(path)


def test_load_trace_bundle_rejects_boolean_trace_count(tmp_path):
    payload = {
        "kind": "CommandTraceExport",
        "apiVersion": f"amc.simulator/v{COMMAND_TRACE_EXPORT_VERSION}",
        "schema_version": COMMAND_TRACE_EXPORT_VERSION,
        "trace_count": True,
        "traces": [_trace(1, "kubectl get pods -n saas-prod").to_dict()],
    }
    path = tmp_path / "boolean-trace-count.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="trace bundle trace_count must be an integer"):
        trace_bundle.load_trace_bundle(path)

import csv
import dataclasses
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


def test_search_trace_bundle_sorts_reordered_bundle_by_trace_id(tmp_path):
    traces = [
        _trace(1, "kubectl get pods -n saas-prod").to_dict(),
        _trace(4, "helm get manifest checkout -n saas-prod").to_dict(),
        _trace(2, "kubectl auth can-i get pods -n saas-prod").to_dict(),
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
    bundle = trace_bundle.load_trace_bundle(path)

    search = trace_bundle.search_trace_bundle(bundle, limit=2)

    assert search["total"] == 3
    assert [item["id"] for item in search["items"]] == [4, 2]


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


def test_load_trace_bundle_rejects_invalid_trace_object(tmp_path):
    trace_payload = _trace(1, "kubectl get pods -n saas-prod").to_dict()
    del trace_payload["raw_input"]
    payload = {
        "kind": "CommandTraceExport",
        "apiVersion": f"amc.simulator/v{COMMAND_TRACE_EXPORT_VERSION}",
        "schema_version": COMMAND_TRACE_EXPORT_VERSION,
        "traces": [trace_payload],
    }
    path = tmp_path / "invalid-trace-object.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="trace entry 0 is invalid"):
        trace_bundle.load_trace_bundle(path)


@pytest.mark.parametrize("trace_count", [None, "1"])
def test_load_trace_bundle_rejects_invalid_trace_count(tmp_path, trace_count):
    payload = {
        "kind": "CommandTraceExport",
        "apiVersion": f"amc.simulator/v{COMMAND_TRACE_EXPORT_VERSION}",
        "schema_version": COMMAND_TRACE_EXPORT_VERSION,
        "trace_count": trace_count,
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


@pytest.mark.parametrize("schema_version", [True, "1"])
def test_load_trace_bundle_rejects_non_integer_schema_version(
    tmp_path, schema_version
):
    payload = {
        "kind": "CommandTraceExport",
        "apiVersion": f"amc.simulator/v{COMMAND_TRACE_EXPORT_VERSION}",
        "schema_version": schema_version,
        "trace_count": 1,
        "traces": [_trace(1, "kubectl get pods -n saas-prod").to_dict()],
    }
    path = tmp_path / "non-integer-schema-version.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        ValueError, match="trace bundle schema_version must be an integer"
    ):
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


# --- A-018: CSV formula-injection neutralization -------------------------------


def _bundle_file(tmp_path, traces, name="formula-traces.json"):
    payload = {
        "kind": "CommandTraceExport",
        "apiVersion": f"amc.simulator/v{COMMAND_TRACE_EXPORT_VERSION}",
        "schema_version": COMMAND_TRACE_EXPORT_VERSION,
        "trace_count": len(traces),
        "traces": [trace.to_dict() for trace in traces],
    }
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _export_single_trace(tmp_path, trace):
    bundle = trace_bundle.load_trace_bundle(_bundle_file(tmp_path, [trace]))
    output = tmp_path / "traces.csv"
    trace_bundle.write_trace_bundle_csv(bundle, output)
    return list(csv.DictReader(output.open(encoding="utf-8")))[0]


# The trigger set is the OWASP CSV-injection list the writer guards against.
_TRIGGERS = ["=", "+", "-", "@", "\t", "\r"]

# Columns a recorded command can influence. Deliberately includes the
# non-obvious ones (fingerprint, matched_rule_id, resource identifiers,
# previews, guessed_intent), not just the free-text raw_input.
_USER_INFLUENCED_FIELDS = [
    "raw_input",
    "client",
    "command_family",
    "verb",
    "resource_kind",
    "resource_name",
    "namespace",
    "support_status",
    "matched_rule_id",
    "fingerprint",
    "guessed_intent",
    "stdout_preview",
    "stderr_preview",
]


@pytest.mark.parametrize("trigger", _TRIGGERS)
@pytest.mark.parametrize("field", _USER_INFLUENCED_FIELDS)
def test_write_trace_bundle_csv_neutralizes_trigger_in_every_column(
    tmp_path, trigger, field
):
    payload = f"{trigger}cmd|' /C calc'!A0"
    trace = dataclasses.replace(
        _trace(1, "kubectl get pods -n saas-prod"), **{field: payload}
    )

    row = _export_single_trace(tmp_path, trace)

    # Assert the first byte is the guard and the payload survived intact. Exact
    # equality would fail for "\r", which csv.reader normalizes to "\n" on
    # read-back inside a quoted field — a reader artifact, not a writer bug.
    assert row[field].startswith("'")
    assert row[field].endswith("cmd|' /C calc'!A0")


def test_write_trace_bundle_csv_neutralizes_active_scenarios_cell(tmp_path):
    trace = dataclasses.replace(
        _trace(1, "kubectl get pods -n saas-prod"),
        active_scenarios=("=cmd|calc", "cache_leak_restart"),
    )

    row = _export_single_trace(tmp_path, trace)

    assert row["active_scenarios"] == "'=cmd|calc,cache_leak_restart"


def test_write_trace_bundle_csv_neutralizes_every_string_cell(tmp_path):
    """Enumeration-proof: no user-influenced column may be left unguarded.

    A named-subset allowlist rots the moment a column is added, so this asserts
    the writer boundary itself, not a hand-maintained list of columns.
    """
    trace = _trace(1, "=malicious")
    string_fields = {
        f.name: "=payload"
        for f in dataclasses.fields(trace)
        if isinstance(getattr(trace, f.name), str)
    }
    row = _export_single_trace(tmp_path, dataclasses.replace(trace, **string_fields))

    unguarded = {
        column: value
        for column, value in row.items()
        if value == "=payload"
    }
    assert unguarded == {}


def test_write_trace_bundle_csv_leaves_benign_cells_unchanged(tmp_path):
    trace = _trace(1, "kubectl get pods -n saas-prod")

    row = _export_single_trace(tmp_path, trace)

    assert row["id"] == "1"
    assert row["exit_code"] == "0"
    assert row["latency_ms"] == "12.5"
    assert row["raw_input"] == "kubectl get pods -n saas-prod"
    assert row["received_at_wall_time"] == "2026-06-25T12:01:00Z"


def test_neutralize_csv_cell_is_idempotent():
    once = trace_bundle._neutralize_csv_cell("=cmd|calc")
    twice = trace_bundle._neutralize_csv_cell(once)

    assert once == "'=cmd|calc"
    assert twice == once


def test_neutralize_csv_cell_passes_through_non_strings():
    assert trace_bundle._neutralize_csv_cell(0) == 0
    assert trace_bundle._neutralize_csv_cell(-3) == -3
    assert trace_bundle._neutralize_csv_cell(12.5) == 12.5


def test_trace_bundle_import_path_reads_json_not_csv(tmp_path):
    """Neutralization is an export-boundary concern; stored traces stay verbatim."""
    trace = dataclasses.replace(
        _trace(1, "kubectl get pods -n saas-prod"), raw_input="=cmd|calc"
    )
    bundle = trace_bundle.load_trace_bundle(_bundle_file(tmp_path, [trace]))

    assert bundle.traces[0].raw_input == "=cmd|calc"


# --- A-070: trace-bundle version policy ---------------------------------------


def test_load_trace_bundle_version_error_states_the_version_policy(tmp_path):
    payload = {
        "kind": "CommandTraceExport",
        "apiVersion": f"amc.simulator/v{COMMAND_TRACE_EXPORT_VERSION}",
        "schema_version": COMMAND_TRACE_EXPORT_VERSION + 1,
        "trace_count": 1,
        "traces": [_trace(1, "kubectl get pods -n saas-prod").to_dict()],
    }
    path = tmp_path / "future-schema-version.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        trace_bundle.load_trace_bundle(path)

    message = str(excinfo.value)
    assert "read by the tool version that wrote them" in message
    assert "re-export" in message

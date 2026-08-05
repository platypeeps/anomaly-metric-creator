"""MCP facade core: JSON-RPC handshake, tools/list, read-only telemetry tools.

Covers the stateless streamable-HTTP layer in ``server_mcp.py`` (dispatch,
error codes, notification handling), the four v1 read-only tools, the
`POST /mcp` wiring on the live HTTP server, and the day-one ground-truth
wall (no tool response may carry scenario slugs or anomaly descriptions).
"""

import datetime as _dt
import json
import urllib.error
import urllib.request

import pytest

from anomaly_metric_creator import server, server_mcp

_COMPONENTS = "apigateway,cacheservice,database,authservice"


def _build_state(amc, out_dir, *, seed="42"):
    argv = [
        "--duration-days", "1",
        "--seed", seed,
        "--components", _COMPONENTS,
        "--output-dir", str(out_dir),
        "--interval-seconds", "3600",
    ]
    amc.main(argv)
    args = amc.parse_args(argv)
    return server.build_state(amc, args)


@pytest.fixture(scope="module")
def mcp_state(amc, tmp_path_factory):
    return _build_state(amc, tmp_path_factory.mktemp("mcp_core"))


def _rpc(state, method, params=None, *, req_id=1):
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        payload["params"] = params
    return server_mcp.handle_mcp_http_post(
        state, json.dumps(payload).encode("utf-8")
    )


def _call_tool(state, name, arguments=None):
    status, body = _rpc(
        state, "tools/call", {"name": name, "arguments": arguments or {}}
    )
    assert status == 200
    assert "error" not in body, body
    return body["result"]


def _epoch_ms(dt):
    return int(dt.replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)


# ------------------------------------------------------------------
# JSON-RPC layer
# ------------------------------------------------------------------

def test_initialize_negotiates_protocol_version(mcp_state):
    status, body = _rpc(
        mcp_state, "initialize",
        {"protocolVersion": server_mcp.MCP_PROTOCOL_VERSION,
         "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
    )
    assert status == 200
    result = body["result"]
    assert result["protocolVersion"] == server_mcp.MCP_PROTOCOL_VERSION
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == server_mcp.SERVER_NAME

    # Unsupported requested version falls back to the server default.
    status, body = _rpc(
        mcp_state, "initialize", {"protocolVersion": "1899-01-01"}
    )
    assert body["result"]["protocolVersion"] == server_mcp.MCP_PROTOCOL_VERSION


def test_notification_returns_202_with_no_body(mcp_state):
    payload = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    status, body = server_mcp.handle_mcp_http_post(
        mcp_state, json.dumps(payload).encode("utf-8")
    )
    assert status == 202
    assert body is None


def test_ping_round_trips(mcp_state):
    status, body = _rpc(mcp_state, "ping")
    assert status == 200
    assert body["result"] == {}


def test_parse_error_and_invalid_request_shapes(mcp_state):
    status, body = server_mcp.handle_mcp_http_post(mcp_state, b"{not json")
    assert status == 200
    assert body["error"]["code"] == server_mcp.PARSE_ERROR
    assert body["id"] is None

    status, body = server_mcp.handle_mcp_http_post(mcp_state, b"[1, 2]")
    assert body["error"]["code"] == server_mcp.INVALID_REQUEST


def test_unknown_method_and_unknown_tool_error_codes(mcp_state):
    status, body = _rpc(mcp_state, "resources/list")
    assert body["error"]["code"] == server_mcp.METHOD_NOT_FOUND

    status, body = _rpc(
        mcp_state, "tools/call", {"name": "no_such_tool", "arguments": {}}
    )
    assert body["error"]["code"] == server_mcp.INVALID_PARAMS
    assert "no_such_tool" in body["error"]["message"]


def test_tools_list_is_sorted_and_stable(mcp_state):
    status, body = _rpc(mcp_state, "tools/list")
    tools = body["result"]["tools"]
    names = [t["name"] for t in tools]
    assert names == sorted(names)
    assert set(names) == {
        "get_current_time", "list_components", "get_topology",
        "get_metric_histogram", "list_metric_fields",
        "group_metrics_by_field", "get_correlated_timeline",
        "get_logs", "deduplicate_logs",
        "kubectl_get", "describe_resource", "get_pod_logs",
        "get_events", "helm_status", "helm_history",
    }
    for tool in tools:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"

    _status, again = _rpc(mcp_state, "tools/list")
    assert json.dumps(body["result"], sort_keys=True) == json.dumps(
        again["result"], sort_keys=True
    )


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------

def test_get_current_time_reflects_paused_seek(amc, tmp_path):
    state = _build_state(amc, tmp_path)
    state.clock.pause()
    state.clock.seek("2026-03-01T05:00:00Z")

    result = _call_tool(state, "get_current_time")
    payload = result["structuredContent"]
    assert result["isError"] is False
    assert payload["now"].startswith("2026-03-01T05:00:00")
    expected_ms = _epoch_ms(_dt.datetime(2026, 3, 1, 5, 0, 0))
    assert payload["epoch_ms"] == expected_ms
    assert payload["one_hour_ago"].startswith("2026-03-01T04:00:00")
    assert payload["one_day_ago"].startswith("2026-02-28T05:00:00")


def test_list_components_matches_effective_specs(amc, mcp_state):
    payload = _call_tool(mcp_state, "list_components")["structuredContent"]
    by_name = {c["name"]: c for c in payload["components"]}
    expected_components = set(_COMPONENTS.split(","))
    assert expected_components  # non-empty guard
    assert set(by_name) == expected_components

    for name, entry in by_name.items():
        expected = [
            spec.name
            for spec in amc.COMPONENTS[name][: amc.DEFAULT_METRICS_PER_COMPONENT[name]]
        ]
        assert expected  # non-empty guard
        assert [m["name"] for m in entry["metrics"]] == expected
        for metric in entry["metrics"]:
            assert set(metric) == {
                "name", "unit", "semantic_type", "dtype",
                "min_value", "max_value", "derivation",
            }


def test_get_topology_restricted_to_active_components(amc, mcp_state):
    payload = _call_tool(mcp_state, "get_topology")["structuredContent"]
    topology = payload["topology"]
    active = set(_COMPONENTS.split(","))
    expected_sources = {
        source for source, edges in amc.TOPOLOGY.items()
        if source in active and any(e.target in active for e in edges)
    }
    assert expected_sources  # non-empty guard
    assert set(topology) == expected_sources
    for source, edges in topology.items():
        assert edges
        for edge in edges:
            assert edge["target"] in active


def test_get_metric_histogram_counts_match_csv(amc, mcp_state):
    component = "apigateway"
    metric = amc.COMPONENTS[component][0].name
    start = amc.START
    from_ms = _epoch_ms(start)
    to_ms = _epoch_ms(start + _dt.timedelta(days=1))

    payload = _call_tool(mcp_state, "get_metric_histogram", {
        "component": component, "metric": metric,
        "from_ms": from_ms, "to_ms": to_ms,
    })["structuredContent"]

    csv_path = mcp_state.output_dir / f"{component}.csv"
    with csv_path.open(encoding="utf-8") as f:
        next(f)
        data_rows = sum(1 for _ in f)
    assert data_rows > 0  # non-empty guard

    assert payload["granularity_ms"] in server_mcp.GRANULARITY_LADDER_MS
    assert sum(b["count"] for b in payload["buckets"]) == data_rows
    non_empty = [b for b in payload["buckets"] if b["count"]]
    assert non_empty
    for bucket in non_empty:
        assert bucket["min"] <= bucket["mean"] <= bucket["max"]


def test_get_metric_histogram_out_of_range_window_is_empty_not_error(mcp_state):
    payload = _call_tool(mcp_state, "get_metric_histogram", {
        "component": "apigateway",
        "metric": "requests_per_sec",
        "from_ms": 0,
        "to_ms": 86_400_000,  # 1970: far before the synthetic day
    })["structuredContent"]
    assert sum(b["count"] for b in payload["buckets"]) == 0


def test_get_metric_histogram_argument_validation(mcp_state):
    for bad_args, needle in [
        ({"component": "apigateway", "metric": "requests_per_sec",
          "from_ms": 10, "to_ms": 10}, "from_ms"),
        ({"component": "nope", "metric": "requests_per_sec",
          "from_ms": 0, "to_ms": 1000}, "nope"),
        ({"component": "apigateway", "metric": "nope",
          "from_ms": 0, "to_ms": 1000}, "nope"),
    ]:
        result = _call_tool(mcp_state, "get_metric_histogram", bad_args)
        assert result["isError"] is True
        assert needle in result["content"][0]["text"]


def test_window_tools_extreme_epoch_ms_is_validated_not_internal_error(mcp_state):
    # An extreme (but type-valid) epoch-ms window overflows the datetime /
    # timedelta arithmetic the A-039 lexicographic gate introduced. It must
    # degrade to a validated argument error (McpToolError -> INVALID_PARAMS),
    # not an unhandled OverflowError read as an internal tool bug.
    extreme = 10**25  # far beyond timedelta's representable range
    for tool, extra in [
        ("get_metric_histogram",
         {"component": "apigateway", "metric": "requests_per_sec"}),
        ("group_metrics_by_field", {"field": "component"}),
        ("get_correlated_timeline", {}),
    ]:
        result = _call_tool(mcp_state, tool, {
            **extra, "from_ms": extreme, "to_ms": extreme + 86_400_000,
        })
        assert result["isError"] is True, tool
        assert "representable" in result["content"][0]["text"], tool


# ------------------------------------------------------------------
# A-039: window pre-filter (lexicographic string gate + layout-gated break)
#
# The scan tools string-gate each CSV row before parsing and, on the
# dimensionless (wide) layout only, break past the window's upper bound.
# These tests prove the gate/break never drops an in-window row by
# comparing the tool output to a from-scratch full scan (no break) — for
# both the wide layout (break path) and the dim-aware long-form layout
# (per-instance blocks, where a break would wrongly skip later instances'
# in-window rows).
# ------------------------------------------------------------------

def _csv_window_count_sum(amc, csv_path, metric, from_ms, to_ms):
    """Full-scan oracle: (count, sum) of in-window cells for ``metric``.

    Parses every data row with no break and no string gate, mirroring the
    pre-A-039 behavior the tools must remain byte-identical to.
    """
    with csv_path.open(encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split(",")
        col = header.index(metric)
        count = 0
        total = 0.0
        for line in f:
            row = line.rstrip("\n").split(",")
            if len(row) <= col:
                continue
            ms = int(
                amc._parse_csv_timestamp(row[0])
                .replace(tzinfo=_dt.timezone.utc)
                .timestamp()
                * 1000
            )
            if not from_ms <= ms < to_ms:
                continue
            cell = row[col]
            if not cell:
                continue
            count += 1
            total += float(cell)
    return count, total


def _histogram_totals(payload):
    buckets = payload["buckets"]
    count = sum(b["count"] for b in buckets)
    total = sum(b["mean"] * b["count"] for b in buckets if b["count"])
    return count, total


def test_get_metric_histogram_mid_window_break_path_matches_full_scan(amc, mcp_state):
    # Wide (dimensionless) layout: the break fires partway through the day.
    # A mid-day window forces the break to skip later rows; the totals must
    # still equal a no-break full scan.
    component = "apigateway"
    metric = amc.COMPONENTS[component][0].name
    start = amc.START
    from_ms = _epoch_ms(start + _dt.timedelta(hours=9))
    to_ms = _epoch_ms(start + _dt.timedelta(hours=12))

    payload = _call_tool(mcp_state, "get_metric_histogram", {
        "component": component, "metric": metric,
        "from_ms": from_ms, "to_ms": to_ms,
    })["structuredContent"]

    csv_path = mcp_state.output_dir / f"{component}.csv"
    expected_count, expected_sum = _csv_window_count_sum(
        amc, csv_path, metric, from_ms, to_ms
    )
    assert expected_count > 0  # non-empty guard
    got_count, got_sum = _histogram_totals(payload)
    assert got_count == expected_count
    assert got_sum == pytest.approx(expected_sum)


def test_get_metric_histogram_late_window_long_form_keeps_all_instances(amc, mcp_n2_state):
    # Dim-aware long-form CSV: rows are per-instance blocks, so timestamps
    # reset at each block. A late window lands in each block's tail; the
    # no-break gate must scan every block, so the totals cover BOTH
    # instances (a wrong break would stop inside instance 0's block and
    # drop instance 1's in-window rows entirely).
    component = "apigateway"
    metric = amc.COMPONENTS[component][0].name
    start = amc.START
    from_ms = _epoch_ms(start + _dt.timedelta(hours=21))
    to_ms = _epoch_ms(start + _dt.timedelta(days=1))

    payload = _call_tool(mcp_n2_state, "get_metric_histogram", {
        "component": component, "metric": metric,
        "from_ms": from_ms, "to_ms": to_ms,
    })["structuredContent"]

    csv_path = mcp_n2_state.output_dir / f"{component}.csv"
    # Sanity: the CSV really is the dim-aware long-form layout.
    with csv_path.open(encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split(",")
    dim_cols, _metric_cols = amc._classify_component_csv_header(header)
    assert dim_cols  # long-form layout under test

    expected_count, expected_sum = _csv_window_count_sum(
        amc, csv_path, metric, from_ms, to_ms
    )
    assert expected_count > 0
    got_count, got_sum = _histogram_totals(payload)
    assert got_count == expected_count
    assert got_sum == pytest.approx(expected_sum)
    # Two instances each contribute their in-window rows.
    assert got_count == 2 * (expected_count // 2)
    assert expected_count % 2 == 0


def test_group_metrics_by_pod_late_window_long_form_keeps_all_instances(amc, mcp_n2_state):
    # Same no-break guarantee via the group_metrics scan site: a late
    # window must still see both pods (instance blocks), each with the same
    # per-instance in-window row count.
    start = amc.START
    from_ms = _epoch_ms(start + _dt.timedelta(hours=21))
    to_ms = _epoch_ms(start + _dt.timedelta(days=1))

    payload = _call_tool(mcp_n2_state, "group_metrics_by_field", {
        "field": "pod",
        "from_ms": from_ms, "to_ms": to_ms,
        "agg": "count",
    })["structuredContent"]

    counts = {b["value"]: b["count"] for b in payload["buckets"]}
    assert set(counts) == {"pod-0", "pod-1"}
    assert counts["pod-0"] == counts["pod-1"]
    assert counts["pod-0"] > 0


# ------------------------------------------------------------------
# Analysis tools (phase 2)
# ------------------------------------------------------------------

@pytest.fixture(scope="module")
def mcp_n2_state(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("mcp_n2")
    argv = [
        "--duration-days", "1",
        "--seed", "42",
        "--components", _COMPONENTS,
        "--output-dir", str(out),
        "--interval-seconds", "3600",
        "--instances-per-component", "2",
    ]
    amc.main(argv)
    args = amc.parse_args(argv)
    return server.build_state(amc, args)


def _day_window(amc):
    start = amc.START
    return _epoch_ms(start), _epoch_ms(start + _dt.timedelta(days=1))


def test_list_metric_fields_dimensionless_and_dimensioned(amc, mcp_state, mcp_n2_state):
    payload = _call_tool(mcp_state, "list_metric_fields")["structuredContent"]
    assert payload["dimensioned"] is False
    assert [f["name"] for f in payload["fields"]] == ["component", "metric"]

    payload = _call_tool(mcp_n2_state, "list_metric_fields")["structuredContent"]
    assert payload["dimensioned"] is True
    expected = ["component", "metric", *amc._INSTANCE_DIMENSION_COLUMNS]
    assert expected  # non-empty guard
    assert [f["name"] for f in payload["fields"]] == expected


def test_group_by_component_counts_match_csv_cells(amc, mcp_state):
    from_ms, to_ms = _day_window(amc)
    payload = _call_tool(mcp_state, "group_metrics_by_field", {
        "field": "component", "from_ms": from_ms, "to_ms": to_ms,
    })["structuredContent"]
    by_value = {b["value"]: b["count"] for b in payload["buckets"]}

    expected = {}
    for component in _COMPONENTS.split(","):
        path = mcp_state.output_dir / f"{component}.csv"
        with path.open(encoding="utf-8") as f:
            next(f)
            cells = sum(
                1
                for line in f
                for cell in line.rstrip("\n").split(",")[1:]
                if cell
            )
        expected[component] = cells
    assert expected and all(expected.values())  # non-empty guard
    assert by_value == expected
    assert payload["truncated"] is False
    counts = [b["count"] for b in payload["buckets"]]
    assert counts == sorted(counts, reverse=True)


def test_group_by_aggregates_avg_and_sum(amc, mcp_state):
    from_ms, to_ms = _day_window(amc)
    metric = "requests_per_sec"
    payload = _call_tool(mcp_state, "group_metrics_by_field", {
        "field": "component", "from_ms": from_ms, "to_ms": to_ms,
        "metric": metric, "agg": "avg",
    })["structuredContent"]
    assert [b["value"] for b in payload["buckets"]] == ["apigateway"]
    bucket = payload["buckets"][0]

    path = mcp_state.output_dir / "apigateway.csv"
    with path.open(encoding="utf-8") as f:
        header = next(f).rstrip("\n").split(",")
        col = header.index(metric)
        values = [
            float(line.rstrip("\n").split(",")[col])
            for line in f
            if line.rstrip("\n").split(",")[col]
        ]
    assert values  # non-empty guard
    assert bucket["count"] == len(values)
    assert bucket["agg_value"] == pytest.approx(sum(values) / len(values))

    payload = _call_tool(mcp_state, "group_metrics_by_field", {
        "field": "component", "from_ms": from_ms, "to_ms": to_ms,
        "metric": metric, "agg": "p95",
    })["structuredContent"]
    p95 = payload["buckets"][0]["agg_value"]
    assert min(values) <= p95 <= max(values)


def test_group_by_dimension_field_on_n2_run(amc, mcp_n2_state):
    from_ms, to_ms = _day_window(amc)
    payload = _call_tool(mcp_n2_state, "group_metrics_by_field", {
        "field": "pod", "from_ms": from_ms, "to_ms": to_ms,
    })["structuredContent"]
    values = {b["value"] for b in payload["buckets"]}
    assert values == {"pod-0", "pod-1"}
    counts = {b["count"] for b in payload["buckets"]}
    assert len(counts) == 1  # symmetric fan-out: same measurement count per pod


def test_group_by_limit_flags_truncation(amc, mcp_state):
    from_ms, to_ms = _day_window(amc)
    payload = _call_tool(mcp_state, "group_metrics_by_field", {
        "field": "component", "from_ms": from_ms, "to_ms": to_ms, "limit": 1,
    })["structuredContent"]
    assert len(payload["buckets"]) == 1
    assert payload["truncated"] is True


def test_group_by_argument_validation(mcp_state):
    result = _call_tool(mcp_state, "group_metrics_by_field", {
        "field": "pod", "from_ms": 0, "to_ms": 1000,
    })
    assert result["isError"] is True  # dimensionless run has no pod field
    result = _call_tool(mcp_state, "group_metrics_by_field", {
        "field": "component", "from_ms": 0, "to_ms": 1000, "agg": "avg",
    })
    assert result["isError"] is True  # value agg requires a metric
    assert "metric" in result["content"][0]["text"]


def test_correlated_timeline_surfaces_excursions_without_ground_truth(amc, mcp_state):
    from_ms, to_ms = _day_window(amc)
    payload = _call_tool(mcp_state, "get_correlated_timeline", {
        "from_ms": from_ms, "to_ms": to_ms,
    })["structuredContent"]

    assert payload["timeline"]  # the planted day has detectable excursions
    stamps = [e["timestamp_ms"] for e in payload["timeline"]]
    assert stamps == sorted(stamps)
    for event in payload["timeline"]:
        assert event["component"] in _COMPONENTS.split(",")
        assert abs(event["z_score"]) >= payload["sensitivity"]

    blob = json.dumps(payload, sort_keys=True)
    slugs = set(amc.SCENARIOS)
    assert slugs  # non-empty guard
    for slug in slugs:
        assert slug not in blob
    anomalies = (mcp_state.output_dir / "anomalies.csv").read_text(encoding="utf-8")
    descriptions = [
        line.split(",")[3] for line in anomalies.splitlines()[1:] if line
    ]
    assert descriptions  # non-empty guard
    for description in descriptions:
        assert description not in blob


def test_get_logs_window_query_and_limit(amc, mcp_state):
    from_ms, to_ms = _day_window(amc)
    payload = _call_tool(mcp_state, "get_logs", {
        "from_ms": from_ms, "to_ms": to_ms,
    })["structuredContent"]
    log_path = mcp_state.output_dir / "metric_report.log"
    total_lines = sum(1 for _ in log_path.open(encoding="utf-8"))
    assert total_lines > 0  # non-empty guard
    assert len(payload["lines"]) == total_lines
    assert payload["truncated"] is False

    filtered = _call_tool(mcp_state, "get_logs", {
        "from_ms": from_ms, "to_ms": to_ms, "query": "component:cacheservice",
    })["structuredContent"]
    assert filtered["lines"]
    for line in filtered["lines"]:
        assert "component=cacheservice" in line
    assert len(filtered["lines"]) < total_lines

    capped = _call_tool(mcp_state, "get_logs", {
        "from_ms": from_ms, "to_ms": to_ms, "limit": 1,
    })["structuredContent"]
    assert len(capped["lines"]) == 1
    assert capped["truncated"] is True


def test_get_logs_absent_artifact_is_note_not_error(amc, tmp_path):
    argv = [
        "--duration-days", "1", "--seed", "42",
        "--components", "apigateway",
        "--output-dir", str(tmp_path),
        "--interval-seconds", "3600",
        "--emit", "metrics",
    ]
    amc.main(argv)
    state = server.build_state(amc, amc.parse_args(argv))
    payload = _call_tool(state, "get_logs", {
        "from_ms": 0, "to_ms": 10**13,
    })["structuredContent"]
    assert payload["lines"] == []
    assert "note" in payload


def test_deduplicate_logs_clusters_account_for_every_line(amc, mcp_state):
    from_ms, to_ms = _day_window(amc)
    payload = _call_tool(mcp_state, "deduplicate_logs", {
        "from_ms": from_ms, "to_ms": to_ms,
    })["structuredContent"]
    log_path = mcp_state.output_dir / "metric_report.log"
    total_lines = sum(1 for _ in log_path.open(encoding="utf-8"))
    assert payload["clusters"]  # non-empty guard
    assert sum(c["count"] for c in payload["clusters"]) == total_lines
    assert payload["total_lines"] == total_lines
    counts = [c["count"] for c in payload["clusters"]]
    assert counts == sorted(counts, reverse=True)
    for cluster in payload["clusters"]:
        assert cluster["representative"]


# ------------------------------------------------------------------
# Ops tools + command-trace integration (phase 3)
# ------------------------------------------------------------------

def test_kubectl_get_rows_match_rendered_command(amc, tmp_path):
    state = _build_state(amc, tmp_path)
    payload = _call_tool(state, "kubectl_get", {"kind": "pods"})["structuredContent"]
    assert payload["kind"] == "pods"
    assert payload["count"] == len(payload["rows"]) > 0
    rendered = server.run_command(
        state, argv=["kubectl", "get", "pods", "-n", state.namespace],
        client="test",
    )["result"]["stdout"]
    row_names = [row["name"] for row in payload["rows"]]
    assert row_names  # non-empty guard
    for row_name in row_names:
        assert row_name in rendered  # same snapshot behind both views

    result = _call_tool(state, "kubectl_get", {"kind": "frobnicators"})
    assert result["isError"] is True
    assert "frobnicators" in result["content"][0]["text"]


def test_kubectl_get_reflects_api_mutation(amc, tmp_path):
    state = _build_state(amc, tmp_path)
    response = server.kubernetes_api_mutating_response(
        state, "PATCH",
        f"/apis/apps/v1/namespaces/{state.namespace}/deployments/apigateway/scale",
        {"spec": {"replicas": 7}},
    )
    assert response.status < 400
    payload = _call_tool(state, "kubectl_get", {
        "kind": "deployments", "name": "apigateway",
    })["structuredContent"]
    assert payload["count"] == 1
    row_blob = json.dumps(payload["rows"][0])
    assert "7" in row_blob  # overlay scale visible through the MCP view


def test_describe_logs_and_helm_tools_render(amc, mcp_state):
    described = _call_tool(mcp_state, "describe_resource", {
        "kind": "deployment", "name": "apigateway",
    })["structuredContent"]
    assert described["exit_code"] == 0
    assert "apigateway" in described["stdout"]

    pods = _call_tool(mcp_state, "kubectl_get", {"kind": "pods"})["structuredContent"]
    pod_name = pods["rows"][0]["name"]
    logs = _call_tool(mcp_state, "get_pod_logs", {
        "pod": pod_name, "tail_lines": 5,
    })["structuredContent"]
    assert logs["exit_code"] == 0
    assert logs["stdout"]

    status = _call_tool(mcp_state, "helm_status", {
        "release": server.DEFAULT_RELEASE,
    })["structuredContent"]
    assert status["exit_code"] == 0
    assert server.DEFAULT_RELEASE in status["stdout"]

    history = _call_tool(mcp_state, "helm_history", {
        "release": server.DEFAULT_RELEASE,
    })["structuredContent"]
    assert history["exit_code"] == 0

    events = _call_tool(mcp_state, "get_events", {})["structuredContent"]
    assert events["count"] == len(events["rows"])


def test_tools_call_records_mcp_command_trace(amc, tmp_path):
    state = _build_state(amc, tmp_path)
    before = state.traces.count()
    _call_tool(state, "get_current_time")
    assert state.traces.count() == before + 1
    trace = state.traces.list(limit=1)[0]
    assert trace["command_family"] == "mcp"
    assert trace["verb"] == "get_current_time"
    assert trace["support_status"] == "supported"
    assert trace["exit_code"] == 0
    assert trace["matched_rule_id"] == "mcp.get_current_time"

    matches = state.traces.search(command_family="mcp")
    assert matches["items"]  # the same filters kubectl traces use find it


def test_unknown_tool_lands_in_unsupported_backlog(amc, tmp_path):
    state = _build_state(amc, tmp_path)
    status, body = _rpc(
        state, "tools/call", {"name": "definitely_not_a_tool", "arguments": {}}
    )
    assert body["error"]["code"] == server_mcp.INVALID_PARAMS
    groups = state.traces.unsupported_summary()
    assert groups  # non-empty guard
    assert "definitely_not_a_tool" in json.dumps(groups)


def test_mcp_trace_arguments_are_redacted(amc, tmp_path):
    state = _build_state(amc, tmp_path)
    _call_tool(state, "get_logs", {
        "from_ms": 0, "to_ms": 1000,
        "token": "supersecretvalue",
    })
    # Whether the tool accepts or rejects the extra argument, the recorded
    # trace must never carry the raw sensitive value.
    trace = state.traces.list(limit=1)[0]
    assert trace["command_family"] == "mcp"
    assert trace["parsed_flags"].get("token") == "***"
    assert "supersecretvalue" not in json.dumps(trace)


def test_mcp_traces_round_trip_through_trace_bundle_helpers(amc, tmp_path):
    from anomaly_metric_creator import server_traces

    state = _build_state(amc, tmp_path)
    _call_tool(state, "get_current_time")
    exported = state.traces.export_payload()
    mcp_traces = [
        t for t in exported["traces"] if t["command_family"] == "mcp"
    ]
    assert mcp_traces  # non-empty guard
    trace = server_traces.CommandTrace.from_dict(mcp_traces[0])
    assert server_traces.trace_matches_search(trace, command_family="mcp")
    assert not server_traces.trace_matches_search(trace, command_family="kubectl")


# ------------------------------------------------------------------
# Ground-truth wall (day one)
# ------------------------------------------------------------------

def test_tool_surface_carries_no_scenario_or_anomaly_ground_truth(amc, mcp_state):
    slugs = set(amc.SCENARIOS)
    assert slugs  # non-empty guard
    _status, listing = _rpc(mcp_state, "tools/list")
    serialized = [json.dumps(listing, sort_keys=True)]
    for name in ("get_current_time", "list_components", "get_topology"):
        serialized.append(json.dumps(_call_tool(mcp_state, name), sort_keys=True))
    blob = "\n".join(serialized)
    for slug in slugs:
        assert slug not in blob
    anomalies = (mcp_state.output_dir / "anomalies.csv").read_text(encoding="utf-8")
    descriptions = [
        line.split(",")[3] for line in anomalies.splitlines()[1:] if line
    ]
    assert descriptions  # non-empty guard
    for description in descriptions:
        assert description not in blob


# ------------------------------------------------------------------
# Live HTTP wiring
# ------------------------------------------------------------------

def _http_post(base_url, payload, *, headers=None):
    request = urllib.request.Request(
        base_url + "/mcp",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        raw = response.read()
        return response.status, json.loads(raw) if raw else None


def test_http_round_trip_initialize_list_call(amc, tmp_path):
    state = _build_state(amc, tmp_path)
    httpd, base_url = server.start_test_server(state)
    try:
        status, body = _http_post(base_url, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": server_mcp.MCP_PROTOCOL_VERSION},
        })
        assert status == 200
        assert body["result"]["serverInfo"]["name"] == server_mcp.SERVER_NAME

        status, body = _http_post(
            base_url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        assert len(body["result"]["tools"]) == len(server_mcp.MCP_TOOLS)

        status, body = _http_post(base_url, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "get_current_time", "arguments": {}},
        })
        assert body["result"]["isError"] is False

        # Notifications are acknowledged with 202 and no body.
        status, body = _http_post(
            base_url,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        assert status == 202
        assert body is None

        # GET /mcp: streamable HTTP only — no legacy SSE stream.
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(base_url + "/mcp", timeout=5)
        assert excinfo.value.code == 405
        refusal = json.loads(excinfo.value.read())
        assert refusal["error"]["code"] == server_mcp.INVALID_REQUEST
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_http_body_cap_returns_jsonrpc_error(amc, tmp_path):
    state = _build_state(amc, tmp_path)
    httpd, base_url = server.start_test_server(
        state, security=server.ServerSecurityConfig(max_body_bytes=64)
    )
    try:
        oversized = {"jsonrpc": "2.0", "id": 1, "method": "ping",
                     "params": {"pad": "x" * 500}}
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _http_post(base_url, oversized)
        assert excinfo.value.code == 413
        body = json.loads(excinfo.value.read())
        assert body["error"]["code"] == server_mcp.INVALID_REQUEST
        assert "64" in body["error"]["message"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_http_mcp_requires_bearer_auth_when_configured(amc, tmp_path):
    state = _build_state(amc, tmp_path)
    httpd, base_url = server.start_test_server(
        state, security=server.ServerSecurityConfig(auth_token="sekrit")
    )
    try:
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _http_post(base_url, ping)
        assert excinfo.value.code == 401

        status, body = _http_post(
            base_url, ping, headers={"authorization": "Bearer sekrit"}
        )
        assert status == 200
        assert body["result"] == {}
    finally:
        httpd.shutdown()
        httpd.server_close()

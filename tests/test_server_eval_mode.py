"""Eval-mode ground-truth wall (docs/work/archive/2026-07/2026-07-02-mcp-eval-mode-hardening).

`--mcp-eval-mode` hides every rubric-bearing surface so an agent evaluated
through `/mcp` cannot read the scoring key. These tests pin: the refusal on
every rubric endpoint, the registry-completeness guard (a new route cannot
ship unclassified), the automated leak sweep over the whole MCP tool
surface, the report-log tools' eval-mode refusal, and the auth/rate/body
interaction on `/mcp`.
"""

import ast
import datetime as _dt
import inspect
import json
import re
import textwrap
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from anomaly_metric_creator import server, server_mcp

_COMPONENTS = "apigateway,cacheservice,database,authservice"

_TOOL_MINIMAL_ARGS = {
    "get_current_time": {},
    "list_components": {},
    "get_topology": {},
    "get_metric_histogram": {"component": "apigateway"},
    "list_metric_fields": {},
    "group_metrics_by_field": {"field": "component"},
    "get_correlated_timeline": {},
    "get_logs": {},
    "deduplicate_logs": {},
    "kubectl_get": {"kind": "configmaps"},
    "describe_resource": {"kind": "deployment", "name": "apigateway"},
    "get_pod_logs": {},
    "get_events": {},
    "helm_status": {},
    "helm_history": {},
}
_WINDOW_TOOLS = {
    "get_metric_histogram",
    "group_metrics_by_field",
    "get_correlated_timeline",
    "get_logs",
    "deduplicate_logs",
}
_LOG_TOOLS = {"get_logs", "deduplicate_logs"}


def _tool_minimal_arguments(amc, state):
    registered = {tool.name for tool in server_mcp.MCP_TOOLS}
    configured = set(_TOOL_MINIMAL_ARGS)
    assert configured == registered, (
        "MCP minimal-argument registry drift: "
        f"missing={sorted(registered - configured)}, "
        f"extra={sorted(configured - registered)}"
    )

    arguments = {
        name: dict(template) for name, template in _TOOL_MINIMAL_ARGS.items()
    }
    window = {
        "from_ms": server_mcp._epoch_ms(amc.START),
        "to_ms": server_mcp._epoch_ms(amc.START + _dt.timedelta(days=1)),
    }
    for name in _WINDOW_TOOLS:
        arguments[name].update(window)

    component = arguments["get_metric_histogram"]["component"]
    arguments["get_metric_histogram"]["metric"] = amc.COMPONENTS[component][0].name

    pods = server_mcp.resource_snapshot(state).get("pods", [])
    assert pods, "minimal MCP arguments require at least one simulated pod"
    arguments["get_pod_logs"]["pod"] = pods[0]["name"]
    for name in ("helm_status", "helm_history"):
        arguments[name]["release"] = server.DEFAULT_RELEASE
    return arguments


def _tool_surface_blob(amc, state):
    parts = []
    status, listing = server_mcp.handle_mcp_http_post(
        state, b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
    )
    assert status == 200
    assert listing is not None
    parts.append(json.dumps(listing, sort_keys=True))

    failures = {}
    for name, arguments in _tool_minimal_arguments(amc, state).items():
        status, body = server_mcp.handle_mcp_http_post(
            state,
            json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }).encode("utf-8"),
        )
        if (
            status != 200
            or body is None
            or "error" in body
            or body.get("result", {}).get("isError") is not False
        ):
            failures[name] = {"status": status, "body": body}
        parts.append(json.dumps(body, sort_keys=True))
    assert not failures, f"minimal MCP calls failed: {failures}"
    return "\n".join(parts)


def _module_local_tool_sources(handler):
    """Return ASTs for a handler and its transitively called local helpers."""
    pending = [inspect.unwrap(handler)]
    sources = {}
    while pending:
        function = pending.pop()
        if function in sources:
            continue
        source = textwrap.dedent(inspect.getsource(function))
        tree = ast.parse(source)
        definition = tree.body[0]
        assert isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef))
        if ast.get_docstring(definition, clean=False) is not None:
            definition.body = definition.body[1:]
        sources[function] = definition
        for node in ast.walk(definition):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            called = function.__globals__.get(node.func.id)
            if (
                inspect.isfunction(called)
                and called.__module__ == server_mcp.__name__
            ):
                pending.append(inspect.unwrap(called))
    return sources


def _build_state(amc, out_dir, *, eval_mode, seed="42"):
    argv = [
        "--duration-days", "1", "--seed", seed,
        "--components", _COMPONENTS,
        "--output-dir", str(out_dir),
        "--interval-seconds", "3600",
    ]
    amc.main(argv)
    args = amc.parse_args(argv)
    return server.build_state(amc, args, eval_mode=eval_mode)


def _get(base_url, path, headers=None):
    request = urllib.request.Request(base_url + path, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _post(base_url, path, payload, headers=None):
    request = urllib.request.Request(
        base_url + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _request(method, base_url, path, headers=None, data=None):
    request = urllib.request.Request(
        base_url + path,
        data=data,
        headers=headers or {},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _ops_surface_blob(base_url, pod_name):
    """Serialize every investigation-open ops surface that renders scenario
    data: the MCP `kubectl_get` tool (ConfigMap + pod rows), the
    `/v1/commands` command API (helm values, exec env, configmap), and the
    real Kubernetes REST facade (ConfigMap object). Returns one string for a
    slug-presence sweep.
    """
    parts = []
    for kind in ("configmaps", "pods"):
        _s, body = _post(base_url, "/mcp", {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "kubectl_get", "arguments": {"kind": kind}},
        })
        parts.append(body.decode("utf-8"))
    for command in (
        "helm get values simulated-saas -n saas-prod",
        f"kubectl exec {pod_name} -n saas-prod -- env",
        "kubectl get configmap simulated-saas-config -n saas-prod -o yaml",
    ):
        _s, body = _post(base_url, "/v1/commands", {"command": command})
        parts.append(body.decode("utf-8"))
    _s, body = _get(
        base_url,
        "/api/v1/namespaces/saas-prod/configmaps/simulated-saas-config",
    )
    parts.append(body.decode("utf-8"))
    return "\n".join(parts)


_RUBRIC_PATHS = [
    "/", "/debug",
    "/v1/anomalies", "/v1/scenarios", "/v1/state",
    "/v1/logs/stream",
    "/v1/debug/commands", "/v1/debug/search", "/v1/debug/unsupported",
    "/v1/debug/resources", "/v1/debug/state",
]


@pytest.mark.parametrize("path", _RUBRIC_PATHS)
def test_eval_mode_hides_rubric_endpoints(amc, tmp_path, path):
    state = _build_state(amc, tmp_path, eval_mode=True)
    httpd, base_url = server.start_test_server(state)
    try:
        status, _body = _get(base_url, path)
        assert status == 404, path
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_non_eval_mode_keeps_rubric_endpoints(amc, tmp_path):
    state = _build_state(amc, tmp_path, eval_mode=False)
    httpd, base_url = server.start_test_server(state)
    try:
        for path in ("/v1/anomalies", "/v1/scenarios", "/v1/state", "/debug"):
            status, _body = _get(base_url, path)
            assert status == 200, path
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_eval_mode_keeps_investigation_surface(amc, tmp_path):
    state = _build_state(amc, tmp_path, eval_mode=True)
    httpd, base_url = server.start_test_server(state)
    try:
        status, _ = _get(base_url, "/healthz")
        assert status == 200
        status, body = _post(base_url, "/v1/commands", {
            "command": "kubectl get pods -n saas-prod",
        })
        assert status == 200
        status, body = _post(base_url, "/mcp", {
            "jsonrpc": "2.0", "id": 1, "method": "tools/list",
        })
        assert status == 200
        assert len(json.loads(body)["result"]["tools"]) == len(server_mcp.MCP_TOOLS)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_every_dispatched_route_is_classified():
    """Completeness guard: a new endpoint cannot ship unclassified.

    Scans the server dispatch for ``path == "..."`` and
    ``path.startswith("...")`` literals and asserts each is either a rubric
    endpoint or on the investigation allowlist. Forces whoever adds a route
    to place it in exactly one bucket, so the ground-truth wall can't be
    bypassed by omission.
    """
    source = Path(server.__file__).read_text(encoding="utf-8")
    exact = set(re.findall(r'path == "([^"]+)"', source))
    for group in re.findall(r'path in \{([^}]+)\}', source):
        exact |= {token.strip().strip('"') for token in group.split(",")}
    prefixes = set(re.findall(r'path\.startswith\("([^"]+)"\)', source))
    literals = {p for p in (exact | prefixes) if p.startswith("/")}
    assert literals  # non-empty guard: the scan must find real routes

    classified = (
        server._RUBRIC_ENDPOINT_EXACT
        | set(server._RUBRIC_ENDPOINT_PREFIXES)
        | server._INVESTIGATION_ENDPOINT_EXACT
    )
    unclassified = {
        path for path in literals
        if path not in classified and not server._rubric_endpoint(path)
    }
    assert not unclassified, (
        f"unclassified server routes (add to a rubric or investigation "
        f"registry in server.py): {sorted(unclassified)}"
    )


def test_mcp_tool_handlers_have_no_rubric_access():
    """Structural wall: tool handlers cannot reach rubric-bearing state.

    The live response sweep below protects observable behavior. This guard
    follows module-local helper calls so moving a rubric read one function
    down cannot evade review. External ops renderers have their own live
    multi-surface sweep in this module. See the MCP facade and eval-mode
    contract in `docs/spec/amc/backend/api-cli-server.md`.
    """
    forbidden_attributes = {
        "anomaly_rows",
        "active_scenarios",
        "scenarios",
        "SCENARIOS",
    }
    violations = []
    for tool in server_mcp.MCP_TOOLS:
        for function, definition in _module_local_tool_sources(tool.handler).items():
            for node in ast.walk(definition):
                if isinstance(node, ast.Name) and node.id == "SCENARIOS":
                    violations.append((tool.name, function.__name__, "SCENARIOS"))
                elif isinstance(node, ast.Attribute) and node.attr in forbidden_attributes:
                    violations.append((tool.name, function.__name__, node.attr))
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if "anomalies.csv" in node.value:
                        violations.append(
                            (tool.name, function.__name__, "anomalies.csv")
                        )
                    if "metric_report.log" in node.value and tool.name not in _LOG_TOOLS:
                        violations.append(
                            (tool.name, function.__name__, "metric_report.log")
                        )
    assert not violations, f"MCP rubric access outside the wall: {violations}"


def test_eval_mode_tool_surface_has_no_ground_truth_leak(amc, tmp_path):
    """Registry-coupled sweep over every tool in eval and non-eval modes."""
    state = _build_state(amc, tmp_path / "eval", eval_mode=True)
    blob = _tool_surface_blob(amc, state)

    slugs = set(amc.SCENARIOS)
    assert slugs  # non-empty guard
    leaked_slugs = [slug for slug in slugs if slug in blob]
    assert not leaked_slugs, leaked_slugs

    anomalies = (state.output_dir / "anomalies.csv").read_text(encoding="utf-8")
    descriptions = [
        line.split(",")[3] for line in anomalies.splitlines()[1:] if line
    ]
    assert descriptions  # non-empty guard
    leaked = [d for d in descriptions if d and d in blob]
    assert not leaked, leaked

    # Positive control: the identical registry-driven calls in non-eval mode
    # expose at least one active slug through the operator-visible ConfigMap.
    plain_state = _build_state(amc, tmp_path / "plain", eval_mode=False)
    plain_blob = _tool_surface_blob(amc, plain_state)
    active = set(state.active_scenarios)
    assert active
    assert any(slug in plain_blob for slug in active), (
        "control failed: no active slug appeared in any non-eval MCP response"
    )


def test_eval_mode_ops_surfaces_have_no_scenario_slug_leak(amc, tmp_path):
    """Leak sweep over the ops surfaces (07-06 review): the ConfigMap
    `SCENARIOS` key, pod `scenario_ids`, `kubectl exec ... env`, `helm get
    values`, and the Helm release config all previously named the active
    scenarios in eval mode. Drive each surface live and assert no active slug
    survives, with a non-eval control proving the surfaces really carry the
    data (so the eval assertion cannot pass vacuously).
    """
    eval_state = _build_state(amc, tmp_path / "eval", eval_mode=True)
    active = set(eval_state.active_scenarios)
    assert active  # non-empty guard: default medium run fires ~11 scenarios

    httpd, base_url = server.start_test_server(eval_state)
    try:
        eval_blob = _ops_surface_blob(base_url, "authservice-0")
    finally:
        httpd.shutdown()
        httpd.server_close()
    leaked = sorted(slug for slug in active if slug in eval_blob)
    assert not leaked, f"active scenario slugs leaked in eval mode: {leaked}"

    # Positive control: the identical surfaces in non-eval mode DO carry the
    # slugs, so the sweep exercises the real render paths.
    plain_state = _build_state(amc, tmp_path / "plain", eval_mode=False)
    httpd, base_url = server.start_test_server(plain_state)
    try:
        plain_blob = _ops_surface_blob(base_url, "authservice-0")
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert any(slug in plain_blob for slug in active), (
        "control failed: no active slug appeared on the ops surfaces in "
        "non-eval mode, so the eval sweep proves nothing"
    )


def test_eval_mode_rubric_404_before_auth_every_method(amc, tmp_path):
    """Fingerprint resistance across methods: with auth enabled, an
    unauthenticated request to a rubric endpoint must 404 (route hidden),
    never 401 (route exists, needs a token). The pre-fix `do_POST` and
    `_handle_mutating_method` checked auth before the rubric gate, leaking
    endpoint existence via 401. Non-eval control returns 401, proving auth is
    genuinely on and the 404 is the wall, not a missing route.
    """
    security = server.ServerSecurityConfig(auth_token="secret-token")
    rubric_path = "/v1/anomalies"  # rubric endpoint reachable by any method
    methods = ["GET", "POST", "PUT", "PATCH", "DELETE"]

    eval_state = _build_state(amc, tmp_path / "eval", eval_mode=True)
    httpd, base_url = server.start_test_server(eval_state, security=security)
    try:
        for method in methods:
            status, _ = _request(method, base_url, rubric_path)
            assert status == 404, f"{method} eval-mode rubric leaked {status}"
    finally:
        httpd.shutdown()
        httpd.server_close()

    plain_state = _build_state(amc, tmp_path / "plain", eval_mode=False)
    httpd, base_url = server.start_test_server(plain_state, security=security)
    try:
        for method in methods:
            status, _ = _request(method, base_url, rubric_path)
            assert status == 401, (
                f"{method} non-eval control expected 401 auth challenge, "
                f"got {status}"
            )
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_eval_mode_log_tools_refuse(amc, tmp_path):
    state = _build_state(amc, tmp_path, eval_mode=True)
    window = {
        "from_ms": server_mcp._epoch_ms(amc.START),
        "to_ms": server_mcp._epoch_ms(amc.START + _dt.timedelta(days=1)),
    }
    logs = server_mcp._tool_get_logs(state, window)
    assert logs["lines"] == []
    assert "eval mode" in logs["note"]
    dedup = server_mcp._tool_deduplicate_logs(state, window)
    assert dedup["clusters"] == []
    assert "eval mode" in dedup["note"]


def test_mcp_auth_rate_and_body_cap_are_jsonrpc_shaped(amc, tmp_path):
    state = _build_state(amc, tmp_path, eval_mode=True)
    security = server.ServerSecurityConfig(
        auth_token="sekrit", max_body_bytes=80, rate_limit_per_minute=2,
    )
    httpd, base_url = server.start_test_server(state, security=security)
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    try:
        # Missing auth -> 401.
        status, _ = _post(base_url, "/mcp", ping)
        assert status == 401

        auth = {"authorization": "Bearer sekrit"}
        # Oversized body -> JSON-RPC-shaped 413.
        status, body = _post(
            base_url, "/mcp",
            {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"p": "x" * 300}},
            headers=auth,
        )
        assert status == 413
        assert json.loads(body)["error"]["code"] == server_mcp.INVALID_REQUEST

        # Rate limit: 2/min, so a later small call trips a JSON-RPC 429.
        results = [_post(base_url, "/mcp", ping, headers=auth) for _ in range(4)]
        codes = [status for status, _ in results]
        assert 429 in codes
        limited = next(body for status, body in results if status == 429)
        assert json.loads(limited)["error"]["code"] == server_mcp.INVALID_REQUEST
    finally:
        httpd.shutdown()
        httpd.server_close()

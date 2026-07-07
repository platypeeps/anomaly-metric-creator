"""Eval-mode ground-truth wall (Trellis task 07-02-mcp-eval-mode-hardening).

`--mcp-eval-mode` hides every rubric-bearing surface so an agent evaluated
through `/mcp` cannot read the scoring key. These tests pin: the refusal on
every rubric endpoint, the registry-completeness guard (a new route cannot
ship unclassified), the automated leak sweep over the whole MCP tool
surface, the report-log tools' eval-mode refusal, and the auth/rate/body
interaction on `/mcp`.
"""

import datetime as _dt
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from anomaly_metric_creator import server, server_mcp

_COMPONENTS = "apigateway,cacheservice,database,authservice"


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


def test_eval_mode_tool_surface_has_no_ground_truth_leak(amc, tmp_path):
    """Automated leak sweep: serialize tools/list + every tool response in
    eval mode and assert no scenario slug or manifest description appears.
    """
    state = _build_state(amc, tmp_path, eval_mode=True)
    day_from = server_mcp._epoch_ms(amc.START)
    day_to = server_mcp._epoch_ms(amc.START + _dt.timedelta(days=1))

    def call(name, arguments=None):
        _status, body = server_mcp.handle_mcp_http_post(
            state,
            json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }).encode("utf-8"),
        )
        return json.dumps(body, sort_keys=True)

    blobs = []
    _status, listing = server_mcp.handle_mcp_http_post(
        state, b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
    )
    blobs.append(json.dumps(listing, sort_keys=True))
    window = {"from_ms": day_from, "to_ms": day_to}
    blobs.append(call("get_current_time"))
    blobs.append(call("list_components"))
    blobs.append(call("get_topology"))
    blobs.append(call("list_metric_fields"))
    blobs.append(call("get_correlated_timeline", window))
    blobs.append(call("group_metrics_by_field", {"field": "component", **window}))
    blobs.append(call("get_logs", window))
    blobs.append(call("deduplicate_logs", window))
    blob = "\n".join(blobs)

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

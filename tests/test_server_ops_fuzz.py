"""Fuzz/property coverage for the ops command and Kubernetes API surface.

Audit task 07-02-audit-server-ops-rendering: the ~7.6k-line fake
Kubernetes/Helm renderer must degrade gracefully under adversarial or
malformed input — structured errors with correct status codes (never an
unhandled 500 for *expected* bad input), Kubernetes ``Status``-shaped
bodies on API paths, well-formed ``CommandTrace`` records for every call,
an unchanged ``SimulationMutations`` overlay after refused mutations, and
a generic 500 body that never leaks ``str(exc)`` internals.

The malformed corpus is seeded (``random.Random(20260702)``) so runs are
deterministic; no fuzzing dependency is introduced.
"""

import contextlib
import json
import random
import string
import urllib.error
import urllib.request

import pytest

from anomaly_metric_creator import server


@pytest.fixture(scope="module")
def fuzz_state(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("ops_fuzz")
    argv = [
        "--duration-days", "1",
        "--seed", "42",
        "--components", "apigateway,cacheservice,database,authservice",
        "--output-dir", str(out),
        "--interval-seconds", "3600",
    ]
    amc.main(argv)
    return server.build_state(amc, amc.parse_args(argv))


@contextlib.contextmanager
def _running(state, **kwargs):
    httpd, base_url = server.start_test_server(state, **kwargs)
    try:
        yield base_url
    finally:
        httpd.shutdown()
        httpd.server_close()


def _post_json(base_url, path, payload):
    """POST and return (status, parsed_body) without raising on 4xx/5xx."""
    request = urllib.request.Request(
        base_url + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw else None


def _get(base_url, path):
    try:
        with urllib.request.urlopen(base_url + path, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = {"_raw": raw.decode("utf-8", "replace")}
        return exc.code, body


def _random_tokens(rng, count):
    alphabet = string.printable
    return [
        "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 60)))
        for _ in range(count)
    ]


_CURATED_COMMANDS = [
    'kubectl get pods -n "unclosed',        # shlex error path
    "",                                      # empty command
    "   ",                                   # whitespace only
    "kubectl",                               # family only
    "kubectl get",                           # verb without resource
    "kubectl get ../../../etc/passwd",       # traversal-shaped resource
    "kubectl get pods --namespace",          # flag missing its value
    "kubectl get pödś -n saas-prod",         # unicode resource
    "kubectl frobnicate widgets",            # unknown verb
    "helm status does-not-exist",            # unknown release
    "helm",                                  # bare helm
    "rm -rf /",                              # non-kubectl family
    "kubectl get pods " + "-v " * 200,       # flag spam
    "kubectl get " + "a" * 5000,             # huge resource name
    "kubectl get pods\x00 -n saas-prod",     # embedded NUL
    "kubectl logs pod/  --tail=-5",          # empty name, negative tail
    "kubectl get pods -l a==b!!,,=",         # mangled label selector
    "kubectl scale deployment/apigateway --replicas=notanumber",
    "kubectl get pods --watch --wide -o json -n saas-prod",   # watch => partial
    "kubectl get -w -n saas-prod",                             # watch, no kind
]

_CURATED_PAYLOADS = [
    {},                                      # neither command nor argv
    {"command": None},
    {"argv": []},
    {"argv": ["kubectl", 42, None, {"x": 1}]},   # non-string argv items
    {"argv": "kubectl get pods"},                # argv as string, not list
    {"command": ["kubectl", "get"]},             # command as list
    {"argv": ["kubectl", "get", "pods"], "command": 7},
]


def test_fuzz_commands_never_500_and_always_trace(fuzz_state):
    rng = random.Random(20260702)
    commands = _CURATED_COMMANDS + _random_tokens(rng, 40)
    with _running(fuzz_state) as base_url:
        before = fuzz_state.traces.count()
        accepted = 0
        for cmd in commands:
            status, body = _post_json(base_url, "/v1/commands", {"command": cmd})
            assert status in (200, 400), (cmd, status, body)
            if status == 200:
                accepted += 1
                result = body.get("result")
                assert isinstance(result, dict), (cmd, body)
                assert isinstance(result.get("exit_code"), int), (cmd, body)
                assert isinstance(result.get("stdout"), str)
                assert isinstance(result.get("stderr"), str)
                assert result.get("support_status"), (cmd, body)
                assert isinstance(body.get("trace"), dict)
        for payload in _CURATED_PAYLOADS:
            status, body = _post_json(base_url, "/v1/commands", payload)
            assert status in (200, 400), (payload, status, body)
            if status == 200:
                accepted += 1
        # Every accepted command produced exactly one well-formed trace.
        assert fuzz_state.traces.count() - before == accepted
        status, _body = _get(base_url, "/v1/state")
        assert status == 200  # server survived the corpus

    for as_dict in fuzz_state.traces.list(limit=accepted or 1):
        assert as_dict["support_status"], as_dict
        assert isinstance(as_dict["command_family"], str)


_CURATED_API_PATHS = [
    "/api/v1/namespaces/../../secrets",
    "/api/v1/namespaces/saas-prod/pods/" + "a" * 4000,
    "/api/v1/namespaces/saas-prod/pods/pod%00name",
    "/api/v1/namespaces/saas-prod/nonexistentkind",
    "/apis/apps/v1/namespaces/saas-prod/deployments/none-such",
    "/apis/made.up.group/v9/things",
    "/api/v1/pods?labelSelector=!!!bad,,==",
    "/api/v1/pods?limit=-5",
    "/api/v1/pods?limit=notanumber",
    "/apis/apps/v1/deployments?fieldSelector===",
    # Watch shapes that must NOT open a stream: a non-true value, a single
    # object path, and an unmodeled resource all fall back to one-shot
    # list/get/404 handling. (A watchable list path with watch=true is a
    # long-lived stream and is covered by test_server_watch, not here — the
    # no-timeout fuzz GET would block on it.)
    "/api/v1/namespaces/saas-prod/pods?watch=banana",
    "/api/v1/namespaces/saas-prod/pods/apigateway-0?watch=true",
    "/api/v1/namespaces/saas-prod/widgets?watch=true",
]


def test_fuzz_kubernetes_api_paths_are_status_shaped(fuzz_state):
    rng = random.Random(20260703)
    fuzz_paths = [
        "/api/v1/" + "".join(rng.choice(string.ascii_letters + "/%.-_")
                             for _ in range(rng.randrange(1, 80)))
        for _ in range(30)
    ]
    with _running(fuzz_state) as base_url:
        for path in _CURATED_API_PATHS + fuzz_paths:
            status, body = _get(base_url, path)
            assert status != 500, (path, body)
            if status >= 400 and isinstance(body, dict) and "_raw" not in body:
                # A real Kubernetes client expects a Status object on API
                # paths, not a bare {"error": ...} body.
                assert body.get("kind") == "Status", (path, body)
        status, _ = _get(base_url, "/v1/state")
        assert status == 200


def _mutate(base_url, method, path, data):
    request = urllib.request.Request(
        base_url + path,
        data=data,
        headers={"content-type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return exc.code, None


def test_fuzz_malformed_mutations_preserve_overlay(fuzz_state):
    cases = [
        ("PATCH", "/apis/apps/v1/namespaces/saas-prod/deployments/apigateway",
         b"{not json"),
        ("PATCH", "/apis/apps/v1/namespaces/saas-prod/deployments/apigateway",
         b"[]"),
        ("PUT", "/api/v1/namespaces/saas-prod/pods/apigateway-0",
         b'{"spec": ' + b'{"a":' * 200 + b"1" + b"}" * 200 + b"}"),
        ("DELETE", "/api/v1/namespaces/saas-prod/pods/no-such-pod", b""),
        ("PATCH", "/apis/apps/v1/namespaces/saas-prod/deployments/none",
         b'{"spec": {"replicas": "notanumber"}}'),
        # A watch query on a refused mutation: do_POST never dispatches a
        # stream, and a PATCH naming a missing deployment must leave the
        # overlay untouched regardless of the query string.
        ("PATCH", "/apis/apps/v1/namespaces/saas-prod/deployments/none?watch=true",
         b'{"spec": {"replicas": 3}}'),
    ]
    with _running(fuzz_state) as base_url:
        baseline = json.dumps(fuzz_state.mutations.summary(), sort_keys=True)
        for method, path, data in cases:
            status, body = _mutate(base_url, method, path, data)
            assert status != 500, (method, path, status, body)
            if status >= 400 and isinstance(body, dict):
                assert body.get("kind") == "Status", (method, path, body)
        # Refused mutations must not leave partial overlay state behind.
        assert json.dumps(fuzz_state.mutations.summary(), sort_keys=True) == baseline


def test_unbalanced_quote_command_is_structured_not_raised(fuzz_state):
    payload = server.run_command(
        fuzz_state, command='kubectl get pods -n "unclosed', client="fuzz"
    )
    result = payload["result"]
    assert result["exit_code"] != 0
    assert result["matched_rule_id"] == "parse.error"
    assert "quotation" in result["stderr"]


def test_500_body_is_generic_and_leak_free(fuzz_state, tmp_path, monkeypatch):
    sentinel_path = str(fuzz_state.output_dir)

    def boom(*args, **kwargs):
        raise RuntimeError(f"secret detail: {sentinel_path}/token=abc123")

    monkeypatch.setattr(server, "run_command", boom)
    with _running(fuzz_state) as base_url:
        status, body = _post_json(
            base_url, "/v1/commands", {"command": "kubectl get pods"}
        )
        assert status == 500
        assert body == {"error": "internal server error"}

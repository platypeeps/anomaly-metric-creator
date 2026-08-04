"""Contract tests for bounded Kubernetes watch streams.

Two halves are covered:

* Real-client API watch — a modeled list path with ``?watch=true`` streams
  newline-delimited JSON watch events (``ADDED``/``MODIFIED``/``DELETED``)
  backed by the same overlay-aware ``resource_snapshot()`` the list path uses,
  bounded by ``timeoutSeconds``/``_WATCH_MAX_SECONDS`` and the SSE ceiling.
* Command mode — ``POST /v1/commands`` cannot hold a stream, so
  ``kubectl get --watch`` renders the one-shot table plus a note and is
  classified ``partial``.

Every test patches ``server._WATCH_POLL_SECONDS`` down so poll-driven events
land fast, and uses short ``timeoutSeconds`` so streams close deterministically.
"""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from anomaly_metric_creator import server


@pytest.fixture(autouse=True)
def _fast_watch_poll(monkeypatch):
    # Sub-second poll so a scale/delete made mid-watch is observed within the
    # short timeoutSeconds the tests use, keeping them fast and deterministic.
    monkeypatch.setattr(server, "_WATCH_POLL_SECONDS", 0.05)


def _build_state(amc, tmp_path):
    argv = [
        "--duration-days", "2",
        "--signal-level", "medium",
        "--scenarios", "cache_leak_restart",
        "--components", "apigateway,cacheservice",
        "--output-dir", str(tmp_path),
        "--interval-seconds", "3600",
    ]
    return server.build_state(amc, amc.parse_args(argv))


def _read_ndjson(url, timeout=8):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def _watch_in_thread(url, sink, timeout=10):
    def run():
        sink["events"] = _read_ndjson(url, timeout=timeout)

    thread = threading.Thread(target=run)
    thread.start()
    return thread


def test_watch_initial_added_matches_snapshot(amc, tmp_path):
    state = _build_state(amc, tmp_path)
    expected = {
        pod["metadata"]["name"]
        for pod in server._k8s_objects_for_resource(state, "", "pods")
    }
    assert expected  # guard: an empty snapshot would make the check vacuous

    httpd, base_url = server.start_test_server(state)
    try:
        events = _read_ndjson(
            base_url
            + "/api/v1/namespaces/saas-prod/pods?watch=true&timeoutSeconds=1"
        )
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert events, "watch produced no events"
    assert {event["type"] for event in events} == {"ADDED"}
    assert {event["object"]["metadata"]["name"] for event in events} == expected


def test_watch_deployment_scale_emits_modified(amc, tmp_path):
    state = _build_state(amc, tmp_path)
    httpd, base_url = server.start_test_server(state)
    sink: dict = {}
    try:
        thread = _watch_in_thread(
            base_url
            + "/apis/apps/v1/namespaces/saas-prod/deployments"
            + "?watch=true&timeoutSeconds=2",
            sink,
        )
        time.sleep(0.4)  # let the initial ADDED replay land before mutating
        server.run_command(
            state,
            command="kubectl scale deployment cacheservice --replicas=9 -n saas-prod",
        )
        thread.join(timeout=10)
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert not thread.is_alive(), "watch thread did not terminate"
    events = sink["events"]
    modified = [event for event in events if event["type"] == "MODIFIED"]
    assert len(modified) == 1, [event["type"] for event in events]
    assert modified[0]["object"]["metadata"]["name"] == "cacheservice"
    assert modified[0]["object"]["spec"]["replicas"] == 9


def test_watch_pod_delete_emits_deleted(amc, tmp_path):
    state = _build_state(amc, tmp_path)
    httpd, base_url = server.start_test_server(state)
    sink: dict = {}
    try:
        thread = _watch_in_thread(
            base_url
            + "/api/v1/namespaces/saas-prod/pods?watch=true&timeoutSeconds=2",
            sink,
        )
        time.sleep(0.4)
        server.run_command(
            state, command="kubectl delete pod cacheservice-0 -n saas-prod"
        )
        thread.join(timeout=10)
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert not thread.is_alive(), "watch thread did not terminate"
    deleted = [event for event in sink["events"] if event["type"] == "DELETED"]
    assert len(deleted) == 1, [event["type"] for event in sink["events"]]
    assert deleted[0]["object"]["metadata"]["name"] == "cacheservice-0"


def test_watch_timeout_closes_stream_and_records_supported_trace(amc, tmp_path):
    state = _build_state(amc, tmp_path)
    httpd, base_url = server.start_test_server(state)
    try:
        started = time.monotonic()
        events = _read_ndjson(
            base_url
            + "/api/v1/namespaces/saas-prod/pods?watch=true&timeoutSeconds=1"
        )
        elapsed = time.monotonic() - started
    finally:
        httpd.shutdown()
        httpd.server_close()

    # The stream closed on its own (urlopen returned) well inside the ceiling.
    assert elapsed < 5.0
    assert events  # a bounded-but-closed stream still replayed the ADDED set
    watch_traces = [
        trace
        for trace in state.traces.list(limit=50)
        if trace["matched_rule_id"] == "k8s.core.watch.pods"
    ]
    assert watch_traces, "watch did not record a kubernetes-api trace"
    assert watch_traces[0]["support_status"] == "supported"
    assert watch_traces[0]["command_family"] == "kubernetes-api"


def test_watch_sse_slot_exhaustion_returns_status_503(amc, tmp_path):
    state = _build_state(amc, tmp_path)
    httpd, base_url = server.start_test_server(state)
    # Force the SSE ceiling: the watch must refuse before any stream headers.
    httpd.try_acquire_sse = lambda: False
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(
                base_url
                + "/api/v1/namespaces/saas-prod/pods?watch=true&timeoutSeconds=1",
                timeout=5,
            )
        body = json.loads(excinfo.value.read().decode("utf-8"))
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert excinfo.value.code == 503
    assert body["kind"] == "Status"
    assert body["reason"] == "ServiceUnavailable"
    refusal = [
        trace
        for trace in state.traces.list(limit=50)
        if trace["matched_rule_id"] == "k8s.core.watch.pods"
    ]
    assert refusal and refusal[0]["support_status"] == "partial"


def test_unmodeled_resource_watch_is_unsupported(amc, tmp_path):
    state = _build_state(amc, tmp_path)
    httpd, base_url = server.start_test_server(state)
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(
                base_url
                + "/api/v1/namespaces/saas-prod/widgets?watch=true",
                timeout=5,
            )
        body = json.loads(excinfo.value.read().decode("utf-8"))
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert excinfo.value.code == 404
    assert body["kind"] == "Status"
    unsupported = [
        trace
        for trace in state.traces.list(limit=50)
        if trace["command_family"] == "kubernetes-api"
        and trace["support_status"] == "unsupported"
    ]
    assert unsupported, "unmodeled watch path recorded no unsupported trace"


@pytest.mark.parametrize("flag", ["--watch", "-w"])
def test_command_mode_watch_is_partial_with_note(amc, tmp_path, flag):
    state = _build_state(amc, tmp_path)
    result = server.run_command(
        state, command=f"kubectl get pods {flag} -n saas-prod"
    )["result"]

    assert result["exit_code"] == 0
    assert result["support_status"] == "partial"
    assert result["matched_rule_id"] == "kubectl.get.pods.watch"
    assert "NAME" in result["stdout"]  # the one-shot table still renders
    assert "live streaming is not available" in result["stderr"]

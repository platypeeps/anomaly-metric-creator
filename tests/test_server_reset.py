"""Contract tests for the quick simulator environment reset.

``POST /v1/mutations/reset`` (and the in-process ``state.mutations.reset()``)
clears **only** the mutation overlay: workload, deleted-pod, created/deleted
generic resource, extra-event, and Helm release overlays all return to the
selected scenario baseline. Command traces, the simulated clock, and the
generation counters are intentionally preserved — see the reset-scope
contract in ``operations-security-logging.md``.

The renders that embed the simulated clock (``kubectl get events`` LAST SEEN,
``helm list`` UPDATED) only compare byte-for-byte when the clock is frozen, so
every render assertion pauses the clock first: with ``now()`` stable, the only
thing that can move a render is the overlay, which is exactly what reset
restores.
"""

import json
import threading
import urllib.request

import pytest

from anomaly_metric_creator import server


def _build_state(amc, tmp_path, *, scenarios="cache_leak_restart"):
    argv = [
        "--duration-days", "2",
        "--signal-level", "medium",
        "--scenarios", scenarios,
        "--components", "apigateway,cacheservice,database,authservice",
        "--output-dir", str(tmp_path),
        "--interval-seconds", "3600",
    ]
    return server.build_state(amc, amc.parse_args(argv))


def _stdout(state, command):
    return server.run_command(state, command=command)["result"]["stdout"]


def _overlay_is_baseline(summary):
    return (
        summary["workloads"] == {}
        and summary["deleted_pods"] == []
        and summary["created_resources"] == {}
        and summary["deleted_resources"] == {}
        and summary["extra_event_count"] == 0
        and summary["release"]["revision_count"] == 0
    )


# (mutation command, render command, overlay-summary key the mutation populates).
_FAMILIES = [
    (
        "kubectl scale deployment cacheservice --replicas=5 -n saas-prod",
        "kubectl get deployments -n saas-prod",
        "workloads",
    ),
    (
        "kubectl delete pod cacheservice-0 -n saas-prod",
        "kubectl get pods -n saas-prod",
        "deleted_pods",
    ),
    (
        "kubectl create configmap demo --from-literal=k=v -n saas-prod",
        "kubectl get configmaps -n saas-prod",
        "created_resources",
    ),
    (
        "kubectl delete configmap simulated-saas-config -n saas-prod",
        "kubectl get configmaps -n saas-prod",
        "deleted_resources",
    ),
    (
        "helm rollback saas-app 1 -n saas-prod",
        "helm list -n saas-prod",
        "release",
    ),
]


@pytest.mark.parametrize(
    "mutate,render,key", _FAMILIES, ids=[family[2] for family in _FAMILIES]
)
def test_reset_restores_overlay_family_to_baseline(
    amc, tmp_path, mutate, render, key
):
    state = _build_state(amc, tmp_path)
    state.clock.pause()  # freeze now() so renders are byte-comparable
    baseline = _stdout(state, render)

    _stdout(state, mutate)
    mutated = state.mutations.summary()
    if key == "release":
        assert mutated["release"]["revision_count"] > 0
    else:
        assert mutated[key] not in ({}, [], 0)
    # The mutation is observable in the render (positive control: if the render
    # never changed, byte-equality after reset would be trivially true).
    assert _stdout(state, render) != baseline

    state.mutations.reset()

    assert _overlay_is_baseline(state.mutations.summary())
    assert _stdout(state, render) == baseline


def test_reset_restores_all_overlay_families_at_once(amc, tmp_path):
    state = _build_state(amc, tmp_path)
    state.clock.pause()
    renders = [
        "kubectl get deployments -n saas-prod",
        "kubectl get pods -n saas-prod",
        "kubectl get configmaps -n saas-prod",
        "kubectl get events -n saas-prod",
        "helm list -n saas-prod",
        "helm history saas-app -n saas-prod",
    ]
    baseline = {render: _stdout(state, render) for render in renders}

    for mutate, _render, _key in _FAMILIES:
        _stdout(state, mutate)
    assert not _overlay_is_baseline(state.mutations.summary())

    state.mutations.reset()

    assert _overlay_is_baseline(state.mutations.summary())
    for render in renders:
        assert _stdout(state, render) == baseline[render], (
            f"render not restored to baseline: {render}"
        )


def test_reset_preserves_traces_clock_and_generation(amc, tmp_path):
    state = _build_state(amc, tmp_path)
    state.clock.pause()
    _stdout(state, "kubectl get pods -n saas-prod")
    _stdout(state, "kubectl scale deployment cacheservice --replicas=4 -n saas-prod")

    traces_before = state.traces.count()
    clock_before = state.clock.now()
    generation_before = state.generation.generation_count
    assert traces_before > 0

    state.mutations.reset()

    # Traces are debug history (and eval-harness scoring data), not overlay.
    assert state.traces.count() == traces_before
    # Reset never rewinds the monotonic simulated clock.
    assert state.clock.now() == clock_before
    # Reset does not regenerate artifacts, so generation counters are untouched.
    assert state.generation.generation_count == generation_before


def test_reset_endpoint_reports_mutation_overlay_scope(amc, tmp_path):
    state = _build_state(amc, tmp_path)
    server.run_command(
        state,
        command="kubectl scale deployment cacheservice --replicas=6 -n saas-prod",
    )
    httpd, base_url = server.start_test_server(state)
    try:
        request = urllib.request.Request(
            base_url + "/v1/mutations/reset",
            data=b"{}",
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert body["scope"] == "mutation-overlay"
    # Compatibility: callers reading only the "mutations" summary still work.
    assert body["mutations"]["deleted_pods"] == []
    assert _overlay_is_baseline(body["mutations"])


def test_reset_is_safe_under_concurrent_polling(amc, tmp_path):
    state = _build_state(amc, tmp_path)
    server.run_command(
        state, command="kubectl delete pod cacheservice-0 -n saas-prod"
    )
    httpd, base_url = server.start_test_server(state)
    errors = []

    def poll():
        try:
            for _ in range(20):
                with urllib.request.urlopen(
                    base_url + "/v1/state", timeout=5
                ) as response:
                    json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    try:
        poller = threading.Thread(target=poll)
        poller.start()
        request = urllib.request.Request(
            base_url + "/v1/mutations/reset",
            data=b"{}",
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
        poller.join(timeout=10)
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert not errors
    assert body["scope"] == "mutation-overlay"
    assert _overlay_is_baseline(body["mutations"])

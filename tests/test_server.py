import base64
import contextlib
import datetime as _dt
import gzip
import importlib
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
import typing
import urllib.error
import urllib.parse
import urllib.request

import pytest

from anomaly_metric_creator import server, server_config, server_traces

REAL_CLIENT_SMOKE_ENV = "AMC_RUN_REAL_CLIENT_SMOKE"


def _build_state(
    amc,
    tmp_path,
    *,
    scenarios,
    components="apigateway,cacheservice,database,authservice",
    signal_level="medium",
    days=2,
    start_time=None,
    persist_command_db=None,
    persist_command_retention=None,
    trace_limit=server.DEFAULT_TRACE_LIMIT,
):
    argv = [
        "--duration-days", str(days),
        "--signal-level", signal_level,
        "--scenarios", scenarios,
        "--components", components,
        "--output-dir", str(tmp_path),
        "--interval-seconds", "3600",
    ]
    if start_time is not None:
        argv += ["--start-time", start_time]
    args = amc.parse_args(argv)
    return server.build_state(
        amc,
        args,
        persist_command_db=persist_command_db,
        persist_command_retention=persist_command_retention,
        trace_limit=trace_limit,
    )


def _get_json(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json_with_headers(url, headers):
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _read_jsonl_records_until(path, expected_count, *, timeout=5.0):
    deadline = time.monotonic() + timeout
    records = []
    while time.monotonic() < deadline:
        if path.exists():
            records = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()  # resource-lint: allow
            ]
            if len(records) >= expected_count:
                return records
        time.sleep(0.01)
    return records


def _pod_name(component):
    return "database-0" if component == "database" else f"{component}-0"


def _trace(
    trace_id,
    timestamp,
    raw_input="kubectl get pods -n saas-prod",
    *,
    active_scenarios=("cache_leak_restart",),
):
    return server.CommandTrace(
        id=trace_id,
        received_at_wall_time=timestamp,
        simulated_time=timestamp,
        raw_input=raw_input,
        argv=tuple(raw_input.split()),
        client="debug-ui",
        command_family="kubectl",
        verb="get",
        resource_kind="pods",
        resource_name="",
        namespace="saas-prod",
        parsed_flags={"namespace": "saas-prod"},
        support_status="supported",
        matched_rule_id="kubectl.get.pods",
        active_scenarios=active_scenarios,
        exit_code=0,
        stdout_preview="",
        stderr_preview="",
        stdout="",
        stderr="",
        latency_ms=12.5,
        fingerprint="kubectl.get.pods",
        guessed_intent="Inspect pods",
    )


@contextlib.contextmanager
def _running_test_server(state, *, security=None, request_logger=None):
    httpd, base_url = server.start_test_server(
        state,
        security=security,
        request_logger=request_logger,
    )
    try:
        yield base_url
    finally:
        httpd.shutdown()
        httpd.server_close()


def _require_real_client_smoke_opt_in():
    if os.environ.get(REAL_CLIENT_SMOKE_ENV) != "1":
        pytest.skip(f"set {REAL_CLIENT_SMOKE_ENV}=1 to run real client smoke tests")


def test_build_state_uses_configured_start_time(amc, tmp_path):
    state = _build_state(
        amc,
        tmp_path,
        scenarios="cache_collapse",
        components="cacheservice",
        days=1,
        start_time="2026-06-24T12:34:56Z",
    )

    assert state.clock.start_time == _dt.datetime(2026, 6, 24, 12, 34, 56)


def test_kubectl_responses_reflect_db_disk_exhaustion(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="db_disk_exhaustion")

    pvc = server.run_command(
        state,
        command="kubectl describe pvc database-data-database-0 -n saas-prod",
    )
    assert pvc["result"]["exit_code"] == 0
    assert "Used:          92%" in pvc["result"]["stdout"]
    assert "VolumePressure" in pvc["result"]["stdout"]

    logs = server.run_command(state, command="kubectl logs database-0 -n saas-prod")
    assert logs["result"]["exit_code"] == 0
    assert "disk_used_pct=92" in logs["result"]["stdout"]


def test_kubectl_logs_models_prefix_previous_container_and_since_time(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="db_disk_exhaustion")

    logs = server.run_command(
        state,
        command=(
            "kubectl logs database-0 -n saas-prod -c database --prefix "
            "--previous --since-time=1970-01-01T00:00:00Z"
        ),
    )
    assert logs["result"]["support_status"] == "supported"
    assert logs["result"]["stderr"] == ""
    assert "database-0/database previous " in logs["result"]["stdout"]
    assert "disk_used_pct=92" in logs["result"]["stdout"]

    future_logs = server.run_command(
        state,
        command="kubectl logs database-0 -n saas-prod --since-time=2999-01-01T00:00:00Z",
    )
    assert future_logs["result"]["support_status"] == "supported"
    assert future_logs["result"]["stdout"] == ""


def test_kubectl_logs_rejects_invalid_since_time(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="db_disk_exhaustion")

    logs = server.run_command(
        state,
        command="kubectl logs database-0 -n saas-prod --since-time=not-a-timestamp",
    )

    assert logs["result"]["exit_code"] == 1
    assert logs["result"]["support_status"] == "partial"
    assert logs["result"]["matched_rule_id"] == "kubectl.logs.since-time"
    assert 'invalid --since-time value "not-a-timestamp"' in logs["result"]["stderr"]


def test_kubectl_logs_label_selector_renders_matching_pod_logs(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="db_disk_exhaustion")

    logs = server.run_command(
        state,
        command=(
            "kubectl logs -l app.kubernetes.io/name=database "
            "-n saas-prod --prefix"
        ),
    )

    assert logs["result"]["support_status"] == "supported"
    assert logs["result"]["matched_rule_id"] == "kubectl.logs.selector"
    assert "database-0/database " in logs["result"]["stdout"]
    assert "disk_used_pct=92" in logs["result"]["stdout"]
    assert "cacheservice-0" not in logs["result"]["stdout"]


def test_kubectl_logs_named_pod_takes_precedence_over_selector(amc, tmp_path, monkeypatch):
    state = _build_state(amc, tmp_path, scenarios="db_disk_exhaustion")

    def fail_snapshot(_state):
        raise AssertionError("named pod logs should not build a resource snapshot")

    monkeypatch.setattr(server, "resource_snapshot", fail_snapshot)

    logs = server.run_command(
        state,
        command=(
            "kubectl logs database-0 -l app.kubernetes.io/name=cacheservice "
            "-n saas-prod --prefix"
        ),
    )

    assert logs["result"]["support_status"] == "supported"
    assert logs["result"]["matched_rule_id"] == "kubectl.logs.pod"
    assert "database-0/database " in logs["result"]["stdout"]
    assert "disk_used_pct=92" in logs["result"]["stdout"]
    assert "cacheservice-0" not in logs["result"]["stdout"]


def test_kubectl_logs_tail_limits_returned_lines(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="auth_brute_force")

    logs = server.run_command(
        state,
        command="kubectl logs authservice-0 -n saas-prod --tail=1",
    )

    assert logs["result"]["support_status"] == "supported"
    assert "apigateway login route returning 429" in logs["result"]["stdout"]
    assert "authservice failed_login_rate elevated" not in logs["result"]["stdout"]


def test_kubectl_logs_rejects_invalid_tail(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="auth_brute_force")

    logs = server.run_command(
        state,
        command="kubectl logs authservice-0 -n saas-prod --tail=last",
    )

    assert logs["result"]["exit_code"] == 1
    assert logs["result"]["support_status"] == "partial"
    assert logs["result"]["matched_rule_id"] == "kubectl.logs.tail"
    assert 'invalid --tail value "last"' in logs["result"]["stderr"]


def test_kubectl_logs_rejects_mismatched_container(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="db_disk_exhaustion")

    logs = server.run_command(
        state,
        command="kubectl logs database-0 -n saas-prod -c apigateway",
    )

    assert logs["result"]["exit_code"] == 1
    assert logs["result"]["support_status"] == "partial"
    assert 'container "apigateway" is not valid for pod "database-0"' in logs["result"]["stderr"]


@pytest.mark.parametrize(
    "command",
    [
        "kubectl logs database-0 -n saas-prod -c",
        "kubectl logs database-0 -n saas-prod --container=",
    ],
)
def test_kubectl_logs_rejects_missing_container_value(amc, tmp_path, command):
    state = _build_state(amc, tmp_path, scenarios="db_disk_exhaustion")

    logs = server.run_command(state, command=command)

    assert logs["result"]["exit_code"] == 1
    assert logs["result"]["support_status"] == "partial"
    assert "requires a container name" in logs["result"]["stderr"]


def test_helm_and_rollout_responses_reflect_bad_canary(amc, tmp_path):
    state = _build_state(
        amc,
        tmp_path,
        scenarios="deploy_bad_canary_rollback",
        signal_level="high",
        days=1,
    )

    history = server.run_command(state, command="helm history simulated-saas -n saas-prod")
    assert history["result"]["exit_code"] == 0
    assert "failed" in history["result"]["stdout"]
    assert "Rollback to revision 2" in history["result"]["stdout"]

    rollout = server.run_command(
        state,
        command="kubectl rollout status deployment/apigateway -n saas-prod",
    )
    assert rollout["result"]["exit_code"] == 0
    assert "rolled back" in rollout["result"]["stdout"]


def test_helm_history_uses_single_synthetic_timestamp_per_response(amc, tmp_path):
    state = _build_state(
        amc,
        tmp_path,
        scenarios="deploy_bad_canary_rollback",
        signal_level="high",
        days=1,
    )

    class _ChangingClock:
        calls = 0

        def now(self):
            self.calls += 1
            return _dt.datetime(2026, 4, 1, 12, 0, self.calls)

    clock = _ChangingClock()
    state.clock = clock
    history = server._render_helm_history(state)

    assert clock.calls == 1
    assert history.count("2026-04-01 12:00:01") == 2


def test_unsupported_commands_are_grouped_for_debugging(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="db_disk_exhaustion")

    result = server.run_command(state, command="kubectl debug pod/database-0 -n saas-prod")
    assert result["result"]["support_status"] == "unsupported"

    partial = server.run_command(
        state,
        command="kubectl get pods --chunk-size=50 -n saas-prod",
    )
    assert partial["result"]["support_status"] == "partial"
    assert "--chunk-size=50" in partial["result"]["stderr"]

    summary = state.traces.unsupported_summary()
    assert len(summary) == 2
    assert {item["count"] for item in summary} == {1}
    assert any("kubectl debug" in item["fingerprint"] for item in summary)
    assert any("renderer" in item["guessed_intent"] for item in summary)


def test_every_scenario_has_kubernetes_and_helm_ops_surface(amc, tmp_path):
    assert set(server.OPS_SCENARIO_PROFILES) == set(amc.SCENARIOS)

    for scenario_id, scenario in amc.SCENARIOS.items():
        components = ",".join(scenario.components_touched)
        state = _build_state(
            amc,
            tmp_path / scenario_id,
            scenarios=scenario_id,
            components=components,
            signal_level="high",
            days=max(2, scenario.days_required),
        )
        profile = server.OPS_SCENARIO_PROFILES[scenario_id]
        primary = profile.affected_components[0]
        resources = server.resource_snapshot(state)
        deployment = next(
            (
                item for item in resources["deployments"]
                if item["name"] == primary
            ),
            None,
        )
        assert deployment is not None, scenario_id
        assert deployment["status"] != "Healthy", scenario_id

        events = server.run_command(state, command="kubectl get events -n saas-prod")
        expected_event = next(
            (
                item for item in resources["events"]
                if item["object"] == f"pod/{_pod_name(primary)}"
            ),
            None,
        )
        assert expected_event is not None, scenario_id
        assert expected_event["message"] in events["result"]["stdout"], scenario_id

        logs = server.run_command(
            state,
            command=f"kubectl logs {_pod_name(primary)} -n saas-prod",
        )
        assert "health probe ok" not in logs["result"]["stdout"], scenario_id

        rollout = server.run_command(
            state,
            command=f"kubectl rollout status deployment/{primary} -n saas-prod",
        )
        assert (profile.rollout_note or profile.summary) in rollout["result"]["stdout"]

        helm = server.run_command(state, command="helm status simulated-saas -n saas-prod")
        assert profile.helm_notes in helm["result"]["stdout"], scenario_id


def test_expanded_kubectl_and_helm_command_coverage(amc, tmp_path):
    state = _build_state(
        amc,
        tmp_path,
        scenarios="deploy_bad_canary_rollback",
        signal_level="high",
        days=1,
    )

    auth = server.run_command(state, command="kubectl auth can-i get pods -n saas-prod")
    assert auth["result"]["support_status"] == "supported"
    assert auth["result"]["stdout"] == "yes\n"

    resources = server.run_command(state, command="kubectl api-resources")
    assert "cronjobs" in resources["result"]["stdout"]
    assert "endpointslices" in resources["result"]["stdout"]

    all_resources = server.run_command(state, command="kubectl get all -n saas-prod")
    assert "deployment" in all_resources["result"]["stdout"]
    assert "scheduler-backfill" in all_resources["result"]["stdout"]

    filtered = server.run_command(
        state,
        command="kubectl get pods --field-selector metadata.name=apigateway-0 -n saas-prod",
    )
    assert "apigateway-0" in filtered["result"]["stdout"]
    assert "cacheservice-0" not in filtered["result"]["stdout"]

    configmaps = server.run_command(state, command="kubectl get configmaps -o name -n saas-prod")
    assert "configmap/simulated-saas-config" in configmaps["result"]["stdout"]

    service = server.run_command(state, command="kubectl describe service apigateway -n saas-prod")
    assert "Endpoints:" in service["result"]["stdout"]

    rollout = server.run_command(
        state,
        command="kubectl rollout history deployment/apigateway -n saas-prod",
    )
    assert "canary readiness failed" in rollout["result"]["stdout"]

    wait = server.run_command(
        state,
        command="kubectl wait --for=condition=available deployment/apigateway -n saas-prod",
    )
    assert "condition met" in wait["result"]["stdout"]

    exec_result = server.run_command(state, command="kubectl exec -n saas-prod apigateway-0 -- env")
    assert "SERVICE_NAME=apigateway" in exec_result["result"]["stdout"]

    follow_logs = server.run_command(state, command="kubectl logs -f apigateway-0 -n saas-prod")
    assert follow_logs["result"]["support_status"] == "supported"
    assert follow_logs["result"]["matched_rule_id"] == "kubectl.logs.pod"
    assert "apigateway" in follow_logs["result"]["stdout"]
    assert "expected pod name" not in follow_logs["result"]["stderr"]

    helm_all = server.run_command(state, command="helm get all simulated-saas -n saas-prod")
    assert "COMPUTED VALUES" in helm_all["result"]["stdout"]
    assert "MANIFEST" in helm_all["result"]["stdout"]

    helm_template = server.run_command(state, command="helm template simulated-saas ./chart")
    assert "kind: Deployment" in helm_template["result"]["stdout"]

    helm_test = server.run_command(state, command="helm test simulated-saas -n saas-prod")
    assert "SucceededAfterRollback" in helm_test["result"]["stdout"]


def test_kubectl_explain_projects_common_resource_schemas(amc, tmp_path):
    state = _build_state(
        amc,
        tmp_path,
        scenarios="cache_leak_restart",
        signal_level="high",
        days=3,
    )

    pods = server.run_command(state, command="kubectl explain pods -n saas-prod")
    assert pods["result"]["support_status"] == "supported"
    assert pods["result"]["matched_rule_id"] == "kubectl.explain.pods"
    assert "KIND:       Pod" in pods["result"]["stdout"]
    assert "VERSION:    v1" in pods["result"]["stdout"]
    assert "FIELDS:" in pods["result"]["stdout"]
    assert "spec" in pods["result"]["stdout"]

    replicas = server.run_command(
        state,
        command="kubectl explain deployment.spec.replicas --api-version=apps/v1 -n saas-prod",
    )
    assert replicas["result"]["support_status"] == "supported"
    assert "FIELD:      spec.replicas <integer>" in replicas["result"]["stdout"]
    assert "spec.replicas field projected" in replicas["result"]["stdout"]

    api_version_mismatch = server.run_command(
        state,
        command="kubectl explain deployment.spec.replicas --api-version=v1 -n saas-prod",
    )
    assert api_version_mismatch["result"]["support_status"] == "partial"
    assert api_version_mismatch["result"]["matched_rule_id"] == "kubectl.explain.api-version"
    assert "available as apps/v1, not v1" in api_version_mismatch["result"]["stderr"]

    missing_api_version = server.run_command(
        state,
        command="kubectl explain deployment.spec.replicas --api-version -n saas-prod",
    )
    assert missing_api_version["result"]["support_status"] == "partial"
    assert missing_api_version["result"]["matched_rule_id"] == "kubectl.explain.api-version.invalid"
    assert "--api-version requires a non-empty value" in missing_api_version["result"]["stderr"]

    recursive = server.run_command(
        state,
        command="kubectl explain pods.spec --recursive -n saas-prod",
    )
    assert recursive["result"]["support_status"] == "supported"
    assert "containers" in recursive["result"]["stdout"]
    assert "image" in recursive["result"]["stdout"]

    unknown_field = server.run_command(
        state,
        command="kubectl explain pods.spec.missingField -n saas-prod",
    )
    assert unknown_field["result"]["support_status"] == "partial"
    assert unknown_field["result"]["matched_rule_id"] == "kubectl.explain.unknown-field"

    unknown_resource = server.run_command(
        state,
        command="kubectl explain widgets.example.com -n saas-prod",
    )
    assert unknown_resource["result"]["support_status"] == "unsupported"
    assert unknown_resource["result"]["matched_rule_id"] == "kubectl.explain.unsupported"


def test_openapi_schema_generation_reuses_resource_snapshot(amc, tmp_path, monkeypatch):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    original_resource_snapshot = server._server_ops.resource_snapshot
    snapshot_calls = 0

    def counted_resource_snapshot(snapshot_state):
        nonlocal snapshot_calls
        snapshot_calls += 1
        return original_resource_snapshot(snapshot_state)

    monkeypatch.setattr(server._server_ops, "resource_snapshot", counted_resource_snapshot)

    discovery = server.kubernetes_api_response(state, "GET", "/openapi/v3", {}, "")
    assert discovery is not None
    assert discovery.status == 200
    assert snapshot_calls == 0

    openapi_v2 = server.kubernetes_api_response(state, "GET", "/openapi/v2", {}, "")
    assert openapi_v2 is not None
    assert openapi_v2.status == 200
    assert "io.k8s.api.core.v1.Pod" in openapi_v2.body["definitions"]
    assert snapshot_calls == 1

    snapshot_calls = 0
    openapi_v3 = server.kubernetes_api_response(state, "GET", "/openapi/v3/api/v1", {}, "")
    assert openapi_v3 is not None
    assert openapi_v3.status == 200
    assert "io.k8s.api.core.v1.Pod" in openapi_v3.body["components"]["schemas"]
    assert snapshot_calls == 1


def test_openapi_v3_discovery_derives_group_versions_from_explain_targets(monkeypatch):
    monkeypatch.setitem(
        server._server_ops._EXPLAIN_RESOURCE_TARGETS,
        "widgets",
        ("example.com", "v1alpha1", "widgets"),
    )

    discovery = server._server_ops._k8s_openapi_v3_discovery()

    assert list(discovery["paths"]) == sorted(discovery["paths"])
    assert discovery["paths"]["apis/example.com/v1alpha1"] == {
        "serverRelativeURL": "/openapi/v3/apis/example.com/v1alpha1?hash=amc-example-com-v1alpha1",
    }


def test_kubectl_delete_ingress_uses_stable_resource_prefix(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)

    # Delete the modeled ``apigateway`` ingress (not a ghost name): A-013 now
    # 404s a delete of a resource absent from the snapshot, so this asserts the
    # stable prefix on the success path.
    result = server.run_command(
        state,
        command="kubectl delete ingress apigateway -n saas-prod",
    )

    assert result["result"]["stdout"] == 'ingress "apigateway" deleted\n'
    assert "ingre " not in result["result"]["stdout"]


def test_kubectl_scale_ingress_uses_stable_resource_prefix(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)

    result = server.run_command(
        state,
        command="kubectl scale ingress public-edge --replicas=2 -n saas-prod",
    )

    assert result["result"]["stdout"] == "ingress/public-edge scaled\n"
    assert "ingre/" not in result["result"]["stdout"]


def test_kubectl_wait_ingress_uses_stable_resource_prefix(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)

    result = server.run_command(
        state,
        command="kubectl wait --for=condition=ready ingress/public-edge -n saas-prod",
    )

    assert result["result"]["stdout"] == "ingress/public-edge condition met: condition=ready\n"
    assert "ingre/" not in result["result"]["stdout"]


def test_mutation_events_are_bounded_by_debug_ring_size(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3, trace_limit=3)
    now = state.clock.now()

    for index in range(5):
        state.mutations.record_event(
            "Normal",
            f"Mutation{index}",
            f"pod/apigateway-{index}",
            f"mutation event {index}",
            now + _dt.timedelta(seconds=index),
        )

    with state.mutations.lock:
        reasons = [event["reason"] for event in state.mutations.extra_events]
    assert reasons == ["Mutation2", "Mutation3", "Mutation4"]

    summary = state.summary()["mutations"]
    assert summary["extra_event_count"] == 3
    assert summary["extra_event_limit"] == 3

    resource_reasons = [event["reason"] for event in server.resource_snapshot(state)["events"]]
    assert "Mutation0" not in resource_reasons
    assert "Mutation4" in resource_reasons


def test_repeated_mutation_events_are_counted_once(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3, trace_limit=3)
    now = state.clock.now()

    for _ in range(3):
        state.mutations.record_event(
            "Normal",
            "DebugToggle",
            "configmap/debug-flags",
            "configmap debug-flags configured in simulator state",
            now,
        )

    summary = state.mutations.summary()
    assert summary["extra_event_count"] == 1
    assert summary["drift"]["event_overlays"] == 1
    events = [
        row for row in server.resource_snapshot(state)["events"]
        if row["reason"] == "DebugToggle"
    ]
    assert len(events) == 1
    assert events[0]["count"] == 3


def test_configured_resource_events_are_distinct_per_namespace(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    now = state.clock.now()

    for namespace in ("tenant-a", "tenant-b"):
        state.mutations.put_resource(
            "configmaps",
            "debug-flags",
            {"name": "debug-flags"},
            now=now,
            namespace=namespace,
        )

    events = [
        event for event in state.mutations.extra_events
        if event["reason"] == "Configured"
    ]
    assert [event["namespace"] for event in events] == ["tenant-a", "tenant-b"]
    assert [event["count"] for event in events] == [1, 1]


def test_deleted_resource_events_are_distinct_per_namespace(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    now = state.clock.now()

    for namespace in ("tenant-a", "tenant-b"):
        state.mutations.delete_resource(
            "configmaps",
            "debug-flags",
            now=now,
            namespace=namespace,
        )

    events = [
        event for event in state.mutations.extra_events
        if event["reason"] == "Deleted"
    ]
    assert [event["namespace"] for event in events] == ["tenant-a", "tenant-b"]
    assert [event["count"] for event in events] == [1, 1]


def test_deleted_pods_are_reconciled_with_replacement_pods(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    before = [
        row for row in server.resource_snapshot(state)["pods"]
        if row["component"] == "apigateway"
    ]

    state.mutations.delete_pod("apigateway-0", now=state.clock.now())

    snapshot = server.resource_snapshot(state)
    after = [row for row in snapshot["pods"] if row["component"] == "apigateway"]
    assert len(after) == len(before)
    assert all(row["name"] != "apigateway-0" for row in after)
    assert any(row["name"].startswith("apigateway-recreated-") for row in after)
    endpoint_slice = next(row for row in snapshot["endpointslices"] if row["service"] == "apigateway")
    assert endpoint_slice["endpoints"] == len(after)


def test_deleting_replacement_pod_updates_original_component_overlay(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    state.mutations.delete_pod("apigateway-0", now=state.clock.now())
    replacement = next(
        row["name"] for row in server.resource_snapshot(state)["pods"]
        if row["name"].startswith("apigateway-recreated-")
    )

    state.mutations.delete_pod(replacement, now=state.clock.now())

    summary = state.mutations.summary()
    assert "apigateway-recreated" not in summary["workloads"]
    assert summary["workloads"]["apigateway"]["restarts_delta"] == 2


def test_mutating_commands_update_simulated_state(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)

    scale = server.run_command(
        state,
        command="kubectl scale deployment/apigateway --replicas=5 -n saas-prod",
    )
    assert scale["result"]["matched_rule_id"] == "kubectl.scale"
    resources = server.resource_snapshot(state)
    gateway = next(item for item in resources["deployments"] if item["name"] == "apigateway")
    assert gateway["ready"] == "5/5"

    restart = server.run_command(
        state,
        command="kubectl rollout restart deployment/apigateway -n saas-prod",
    )
    assert "restarted" in restart["result"]["stdout"]
    resources = server.resource_snapshot(state)
    gateway_pod = next(item for item in resources["pods"] if item["name"] == "apigateway-0")
    assert gateway_pod["restarts"] >= 1

    upgrade = server.run_command(
        state,
        command="helm upgrade simulated-saas ./chart -n saas-prod",
    )
    assert "release state updated" in upgrade["result"]["stdout"]
    assert "REVISION: 4" in server.run_command(
        state,
        command="helm status simulated-saas -n saas-prod",
    )["result"]["stdout"]

    rollback = server.run_command(
        state,
        command="helm rollback simulated-saas 2 -n saas-prod",
    )
    assert "release state updated" in rollback["result"]["stdout"]
    assert "Rollback to revision 2" in server.run_command(
        state,
        command="helm history simulated-saas -n saas-prod",
    )["result"]["stdout"]

    uninstall = server.run_command(
        state,
        command="helm uninstall simulated-saas -n saas-prod",
    )
    assert uninstall["result"]["stdout"] == 'release "simulated-saas" uninstalled\n'
    helm_list = server.run_command(state, command="helm list -n saas-prod")
    assert "simulated-saas" not in helm_list["result"]["stdout"]

    install = server.run_command(
        state,
        command="helm install simulated-saas ./chart -n saas-prod --set feature.debug=true",
    )
    assert "STATUS: deployed" in install["result"]["stdout"]
    assert "feature.debug: true" in server.run_command(
        state,
        command="helm get values simulated-saas -n saas-prod",
    )["result"]["stdout"]

    server.run_command(state, command="helm uninstall simulated-saas -n saas-prod")
    assert "feature.debug" not in server.run_command(
        state,
        command="helm get values simulated-saas -n saas-prod",
    )["result"]["stdout"]

    create = server.run_command(
        state,
        command="kubectl create configmap debug-flags --from-literal=mode=on -n saas-prod",
    )
    assert create["result"]["stdout"] == "configmap/debug-flags created\n"
    assert "debug-flags" in server.run_command(
        state,
        command="kubectl get configmaps -n saas-prod",
    )["result"]["stdout"]

    server.run_command(state, command="kubectl delete configmap debug-flags -n saas-prod")
    assert "debug-flags" not in server.run_command(
        state,
        command="kubectl get configmaps -n saas-prod",
    )["result"]["stdout"]


def test_kubectl_rollout_pause_resume_and_undo_update_overlay(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)

    pause = server.run_command(
        state,
        command="kubectl rollout pause deployment apigateway -n saas-prod",
    )
    assert pause["result"]["matched_rule_id"] == "kubectl.rollout.pause"
    assert pause["result"]["stdout"] == "deployment.apps/apigateway paused\n"
    paused = next(
        item for item in server.resource_snapshot(state)["deployments"]
        if item["name"] == "apigateway"
    )
    assert paused["status"] == "Paused"

    paused_status = server.run_command(
        state,
        command="kubectl rollout status deployment/apigateway -n saas-prod",
    )
    assert 'deployment "apigateway" rollout to finish: Paused' in paused_status["result"]["stdout"]
    events = server.run_command(state, command="kubectl get events -n saas-prod")
    assert "RolloutPaused" in events["result"]["stdout"]

    resume = server.run_command(
        state,
        command="kubectl rollout resume deployment/apigateway -n saas-prod",
    )
    assert resume["result"]["matched_rule_id"] == "kubectl.rollout.resume"
    assert resume["result"]["stdout"] == "deployment.apps/apigateway resumed\n"
    resumed_status = server.run_command(
        state,
        command="kubectl rollout status deployment/apigateway -n saas-prod",
    )
    assert 'deployment "apigateway" successfully rolled out' in resumed_status["result"]["stdout"]

    undo = server.run_command(
        state,
        command="kubectl rollout undo deployment/apigateway --to-revision=2 -n saas-prod",
    )
    assert undo["result"]["matched_rule_id"] == "kubectl.rollout.undo"
    assert undo["result"]["stdout"] == "deployment.apps/apigateway rolled back to revision 2\n"
    rolled_back = next(
        item for item in server.resource_snapshot(state)["deployments"]
        if item["name"] == "apigateway"
    )
    assert rolled_back["status"] == "RolledBack"
    rollback_status = server.run_command(
        state,
        command="kubectl rollout status deployment/apigateway -n saas-prod",
    )
    assert "deployment was rolled back by simulator command" in rollback_status["result"]["stdout"]
    events = server.run_command(state, command="kubectl get events -n saas-prod")
    assert "RolloutUndo" in events["result"]["stdout"]
    assert "rolled back to revision 2" in events["result"]["stdout"]
    assert "rolled back to 2 revision" not in events["result"]["stdout"]


def test_kubectl_rollout_undo_without_revision_uses_previous(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)

    result = server.run_command(
        state,
        command="kubectl rollout undo deployment/apigateway -n saas-prod --to-revision",
    )

    assert result["result"]["matched_rule_id"] == "kubectl.rollout.undo"
    assert result["result"]["stdout"] == "deployment.apps/apigateway rolled back\n"
    events = server.run_command(state, command="kubectl get events -n saas-prod")
    assert "rolled back to previous revision" in events["result"]["stdout"]


def test_kubectl_rollout_rejects_non_deployment_targets(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)

    service_target = server.run_command(
        state,
        command="kubectl rollout pause service/apigateway -n saas-prod",
    )
    missing_name = server.run_command(
        state,
        command="kubectl rollout pause deployment -n saas-prod",
    )

    assert service_target["result"]["support_status"] == "unsupported"
    assert service_target["result"]["matched_rule_id"] == "unsupported"
    assert "kubectl rollout pause is not implemented" in service_target["result"]["stderr"]
    assert missing_name["result"]["support_status"] == "unsupported"
    assert "kubectl rollout pause is not implemented" in missing_name["result"]["stderr"]


def test_kubectl_patch_diff_and_dry_run_commands(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)

    create = server.run_command(
        state,
        command="kubectl create configmap debug-flags --from-literal=mode=on -n saas-prod",
    )
    assert create["result"]["support_status"] == "supported"

    multi_literal = server.run_command(
        state,
        command=(
            "kubectl create configmap multi-flags --from-literal=one=1 "
            "--from-literal=two=2 -n saas-prod"
        ),
    )
    assert multi_literal["result"]["support_status"] == "supported"
    multi_output = server.run_command(
        state,
        command="kubectl get configmaps -n saas-prod",
    )["result"]["stdout"]
    multi_row = next(line for line in multi_output.splitlines() if line.startswith("multi-flags"))
    assert multi_row.split()[1] == "2"

    merge_patch = server.run_command(
        state,
        command=(
            "kubectl patch configmap debug-flags --type=merge "
            "--patch '{\"data\":{\"extra\":\"1\"}}' -n saas-prod"
        ),
    )
    assert merge_patch["result"]["matched_rule_id"] == "kubectl.patch.configmaps"
    assert merge_patch["result"]["stdout"] == "configmap/debug-flags patched\n"
    configmaps_output = server.run_command(
        state,
        command="kubectl get configmaps -n saas-prod",
    )["result"]["stdout"]
    debug_row = next(line for line in configmaps_output.splitlines() if line.startswith("debug-flags"))
    assert debug_row.split()[1] == "2"

    json_patch = server.run_command(
        state,
        command=(
            "kubectl patch deployment/apigateway --type=json "
            "-p '[{\"op\":\"replace\",\"path\":\"/spec/replicas\",\"value\":2}]' -n saas-prod"
        ),
    )
    assert json_patch["result"]["support_status"] == "supported"
    assert json_patch["result"]["stdout"] == "deployment/apigateway patched\n"
    assert json_patch["trace"]["parsed_flags"]["-p"].startswith("[")
    gateway = next(
        item for item in server.resource_snapshot(state)["deployments"]
        if item["name"] == "apigateway"
    )
    assert gateway["ready"] == "2/2"

    dry_run = server.run_command(
        state,
        command=(
            "kubectl create configmap dry-run-flags --from-literal=mode=off "
            "--dry-run=client -n saas-prod"
        ),
    )
    assert dry_run["result"]["stdout"] == "configmap/dry-run-flags created (dry run)\n"
    assert "dry-run-flags" not in server.run_command(
        state,
        command="kubectl get configmaps -n saas-prod",
    )["result"]["stdout"]

    diff = server.run_command(
        state,
        command="kubectl diff -f configmap-dry-run-flags.yaml -n saas-prod",
    )
    assert diff["result"]["exit_code"] == 1
    assert diff["result"]["support_status"] == "supported"
    assert "desired/configmap/dry-run-flags" in diff["result"]["stdout"]
    assert "dry-run-flags" not in server.run_command(
        state,
        command="kubectl get configmaps -n saas-prod",
    )["result"]["stdout"]

    unsupported_json_patch = server.run_command(
        state,
        command=(
            "kubectl patch configmap debug-flags --type=json "
            "-p '[{\"op\":\"copy\",\"path\":\"/data/copied\",\"from\":\"/data/mode\"}]' -n saas-prod"
        ),
    )
    assert unsupported_json_patch["result"]["support_status"] == "partial"
    assert unsupported_json_patch["result"]["matched_rule_id"] == "kubectl.patch.json"

    missing_remove = server.run_command(
        state,
        command=(
            "kubectl patch configmap debug-flags --type=json "
            "-p '[{\"op\":\"remove\",\"path\":\"/data/missing\"}]' -n saas-prod"
        ),
    )
    assert missing_remove["result"]["support_status"] == "partial"
    assert "does not exist" in missing_remove["result"]["stderr"]


def test_kubectl_apply_reads_multi_document_yaml_manifest(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    manifest = tmp_path / "simulator-stack.yaml"
    manifest.write_text(
        """
apiVersion: v1
kind: ConfigMap
metadata:
  name: applied-config
  namespace: tools
  labels:
    app: applied
data:
  feature: enabled
  mode: simulator
---
apiVersion: v1
kind: Service
metadata:
  name: applied-api
  namespace: tools
spec:
  type: ClusterIP
  selector:
    app: applied
  ports:
    - port: 9090
""".strip(),
        encoding="utf-8",
    )

    result = server.run_command(state, command=f"kubectl apply -f {manifest} -n saas-prod")

    assert result["result"]["support_status"] == "supported"
    assert result["result"]["matched_rule_id"] == "kubectl.apply.manifest"
    assert result["result"]["stdout"] == (
        "configmap/applied-config configured\n"
        "service/applied-api configured\n"
    )
    configmaps = server.run_command(
        state,
        command="kubectl get configmaps -n tools",
    )["result"]["stdout"]
    services = server.run_command(
        state,
        command="kubectl get services -n tools",
    )["result"]["stdout"]
    assert "applied-config" in configmaps
    assert "applied-api" in services
    resources = server.resource_snapshot(state)
    applied_config = next(
        item for item in resources["configmaps"]
        if item["name"] == "applied-config" and item["namespace"] == "tools"
    )
    assert applied_config["keys"] == {"feature": "enabled", "mode": "simulator"}
    assert applied_config["labels"]["app"] == "applied"
    applied_service = next(
        item for item in resources["services"]
        if item["name"] == "applied-api" and item["namespace"] == "tools"
    )
    assert applied_service["ports"] == "9090/TCP"
    summary = state.mutations.summary()
    assert "tools/applied-config" in summary["created_resources"]["configmaps"]
    assert "tools/applied-api" in summary["created_resources"]["services"]


def test_kubectl_apply_reads_json_list_manifest(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    manifest = tmp_path / "resources.json"
    manifest.write_text(
        json.dumps([
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "json-config", "namespace": "tools"},
                "data": {"source": "json"},
            },
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "json-secret", "namespace": "tools"},
                "stringData": {"password": "simulated", "token": "redacted"},
            },
        ]),
        encoding="utf-8",
    )

    result = server.run_command(state, command=f"kubectl apply -f {manifest} -n tools")

    assert result["result"]["support_status"] == "supported"
    assert result["result"]["stdout"] == (
        "configmap/json-config configured\n"
        "secret/json-secret configured\n"
    )
    secrets = server.run_command(state, command="kubectl get secrets -n tools")["result"]["stdout"]
    assert "json-secret" in secrets
    json_config = next(
        item for item in server.resource_snapshot(state)["configmaps"]
        if item["name"] == "json-config" and item["namespace"] == "tools"
    )
    assert json_config["keys"] == {"source": "json"}


def test_kubectl_apply_reads_json_object_manifest(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    manifest = tmp_path / "single-resource.json"
    manifest.write_text(
        json.dumps({
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "json-object-config",
                "namespace": "tools",
                "labels": {"source": "object"},
            },
            "data": {"mode": "single"},
        }),
        encoding="utf-8",
    )

    result = server.run_command(state, command=f"kubectl apply -f {manifest} -n tools")

    assert result["result"]["support_status"] == "supported"
    assert result["result"]["matched_rule_id"] == "kubectl.apply.manifest"
    assert result["result"]["stdout"] == "configmap/json-object-config configured\n"
    json_config = next(
        item for item in server.resource_snapshot(state)["configmaps"]
        if item["name"] == "json-object-config" and item["namespace"] == "tools"
    )
    assert json_config["keys"] == {"mode": "single"}
    assert json_config["labels"]["source"] == "object"


def test_kubectl_apply_manifest_dry_run_does_not_mutate(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    manifest = tmp_path / "dry-run.yaml"
    manifest.write_text(
        """
apiVersion: v1
kind: ConfigMap
metadata:
  name: dry-run-applied
  namespace: tools
data:
  mode: preview
""".strip(),
        encoding="utf-8",
    )

    result = server.run_command(
        state,
        command=f"kubectl apply --dry-run=client -f {manifest} -n tools",
    )

    assert result["result"]["support_status"] == "supported"
    assert result["result"]["stdout"] == "configmap/dry-run-applied configured (dry run)\n"
    assert "dry-run-applied" not in server.run_command(
        state,
        command="kubectl get configmaps -n tools",
    )["result"]["stdout"]
    assert "configmaps" not in state.mutations.summary()["created_resources"]


def test_kubectl_apply_manifest_rejects_unsupported_documents_atomically(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    manifest = tmp_path / "mixed.yaml"
    manifest.write_text(
        """
apiVersion: v1
kind: ConfigMap
metadata:
  name: should-not-apply
  namespace: tools
data:
  mode: blocked
---
apiVersion: example.com/v1
kind: Widget
metadata:
  name: unsupported-widget
  namespace: tools
""".strip(),
        encoding="utf-8",
    )

    result = server.run_command(state, command=f"kubectl apply -f {manifest} -n tools")

    assert result["result"]["support_status"] == "partial"
    assert result["result"]["matched_rule_id"] == "kubectl.apply.manifest.unsupported"
    assert "Widget" in result["result"]["stderr"]
    assert "should-not-apply" not in server.run_command(
        state,
        command="kubectl get configmaps -n tools",
    )["result"]["stdout"]


def test_kubectl_apply_manifest_reports_non_utf8_read_failure(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    manifest = tmp_path / "invalid-encoding.yaml"
    manifest.write_bytes(b"\xff\xfe\x00")

    result = server.run_command(state, command=f"kubectl apply -f {manifest} -n tools")

    assert result["result"]["support_status"] == "partial"
    assert result["result"]["matched_rule_id"] == "kubectl.apply.manifest.read"
    assert "unable to read manifest" in result["result"]["stderr"]
    assert state.mutations.summary()["created_resources"] == {}


def test_kubectl_apply_missing_manifest_all_namespaces_uses_active_namespace(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    manifest = tmp_path / "configmap-review-flag.yaml"

    result = server.run_command(state, command=f"kubectl apply -A -f {manifest}")

    assert result["result"]["support_status"] == "supported"
    assert result["result"]["stdout"] == "configmap/review-flag configured\n"
    configmaps = server.run_command(
        state,
        command="kubectl get configmaps -n saas-prod",
    )["result"]["stdout"]
    assert "review-flag" in configmaps
    resources = server.resource_snapshot(state)
    applied = next(item for item in resources["configmaps"] if item["name"] == "review-flag")
    assert applied["namespace"] == "saas-prod"
    summary = state.mutations.summary()
    assert "saas-prod/review-flag" in summary["created_resources"]["configmaps"]
    assert "*/review-flag" not in summary["created_resources"]["configmaps"]


def test_kubectl_patch_p_flag_space_separated(amc, tmp_path):
    """kubectl patch -p <json> (space-separated, no =) must capture the payload."""
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)

    result = server.run_command(
        state,
        command=(
            "kubectl patch deployment/apigateway --type=merge "
            '-p \'{"spec":{"replicas":3}}\' -n saas-prod'
        ),
    )
    assert result["result"]["support_status"] == "supported"
    assert result["result"]["stdout"] == "deployment/apigateway patched\n"
    gateway = next(
        item
        for item in server.resource_snapshot(state)["deployments"]
        if item["name"] == "apigateway"
    )
    assert gateway["ready"] == "3/3"


def test_kubectl_create_configmap_multiple_from_literal(amc, tmp_path):
    """Multiple --from-literal flags must all produce keys in the configmap."""
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)

    result = server.run_command(
        state,
        command=(
            "kubectl create configmap multi-literal "
            "--from-literal=key1=val1 --from-literal=key2=val2 --from-literal=key3=val3 "
            "-n saas-prod"
        ),
    )
    assert result["result"]["support_status"] == "supported"

    get_result = server.run_command(
        state,
        command="kubectl get configmaps multi-literal -n saas-prod",
    )
    # Find the multi-literal row by name; the list may include pre-existing configmaps
    row = next(
        line for line in get_result["result"]["stdout"].splitlines()
        if line.startswith("multi-literal")
    )
    assert row.split()[1] == "3"


def test_kubectl_create_configmap_from_file(amc, tmp_path):
    """--from-file flags should contribute keys to the configmap."""
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)

    result = server.run_command(
        state,
        command=(
            "kubectl create configmap file-config "
            "--from-file=app.conf=/etc/app/config.conf "
            "--from-file=/etc/app/extra.conf "
            "-n saas-prod"
        ),
    )
    assert result["result"]["support_status"] == "supported"

    get_result = server.run_command(
        state,
        command="kubectl get configmaps file-config -n saas-prod",
    )
    row = next(
        line for line in get_result["result"]["stdout"].splitlines()
        if line.startswith("file-config")
    )
    assert row.split()[1] == "2"


def test_helm_upgrade_layers_repeated_values_and_lifecycle_flags(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)

    install = server.run_command(
        state,
        command="helm install simulated-saas ./chart -n saas-prod --set feature.debug=true",
    )
    assert install["result"]["support_status"] == "supported"

    upgrade = server.run_command(
        state,
        command=(
            "helm upgrade simulated-saas ./chart -n saas-prod --reuse-values "
            "--set image.tag=canary --set worker.replicas=2 "
            "--set-string feature.enabled=true "
            "--values base.yaml --values prod.yaml --wait --timeout=5m --atomic"
        ),
    )
    assert "reused existing release values" in upgrade["result"]["stdout"]
    assert "recorded values files: base.yaml,prod.yaml" in upgrade["result"]["stdout"]
    assert "wait completed before 5m" in upgrade["result"]["stdout"]
    assert "atomic rollback was not needed" in upgrade["result"]["stdout"]

    values = server.run_command(
        state,
        command="helm get values simulated-saas -n saas-prod",
    )["result"]["stdout"]
    assert "feature.debug: true" in values
    assert "feature.enabled: true" in values
    assert "image.tag: canary" in values
    assert "worker.replicas: 2" in values
    assert "values_file: prod.yaml" in values
    assert "values_files: base.yaml,prod.yaml" in values

    dry_run_reset = server.run_command(
        state,
        command=(
            "helm upgrade simulated-saas ./chart -n saas-prod --reset-values "
            "--set only.new=true --dry-run=server"
        ),
    )
    assert "not changed during dry run" in dry_run_reset["result"]["stdout"]
    values_after_dry_run = server.run_command(
        state,
        command="helm get values simulated-saas -n saas-prod",
    )["result"]["stdout"]
    assert "feature.debug: true" in values_after_dry_run
    assert "only.new" not in values_after_dry_run

    reset = server.run_command(
        state,
        command="helm upgrade simulated-saas ./chart -n saas-prod --reset-values --set only.new=true",
    )
    assert "reset release values" in reset["result"]["stdout"]
    values_after_reset = server.run_command(
        state,
        command="helm get values simulated-saas -n saas-prod",
    )["result"]["stdout"]
    assert "only.new: true" in values_after_reset
    assert "feature.debug" not in values_after_reset


def test_command_trace_sqlite_persistence_and_search(amc, tmp_path):
    db_path = tmp_path / "commands.sqlite"
    state = _build_state(
        amc,
        tmp_path,
        scenarios="cache_leak_restart",
        days=3,
        persist_command_db=db_path,
    )

    server.run_command(state, command="kubectl get pods -n saas-prod")
    server.run_command(state, command="kubectl auth can-i get pods -n saas-prod")
    server.run_command(state, command="kubectl debug pod/cacheservice-0 -n saas-prod")

    assert db_path.exists()
    search = state.traces.search(query="auth can-i")
    assert search["total"] == 1
    assert search["items"][0]["support_status"] == "supported"

    restored = _build_state(
        amc,
        tmp_path,
        scenarios="cache_leak_restart",
        days=3,
        persist_command_db=db_path,
    )
    assert restored.traces.count() == 3
    assert restored.traces.search(support_status="unsupported")["total"] == 1
    assert restored.traces.search(command_family="kubectl")["total"] == 3
    summary = restored.traces.unsupported_summary()
    assert summary[0]["count"] == 1
    assert "kubectl debug" in summary[0]["fingerprint"]


def test_command_trace_jsonl_persistence_writes_off_the_ring_lock(tmp_path):
    persist_path = tmp_path / "commands.jsonl"
    store = server.CommandTraceStore(persist_path=persist_path)
    writes = []
    real_handle = store._jsonl_handle

    class _CaptureFile:
        def write(self, line):
            # JSONL persistence uses a long-lived handle written under its
            # own lock, off the in-memory ring lock (A-041), so a slow disk
            # cannot stall ring readers.
            assert store._jsonl_lock.locked()
            assert not store._lock.locked()
            writes.append(line)
            real_handle.write(line)

        def flush(self):
            real_handle.flush()

        def close(self):
            real_handle.close()

    store._jsonl_handle = _CaptureFile()

    store.record(_trace(1, "2026-06-25T12:01:00Z"))

    assert len(writes) == 1
    assert json.loads(writes[0])["id"] == 1

    # The flush-per-write contract makes the record durable immediately.
    store.close()
    with persist_path.open(encoding="utf-8") as fh:
        lines = [line for line in fh if line.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == 1


def test_command_trace_import_rejects_non_object_trace_entries():
    store = server.CommandTraceStore()
    payload = {
        "traces": [
            _trace(1, "2026-06-25T12:01:00Z").to_dict(),
            "not-a-trace-object",
        ],
    }

    with pytest.raises(ValueError, match="trace import entry 1 must be an object"):
        store.import_payload(payload)

    assert store.count() == 0


def test_command_trace_import_rejects_invalid_trace_objects():
    store = server.CommandTraceStore()
    trace_payload = _trace(1, "2026-06-25T12:01:00Z").to_dict()
    del trace_payload["raw_input"]

    with pytest.raises(ValueError, match="trace import entry 0 is invalid"):
        store.import_payload({"traces": [trace_payload]})

    assert store.count() == 0


def test_command_trace_from_dict_rejects_string_argv():
    trace_payload = _trace(1, "2026-06-25T12:01:00Z").to_dict()
    trace_payload["argv"] = "kubectl"

    with pytest.raises(ValueError, match="argv must be a list or tuple"):
        server.CommandTrace.from_dict(trace_payload)


def test_command_trace_from_dict_rejects_string_active_scenarios():
    trace_payload = _trace(1, "2026-06-25T12:01:00Z").to_dict()
    trace_payload["active_scenarios"] = "cache_leak_restart"

    with pytest.raises(ValueError, match="active_scenarios must be a list or tuple"):
        server.CommandTrace.from_dict(trace_payload)


@pytest.mark.parametrize("field", ["id", "exit_code"])
@pytest.mark.parametrize("value", [True, "1"])
def test_command_trace_from_dict_rejects_non_integer_fields(field, value):
    trace_payload = _trace(1, "2026-06-25T12:01:00Z").to_dict()
    trace_payload[field] = value

    with pytest.raises(ValueError, match=f"{field} must be an integer"):
        server.CommandTrace.from_dict(trace_payload)


def test_command_trace_memory_import_bumps_version_for_same_sized_replacement():
    store = server.CommandTraceStore()
    store.record(_trace(1, "2026-06-25T12:01:00Z", "kubectl get pods"))
    store.record(_trace(2, "2026-06-25T12:02:00Z", "kubectl get services"))
    version_before_import = store.version

    result = store.import_payload({
        "traces": [
            _trace(3, "2026-06-25T12:03:00Z", "kubectl get deployments").to_dict(),
            _trace(4, "2026-06-25T12:04:00Z", "kubectl get configmaps").to_dict(),
        ],
    })

    assert result["trace_count"] == 2
    assert store.version > version_before_import
    assert [item["id"] for item in store.list_traces()] == [4, 3]


def test_command_trace_sqlite_import_bumps_version_for_same_sized_replacement(tmp_path):
    db_path = tmp_path / "commands.sqlite"
    store = server.CommandTraceStore(sqlite_path=db_path)
    store.record(_trace(1, "2026-06-25T12:01:00Z", "kubectl get pods"))
    store.record(_trace(2, "2026-06-25T12:02:00Z", "kubectl get services"))
    version_before_import = store.version

    result = store.import_payload({
        "traces": [
            _trace(3, "2026-06-25T12:03:00Z", "kubectl get deployments").to_dict(),
            _trace(4, "2026-06-25T12:04:00Z", "kubectl get configmaps").to_dict(),
        ],
    })

    assert result["trace_count"] == 2
    assert store.version > version_before_import
    assert [item["id"] for item in store.list_traces()] == [4, 3]


def _trace_payload_key_split():
    """Return (required, optional) keys of ``TracePayload``, resolved properly.

    ``TracePayload.__optional_keys__`` is **empty** at runtime and every key
    reports as required. `server_traces.py` uses `from __future__ import
    annotations`, so the class body stores `"NotRequired[list[str]]"` as a
    string and the `TypedDict` machinery never sees the qualifier. mypy is
    unaffected — it reads the source — but any runtime introspection of the
    split has to resolve the annotations first, which is what
    ``get_type_hints(..., include_extras=True)`` does.
    """
    hints = typing.get_type_hints(server_traces.TracePayload, include_extras=True)
    optional = {
        key
        for key, hint in hints.items()
        if typing.get_origin(hint) is typing.NotRequired
    }
    return set(hints) - optional, optional


def test_trace_payload_typeddict_covers_exactly_the_to_dict_keys():
    """The `TypedDict` and `to_dict` cannot drift apart in either direction.

    mypy already catches both — an extra key in the returned literal is
    `typeddict-unknown-key`, a missing required one is `typeddict-item`, and
    `server_traces.py` is in the CI-gated clean-module list. This asserts the
    same thing at the value level, so the pairing is visible to a reader who
    is not running the type checker.
    """
    payload = _trace(1, "2026-06-25T12:01:00Z").to_dict()
    required, optional = _trace_payload_key_split()

    assert set(payload) == required | optional
    assert len(required) == 13
    assert len(optional) == 11
    assert set(typing.get_type_hints(server_traces.TraceListItem)) == (
        required | optional | {"version"}
    )


@pytest.mark.parametrize("key", sorted(_trace_payload_key_split()[1]))
def test_trace_payload_optional_key_is_one_from_dict_actually_defaults(key):
    """Each `NotRequired` key must be one `from_dict` survives the absence of.

    The split is derived from how `from_dict` reads each key, and nothing
    mechanical held that derivation in place: a new field annotated on the
    wrong side would type-check either way. Deriving it from behavior instead
    of restating the list is what makes this a check rather than a second copy.
    """
    payload = _trace(1, "2026-06-25T12:01:00Z").to_dict()
    del payload[key]

    server.CommandTrace.from_dict(payload)


@pytest.mark.parametrize("key", sorted(_trace_payload_key_split()[0]))
def test_trace_payload_required_key_is_one_from_dict_demands(key):
    """And each required key must be one whose absence is already an error."""
    payload = _trace(1, "2026-06-25T12:01:00Z").to_dict()
    del payload[key]

    with pytest.raises(KeyError):
        server.CommandTrace.from_dict(payload)


def test_command_trace_store_has_no_list_attribute():
    """The store's listing method is ``list_traces``; ``list`` is gone.

    Not cosmetic: a ``list`` binding in the class body shadows the builtin for
    every annotation below it, which is what kept this module out of the mypy
    clean gate. A compatibility alias would reintroduce the shadow, so the
    absence of the name is the contract.
    """
    store = server.CommandTraceStore()

    assert not hasattr(store, "list")
    assert callable(store.list_traces)


def test_command_trace_sqlite_row_missing_every_optional_key_still_loads(tmp_path):
    """A row written by an older build omits keys and must still read back.

    This is what the `NotRequired` half of `TracePayload` encodes: the store
    persists whole `to_dict` blobs, so a row predating a field simply lacks it
    and `from_dict` defaults it. Marking those keys required would type-assert
    a shape this reader is explicitly built to tolerate the absence of, and
    nothing would fail at runtime to catch the mistake — hence this test.
    """
    db_path = tmp_path / "commands.sqlite"
    store = server.CommandTraceStore(sqlite_path=db_path)
    store.record(_trace(1, "2026-06-25T12:01:00Z", "kubectl get pods"))

    # Derived, not restated: a second hard-coded copy of the `NotRequired`
    # half would keep passing after a key moved to the required half, which
    # is exactly the drift the split exists to catch.
    optional_keys = sorted(_trace_payload_key_split()[1])
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT payload_json FROM command_traces").fetchone()
        payload = json.loads(row[0])
        for key in optional_keys:
            assert key in payload, f"{key} is not actually emitted by to_dict"
            del payload[key]
        conn.execute("UPDATE command_traces SET payload_json = ?", (json.dumps(payload),))
        conn.commit()

    (item,) = store.list_traces()
    assert item["id"] == 1
    assert all(key not in item for key in optional_keys)

    trace = server.CommandTrace.from_dict(store.get(1))
    assert trace.argv == ()
    assert trace.active_scenarios == ()
    assert trace.parsed_flags == {}
    assert trace.latency_ms == 0.0
    assert trace.request_id == ""


def test_command_trace_sqlite_row_that_is_not_a_json_object_raises_typeerror(tmp_path):
    """A malformed ``payload_json`` fails at the read boundary, not downstream.

    ``_row_to_payload`` casts to ``TracePayload`` without per-field validation
    — the row is machine-written by this same store — but a value that is not
    a JSON object at all would otherwise flow on as a list or a string and
    fail with an ``AttributeError`` somewhere further away. There is no store
    API that writes one, so the bad value goes straight into the SQLite file.
    """
    db_path = tmp_path / "commands.sqlite"
    store = server.CommandTraceStore(sqlite_path=db_path)
    store.record(_trace(1, "2026-06-25T12:01:00Z", "kubectl get pods"))

    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE command_traces SET payload_json = ?", ('["not", "an", "object"]',))
        conn.commit()

    with pytest.raises(TypeError, match="expected a JSON object"):
        store.list_traces()


def test_command_trace_sqlite_record_serializes_insert_with_sqlite_lock(tmp_path, monkeypatch):
    db_path = tmp_path / "commands.sqlite"
    store = server.CommandTraceStore(sqlite_path=db_path)
    observed = []

    # The long-lived connection is only ever touched under ``_sqlite_lock``
    # (via ``_locked_conn``); ``_enforce_sqlite_retention`` runs inside that
    # locked section during an insert, so it observes the lock held.
    def capture_retention(conn):
        observed.append(store._sqlite_lock.locked())

    monkeypatch.setattr(store, "_enforce_sqlite_retention", capture_retention)

    store.record(_trace(1, "2026-06-25T12:01:00Z", "kubectl get pods"))

    assert observed == [True]


def test_command_trace_sqlite_record_serializes_payload_off_the_sqlite_lock(
    tmp_path, monkeypatch
):
    # ``_insert_sqlite`` computes ``trace.to_dict()`` before entering
    # ``_locked_conn``, so payload serialization stays off the SQLite lock on
    # the hot record path. That is why ``_insert_trace_row`` takes ``payload``
    # as a parameter instead of deriving it: a helper that called
    # ``to_dict()`` itself would pull this work under the lock, and nothing
    # else in the suite would notice (it is a latency regression, not a
    # correctness one). The import path deliberately serializes inside the
    # lock and is covered by the replace-path test below.
    db_path = tmp_path / "commands.sqlite"
    store = server.CommandTraceStore(sqlite_path=db_path)
    observed = []
    real_to_dict = server.CommandTrace.to_dict

    def observing_to_dict(self):
        observed.append(store._sqlite_lock.locked())
        return real_to_dict(self)

    monkeypatch.setattr(server.CommandTrace, "to_dict", observing_to_dict)

    store.record(_trace(1, "2026-06-25T12:01:00Z", "kubectl get pods"))

    assert observed
    assert not any(observed)


def test_command_trace_sqlite_import_serializes_replace_with_sqlite_lock(tmp_path, monkeypatch):
    db_path = tmp_path / "commands.sqlite"
    store = server.CommandTraceStore(sqlite_path=db_path)
    observed = []

    def capture_retention(conn):
        observed.append(store._sqlite_lock.locked())

    monkeypatch.setattr(store, "_enforce_sqlite_retention", capture_retention)

    store.import_payload({
        "traces": [_trace(1, "2026-06-25T12:01:00Z", "kubectl get pods").to_dict()],
    })

    # Replace and the subsequent tail reload each enforce retention inside
    # their own locked section, so every observation sees the lock held.
    assert observed
    assert all(observed)


def test_command_trace_sqlite_use_after_close_raises_runtime_error(tmp_path):
    # ``_locked_conn`` re-reads ``self._conn`` inside ``_sqlite_lock`` and
    # raises a clear RuntimeError when persistence has been torn down. After
    # ``close()`` a sqlite-backed operation must degrade to that RuntimeError,
    # never an AttributeError from ``None.execute`` (the race the review flagged
    # when the None-check sat outside the lock).
    db_path = tmp_path / "commands.sqlite"
    store = server.CommandTraceStore(sqlite_path=db_path)
    store.close()

    with pytest.raises(RuntimeError, match="sqlite persistence is not configured"):
        store.record(_trace(1, "2026-06-25T12:01:00Z", "kubectl get pods"))


# ``_insert_sqlite`` and ``_replace_sqlite_traces`` share one row writer,
# ``_insert_trace_row`` (audit A-031). Every read path rebuilds traces from
# ``payload_json`` alone, so a reload-and-compare test cannot see drift in the
# other 20 columns -- these read the raw rows instead.
_TRACE_COLUMNS = (
    "id",
    "received_at_wall_time",
    "simulated_time",
    "raw_input",
    "command_family",
    "verb",
    "resource_kind",
    "resource_name",
    "namespace",
    "support_status",
    "matched_rule_id",
    "fingerprint",
    "guessed_intent",
    "active_scenarios_json",
    "exit_code",
    "stdout_preview",
    "stderr_preview",
    "stdout",
    "stderr",
    "latency_ms",
    "payload_json",
)


def _raw_trace_row(db_path, trace_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM command_traces WHERE id = ?", (trace_id,)
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else {key: row[key] for key in row.keys()}


def _expected_trace_columns(trace):
    return {
        "id": trace.id,
        "received_at_wall_time": trace.received_at_wall_time,
        "simulated_time": trace.simulated_time,
        "raw_input": trace.raw_input,
        "command_family": trace.command_family,
        "verb": trace.verb,
        "resource_kind": trace.resource_kind,
        "resource_name": trace.resource_name,
        "namespace": trace.namespace,
        "support_status": trace.support_status,
        "matched_rule_id": trace.matched_rule_id,
        "fingerprint": trace.fingerprint,
        "guessed_intent": trace.guessed_intent,
        "active_scenarios_json": json.dumps(
            list(trace.active_scenarios), sort_keys=True
        ),
        "exit_code": trace.exit_code,
        "stdout_preview": trace.stdout_preview,
        "stderr_preview": trace.stderr_preview,
        "stdout": trace.stdout,
        "stderr": trace.stderr,
        "latency_ms": trace.latency_ms,
        "payload_json": json.dumps(trace.to_dict(), sort_keys=True),
    }


def _fts_rows(db_path, trace_id=None):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sql = (
        "SELECT trace_id, raw_input, stdout, stderr, fingerprint, "
        "guessed_intent, matched_rule_id FROM command_traces_fts"
    )
    params = ()
    if trace_id is not None:
        sql += " WHERE trace_id = ?"
        params = (trace_id,)
    try:
        rows = conn.execute(sql + " ORDER BY trace_id ASC", params).fetchall()
    finally:
        conn.close()
    return [{key: row[key] for key in row.keys()} for row in rows]


@pytest.mark.parametrize("write_path", ["record", "import"])
def test_command_trace_sqlite_writes_every_column_on_both_paths(tmp_path, write_path):
    # Guards the shared ``_insert_trace_row``: the live-insert and
    # bundle-import paths must persist all 21 columns identically. Asserted on
    # the raw row because ``list``/``get``/``search`` only deserialize
    # ``payload_json`` and would pass even if the rest were dropped.
    db_path = tmp_path / f"commands-{write_path}.sqlite"
    store = server.CommandTraceStore(sqlite_path=db_path)
    trace = _trace(
        7,
        "2026-06-25T12:07:00Z",
        "kubectl logs cacheservice-0 --tail 5",
        active_scenarios=("zeta_scenario", "alpha_scenario"),
    )

    if write_path == "record":
        store.record(trace)
    else:
        store.import_payload({"traces": [trace.to_dict()]})
    store.close()

    row = _raw_trace_row(db_path, 7)
    assert row is not None
    assert set(row) == set(_TRACE_COLUMNS)
    assert row == _expected_trace_columns(trace)


@pytest.mark.parametrize("write_path", ["record", "import"])
def test_command_trace_sqlite_writes_fts_rows_on_both_paths(tmp_path, write_path):
    # Asserted against ``command_traces_fts`` directly: ``search()`` silently
    # falls back to a LIKE scan over ``command_traces`` when FTS5 is
    # unavailable, so a passing search would not prove the FTS write happened.
    db_path = tmp_path / f"fts-{write_path}.sqlite"
    store = server.CommandTraceStore(sqlite_path=db_path)
    if not store._sqlite_fts_enabled:
        store.close()
        pytest.skip("sqlite build has no FTS5 support")

    traces = [
        _trace(1, "2026-06-25T12:01:00Z", "kubectl get pods"),
        _trace(2, "2026-06-25T12:02:00Z", "kubectl get services"),
    ]
    if write_path == "record":
        for trace in traces:
            store.record(trace)
    else:
        store.import_payload({"traces": [trace.to_dict() for trace in traces]})
    store.close()

    rows = _fts_rows(db_path)
    assert [row["trace_id"] for row in rows] == [1, 2]
    assert rows[1] == {
        "trace_id": 2,
        "raw_input": "kubectl get services",
        "stdout": "",
        "stderr": "",
        "fingerprint": "kubectl.get.pods",
        "guessed_intent": "Inspect pods",
        "matched_rule_id": "kubectl.get.pods",
    }


def test_command_trace_sqlite_record_replaces_rather_than_duplicates_fts_row(tmp_path):
    # ``_insert_sqlite`` passes ``delete_fts_first=True`` because it can
    # overwrite an existing id. Without that delete the FTS mirror accumulates
    # a stale second row and search returns the superseded text.
    db_path = tmp_path / "commands.sqlite"
    store = server.CommandTraceStore(sqlite_path=db_path)
    if not store._sqlite_fts_enabled:
        store.close()
        pytest.skip("sqlite build has no FTS5 support")

    store.record(_trace(3, "2026-06-25T12:03:00Z", "kubectl get pods"))
    store.record(_trace(3, "2026-06-25T12:09:00Z", "kubectl get configmaps"))
    store.close()

    rows = _fts_rows(db_path, trace_id=3)
    assert len(rows) == 1
    assert rows[0]["raw_input"] == "kubectl get configmaps"


def test_command_trace_sqlite_import_clears_superseded_fts_rows(tmp_path):
    # ``_replace_sqlite_traces`` passes ``delete_fts_first=False`` -- correct
    # only because it bulk-clears ``command_traces_fts`` before the loop. If
    # that clear were dropped, the pre-import trace would linger here.
    db_path = tmp_path / "commands.sqlite"
    store = server.CommandTraceStore(sqlite_path=db_path)
    if not store._sqlite_fts_enabled:
        store.close()
        pytest.skip("sqlite build has no FTS5 support")

    store.record(_trace(99, "2026-06-25T11:00:00Z", "kubectl get nodes"))
    replacement = _trace(1, "2026-06-25T12:01:00Z", "kubectl get pods")
    store.import_payload({"traces": [replacement.to_dict()]})
    store.close()

    assert [row["trace_id"] for row in _fts_rows(db_path)] == [1]


def test_command_trace_sqlite_per_row_fts_delete_cannot_reach_absent_traces(
    tmp_path,
):
    # The test above says the bulk clear is required; this one says *why*, so
    # the claim is pinned by the suite rather than by a hand-run mutation.
    # ``delete_fts_first=True`` only drops the FTS row for the trace being
    # written, so it can never evict a trace that the replacement set omits.
    # That is what makes the bulk clear load-bearing on its own, independent
    # of how ``delete_fts_first`` is derived.
    db_path = tmp_path / "commands.sqlite"
    store = server.CommandTraceStore(sqlite_path=db_path)
    if not store._sqlite_fts_enabled:
        store.close()
        pytest.skip("sqlite build has no FTS5 support")

    store.record(_trace(99, "2026-06-25T11:00:00Z", "kubectl get nodes"))
    superseding = _trace(1, "2026-06-25T12:01:00Z", "kubectl get pods")

    # Drive the row writer directly with the import path's per-row settings but
    # without any bulk clear -- i.e. exactly the state a dropped clear leaves.
    with store._locked_conn() as conn:
        store._insert_trace_row(
            conn, superseding, superseding.to_dict(), delete_fts_first=True
        )
    store.close()

    # Trace 99 survives: no per-row delete was ever issued for its id.
    assert [row["trace_id"] for row in _fts_rows(db_path)] == [1, 99]


def _summary_trace(tid, ts, *, fingerprint, support_status, guessed_intent):
    return server.CommandTrace(
        id=tid,
        received_at_wall_time=ts,
        simulated_time=ts,
        raw_input=f"kubectl weird {fingerprint} {tid}",
        argv=("kubectl", "weird", fingerprint, str(tid)),
        client="debug-ui",
        command_family="kubectl",
        verb="weird",
        resource_kind="",
        resource_name="",
        namespace="saas-prod",
        parsed_flags={"n": tid},
        support_status=support_status,
        matched_rule_id="",
        active_scenarios=("cache_leak_restart",),
        exit_code=1,
        stdout_preview="",
        stderr_preview="",
        stdout="out",
        stderr="err",
        latency_ms=1.0,
        fingerprint=fingerprint,
        guessed_intent=guessed_intent,
    )


_SUMMARY_FIXTURE = [
    ("fp.a", "unsupported", "intent-a1"),
    ("fp.b", "partial", "intent-b1"),
    ("fp.a", "partial", "intent-a2"),
    ("fp.a", "unsupported", "intent-a3"),
    ("fp.c", "supported", "intent-c"),   # excluded from the summary
    ("fp.b", "unsupported", "intent-b2"),
]


def _build_summary_traces():
    return [
        _summary_trace(
            index + 1,
            f"2026-06-25T12:0{index + 1}:00Z",
            fingerprint=fp,
            support_status=status,
            guessed_intent=intent,
        )
        for index, (fp, status, intent) in enumerate(_SUMMARY_FIXTURE)
    ]


def _reference_summary(traces, *, descending):
    from anomaly_metric_creator import server_traces

    unsupported = [t for t in traces if t.support_status != "supported"]
    # Backends feed the canonical grouping in different orders: the sqlite
    # store reads rows ORDER BY id DESC, while the in-memory ring iterates
    # insertion order (id ASC). Match whichever the backend under test uses
    # so example ordering / guessed_intent selection line up exactly.
    unsupported.sort(key=lambda t: t.id, reverse=descending)
    return server_traces._unsupported_summary_from_traces(unsupported)


def test_unsupported_summary_sqlite_matches_reference_grouping(tmp_path):
    # A-040 byte-identity oracle: the sqlite-backed unsupported_summary must
    # produce exactly what the canonical Python grouping produces over the
    # same non-supported traces (same order, examples, counters).
    db_path = tmp_path / "commands.sqlite"
    store = server.CommandTraceStore(sqlite_path=db_path, limit=100)
    traces = _build_summary_traces()
    for trace in traces:
        store.record(trace)

    got = store.unsupported_summary()
    assert got == _reference_summary(traces, descending=True)
    # Memoized second call is still byte-identical.
    assert store.unsupported_summary() == got
    # The /v1/state count path agrees with the summary length.
    assert store.unsupported_fingerprint_count() == len(got)
    assert len(got) == 2  # fp.a, fp.b (fp.c is supported)
    store.close()


def test_unsupported_summary_memory_matches_reference_grouping(tmp_path):
    store = server.CommandTraceStore(limit=100)
    traces = _build_summary_traces()
    for trace in traces:
        store.record(trace)

    got = store.unsupported_summary()
    assert got == _reference_summary(traces, descending=False)
    assert store.unsupported_summary() == got
    assert store.unsupported_fingerprint_count() == len(got)


def test_unsupported_summary_cache_invalidates_on_new_record(tmp_path):
    db_path = tmp_path / "commands.sqlite"
    store = server.CommandTraceStore(sqlite_path=db_path, limit=100)
    for trace in _build_summary_traces():
        store.record(trace)
    first = store.unsupported_summary()
    assert store.unsupported_fingerprint_count() == len(first)

    store.record(_summary_trace(
        99, "2026-06-25T12:59:00Z",
        fingerprint="fp.d", support_status="unsupported", guessed_intent="d",
    ))
    second = store.unsupported_summary()
    assert len(second) == len(first) + 1
    assert store.unsupported_fingerprint_count() == len(second)
    assert {group["fingerprint"] for group in second} == {"fp.a", "fp.b", "fp.d"}
    store.close()


def test_command_trace_jsonl_appends_all_records_durably(tmp_path):
    # A-041: the long-lived append handle records every trace, in order,
    # flushed durable — a second process/store reading the file sees them.
    persist_path = tmp_path / "commands.jsonl"
    store = server.CommandTraceStore(persist_path=persist_path)
    for index in range(5):
        store.record(_trace(index + 1, f"2026-06-25T12:0{index}:00Z"))

    with persist_path.open(encoding="utf-8") as fh:
        ids = [json.loads(line)["id"] for line in fh if line.strip()]
    assert ids == [1, 2, 3, 4, 5]
    store.close()


def test_command_trace_sqlite_concurrent_record_and_read_is_consistent(tmp_path):
    # A-041: the single long-lived connection is shared across threads under
    # one lock; concurrent records + reads must not raise or corrupt state.
    db_path = tmp_path / "commands.sqlite"
    store = server.CommandTraceStore(sqlite_path=db_path, limit=500)
    errors = []
    total = 40

    def writer(start):
        try:
            for offset in range(total // 2):
                tid = start + offset
                store.record(_trace(
                    tid,
                    f"2026-06-25T13:{tid % 60:02d}:00Z",
                    f"kubectl get pods marker-{tid}",
                ))
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    def reader():
        try:
            for _ in range(50):
                store.count()
                store.unsupported_fingerprint_count()
                store.list_traces(limit=5)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(1,)),
        threading.Thread(target=writer, args=(1 + total // 2,)),
        threading.Thread(target=reader),
        threading.Thread(target=reader),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert store.count() == total

    # Reload from disk sees exactly the persisted rows.
    reloaded = server.CommandTraceStore(sqlite_path=db_path, limit=500)
    assert reloaded.count() == total
    store.close()
    reloaded.close()


def test_command_trace_sqlite_search_reports_backend_and_schema(amc, tmp_path):
    db_path = tmp_path / "commands.sqlite"
    state = _build_state(
        amc,
        tmp_path,
        scenarios="cache_leak_restart",
        days=3,
        persist_command_db=db_path,
    )

    server.run_command(state, command="kubectl auth can-i get pods -n saas-prod")

    search = state.traces.search(query="auth can-i")
    assert search["total"] == 1
    assert search["items"][0]["matched_rule_id"] == "kubectl.auth.can-i"
    assert search["search_backend"] in {"fts5", "like"}
    with sqlite3.connect(db_path) as conn:
        schema_version = conn.execute(
            "SELECT value FROM command_trace_meta WHERE key = 'schema_version'"
        ).fetchone()
    assert schema_version is not None
    assert int(schema_version[0]) >= 1


def test_command_trace_sqlite_scenario_filter_matches_exact_membership(tmp_path):
    db_path = tmp_path / "commands.sqlite"
    store = server.CommandTraceStore(sqlite_path=db_path)
    store.record(_trace(
        1,
        "2026-06-25T12:01:00Z",
        "kubectl get pods -n saas-prod",
        active_scenarios=("cache",),
    ))
    store.record(_trace(
        2,
        "2026-06-25T12:02:00Z",
        "kubectl get services -n saas-prod",
        active_scenarios=("cache_leak_restart",),
    ))

    search = store.search(scenario_id="cache")
    fallback = store._search_sqlite_like_fallback(
        query="kubectl get",
        support_status="",
        command_family="",
        scenario_id="cache",
        limit=10,
        offset=0,
    )

    assert search["total"] == 1
    assert [item["id"] for item in search["items"]] == [1]
    assert fallback["total"] == 1
    assert [item["id"] for item in fallback["items"]] == [1]


def test_command_trace_sqlite_restart_searches_beyond_ring_size(amc, tmp_path):
    db_path = tmp_path / "commands.sqlite"
    state = _build_state(
        amc,
        tmp_path,
        scenarios="cache_leak_restart",
        days=3,
        persist_command_db=db_path,
        trace_limit=2,
    )

    commands = [
        "kubectl diagnose old-ring-marker -n saas-prod",
        "kubectl auth can-i get pods -n saas-prod",
        "kubectl get services -n saas-prod",
        "helm status simulated-saas -n saas-prod",
        "kubectl debug pod/cacheservice-0 -n saas-prod",
    ]
    for command in commands:
        server.run_command(state, command=command)

    restored = _build_state(
        amc,
        tmp_path,
        scenarios="cache_leak_restart",
        days=3,
        persist_command_db=db_path,
        trace_limit=2,
    )

    assert restored.traces.count() == len(commands)
    assert restored.traces.search(query="auth can-i")["total"] == 1
    assert restored.traces.search(query="old-ring-marker")["total"] == 1
    recent = restored.traces.list_traces(limit=10)
    assert [item["raw_input"] for item in recent] == list(reversed(commands))


def test_command_trace_sqlite_retention_limits_persisted_history(amc, tmp_path):
    db_path = tmp_path / "commands.sqlite"
    state = _build_state(
        amc,
        tmp_path,
        scenarios="cache_leak_restart",
        days=3,
        persist_command_db=db_path,
        persist_command_retention=3,
        trace_limit=2,
    )

    commands = [
        "kubectl diagnose old-retention-marker -n saas-prod",
        "kubectl get services -n saas-prod",
        "kubectl get deployments -n saas-prod",
        "helm status simulated-saas -n saas-prod",
        "kubectl debug pod/cacheservice-0 -n saas-prod",
    ]
    for command in commands:
        server.run_command(state, command=command)

    assert state.traces.count() == 3
    assert state.traces.get(1) is None
    assert state.traces.search(query="old-retention-marker")["total"] == 0
    assert [item["raw_input"] for item in state.traces.list_traces(limit=10)] == list(reversed(commands[-3:]))

    restored = _build_state(
        amc,
        tmp_path,
        scenarios="cache_leak_restart",
        days=3,
        persist_command_db=db_path,
        persist_command_retention=3,
        trace_limit=2,
    )
    assert restored.traces.count() == 3
    assert [item["raw_input"] for item in restored.traces.list_traces(limit=10)] == list(reversed(commands[-3:]))


def test_command_trace_sqlite_get_respects_retention_below_ring_limit(tmp_path):
    db_path = tmp_path / "commands.sqlite"
    store = server.CommandTraceStore(
        sqlite_path=db_path,
        sqlite_retention=1,
        limit=10,
    )
    store.record(_trace(1, "2026-06-25T12:01:00Z", "kubectl get pods"))
    store.record(_trace(2, "2026-06-25T12:02:00Z", "kubectl get services"))

    assert store.count() == 1
    assert store.list_traces() == [{"version": store.version, **_trace(
        2,
        "2026-06-25T12:02:00Z",
        "kubectl get services",
    ).to_dict()}]
    assert store.get(1) is None
    assert store.get(2)["raw_input"] == "kubectl get services"


def test_command_trace_export_import_round_trips_sqlite_history(amc, tmp_path):
    source_db = tmp_path / "source.sqlite"
    target_db = tmp_path / "target.sqlite"
    source = _build_state(
        amc,
        tmp_path,
        scenarios="cache_leak_restart",
        days=3,
        persist_command_db=source_db,
        trace_limit=1,
    )
    server.run_command(source, command="kubectl get pods -n saas-prod")
    server.run_command(source, command="kubectl auth can-i get pods -n saas-prod")
    with _running_test_server(source) as base_url:
        export_payload = _get_json(base_url + "/v1/debug/commands/export")

    target = _build_state(
        amc,
        tmp_path,
        scenarios="cache_leak_restart",
        days=3,
        persist_command_db=target_db,
        trace_limit=1,
    )
    with _running_test_server(target) as base_url:
        request = urllib.request.Request(
            base_url + "/v1/debug/commands/import",
            data=json.dumps(export_payload).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            imported = json.loads(response.read().decode("utf-8"))

    assert imported["imported"] == 2
    assert target.traces.count() == 2
    assert target.traces.search(query="auth can-i")["total"] == 1
    assert target.traces.list_traces(limit=10)[0]["raw_input"] == "kubectl auth can-i get pods -n saas-prod"


def test_debug_http_api_records_commands(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    with _running_test_server(state) as base_url:
        request = urllib.request.Request(
            base_url + "/v1/commands",
            data=json.dumps({"command": "kubectl get pods -n saas-prod"}).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
        assert body["result"]["exit_code"] == 0
        assert "cacheservice-0" in body["result"]["stdout"]

        with urllib.request.urlopen(base_url + "/v1/debug/commands", timeout=5) as response:
            commands = json.loads(response.read().decode("utf-8"))
        assert commands["items"][0]["raw_input"] == "kubectl get pods -n saas-prod"

        with urllib.request.urlopen(
            base_url + f"/v1/debug/commands/{commands['items'][0]['id']}",
            timeout=5,
        ) as response:
            command_detail = json.loads(response.read().decode("utf-8"))
        assert command_detail["raw_input"] == "kubectl get pods -n saas-prod"

        for path, expected_status in (
            ("/v1/debug/commands/not-an-int", 400),
            ("/v1/debug/commands/999999", 404),
        ):
            with pytest.raises(urllib.error.HTTPError) as excinfo:
                urllib.request.urlopen(base_url + path, timeout=5)
            assert excinfo.value.code == expected_status

        search_url = base_url + "/v1/debug/search?q=cacheservice&status=supported"
        with urllib.request.urlopen(search_url, timeout=5) as response:
            search = json.loads(response.read().decode("utf-8"))
        assert search["total"] == 1
        assert search["items"][0]["raw_input"] == "kubectl get pods -n saas-prod"

        scenarios = _get_json(base_url + "/v1/scenarios")
        cache_scenario = next(item for item in scenarios["known"] if item["id"] == "cache_leak_restart")
        assert cache_scenario["active"] is True
        assert cache_scenario["category"] == "multi_day_cascade"
        assert cache_scenario["ops_profile"] is True
        assert cache_scenario["ops_profile_detail"]["summary"]
        assert cache_scenario["primary_specs"][0]["time_offset_seconds"] == 24 * 60 * 60
        assert any(
            "Cache memory leak" in spec["description"]
            for spec in cache_scenario["primary_specs"]
        )
        assert any(
            "Cache restart causes brief gateway errors" in spec["description"]
            for spec in cache_scenario["cascade_specs"]
        )

        with urllib.request.urlopen(base_url + "/debug", timeout=5) as response:
            html = response.read().decode("utf-8")
        assert "AMC Debug Console" in html
        assert "Search" in html
        assert "Unsupported Explorer" in html
        assert "Scenario Catalog" in html
        assert "Runtime" in html
        assert "Mutable State" in html


def test_server_auth_token_protects_debug_api_and_embeds_kubeconfig(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    security = server.ServerSecurityConfig(auth_token="test-token")
    with _running_test_server(state, security=security) as base_url:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(base_url + "/v1/state", timeout=5)
        assert excinfo.value.code == 401
        assert excinfo.value.headers["www-authenticate"] == "Bearer"

        with urllib.request.urlopen(base_url + "/healthz", timeout=5) as response:
            assert response.read().decode("utf-8") == "ok\n"
            assert response.headers["x-content-type-options"] == "nosniff"

        for shell_path in ("/", "/debug"):
            with urllib.request.urlopen(base_url + shell_path, timeout=5) as response:
                html = response.read().decode("utf-8")
            assert "AMC Debug Console" in html
            assert "amc.debug.authToken" in html
            assert "authorization: `Bearer ${token}`" in html

        headers = {"authorization": "Bearer test-token"}
        request = urllib.request.Request(base_url + "/v1/kubeconfig", headers=headers)
        with urllib.request.urlopen(request, timeout=5) as response:
            kubeconfig = response.read().decode("utf-8")
        assert f"server: {base_url}" in kubeconfig
        assert 'token: "test-token"' in kubeconfig

        command_request = urllib.request.Request(
            base_url + "/v1/commands",
            data=json.dumps({"command": "kubectl get pods -n saas-prod"}).encode("utf-8"),
            headers={**headers, "content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(command_request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
        assert body["result"]["support_status"] == "supported"

        version = _get_json_with_headers(base_url + "/version", headers)
        assert version["gitVersion"] == "v1.36.2-amc"


def test_request_body_limit_and_mutating_k8s_operations_are_traced(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    security = server.ServerSecurityConfig(max_body_bytes=16)
    with _running_test_server(state, security=security) as base_url:
        too_large = urllib.request.Request(
            base_url + "/v1/commands",
            data=json.dumps({"command": "kubectl get pods -n saas-prod"}).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(too_large, timeout=5)
        assert excinfo.value.code == 413

        delete = urllib.request.Request(
            base_url + "/api/v1/namespaces/saas-prod/pods/cacheservice-0",
            method="DELETE",
        )
        with urllib.request.urlopen(delete, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["kind"] == "Status"
        assert payload["reason"] == "Deleted"
        assert "deleted" in payload["message"]

        query = urllib.parse.urlencode({"family": "kubernetes-api", "q": "DELETE"})
        search = _get_json(base_url + "/v1/debug/search?" + query)
        assert search["total"] == 1
        assert search["items"][0]["support_status"] == "supported"
        assert search["items"][0]["matched_rule_id"] == "k8s.core.pods.delete"

        reset_request = urllib.request.Request(
            base_url + "/v1/mutations/reset",
            data=b"{}",
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(reset_request, timeout=5) as response:
            reset = json.loads(response.read().decode("utf-8"))
        assert reset["mutations"]["version"] > 0
        assert reset["mutations"]["deleted_pods"] == []


def test_server_cors_preflight_is_explicit_and_unauthenticated(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    security = server.ServerSecurityConfig(
        auth_token="test-token",
        cors_allow_origin="https://ops.example",
    )
    with _running_test_server(state, security=security) as base_url:
        preflight = urllib.request.Request(
            base_url + "/v1/commands",
            headers={
                "origin": "https://ops.example",
                "access-control-request-method": "POST",
                "access-control-request-headers": "authorization, content-type",
            },
            method="OPTIONS",
        )
        with urllib.request.urlopen(preflight, timeout=5) as response:
            assert response.status == 204
            assert response.headers["access-control-allow-origin"] == "https://ops.example"
            assert "POST" in response.headers["access-control-allow-methods"]
            assert "authorization" in response.headers["access-control-allow-headers"]
            assert response.read() == b""

        healthz = urllib.request.Request(
            base_url + "/healthz",
            headers={"origin": "https://ops.example"},
        )
        with urllib.request.urlopen(healthz, timeout=5) as response:
            assert response.headers["access-control-allow-origin"] == "https://ops.example"


def test_server_rate_limits_command_and_kubernetes_api_endpoints(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    security = server.ServerSecurityConfig(rate_limit_per_minute=1)
    with _running_test_server(state, security=security) as base_url:
        first_command = urllib.request.Request(
            base_url + "/v1/commands",
            data=json.dumps({"command": "kubectl get pods -n saas-prod"}).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(first_command, timeout=5) as response:
            assert response.status == 200

        second_command = urllib.request.Request(
            base_url + "/v1/commands",
            data=json.dumps({"command": "kubectl get svc -n saas-prod"}).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(second_command, timeout=5)
        assert excinfo.value.code == 429
        assert int(excinfo.value.headers["retry-after"]) > 0
        command_error = json.loads(excinfo.value.read().decode("utf-8"))
        assert command_error["error"] == "rate limit exceeded"

        with urllib.request.urlopen(base_url + "/version", timeout=5) as response:
            assert response.status == 200
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(base_url + "/version", timeout=5)
        assert excinfo.value.code == 429
        assert int(excinfo.value.headers["retry-after"]) > 0
        status = json.loads(excinfo.value.read().decode("utf-8"))
        assert status["kind"] == "Status"
        assert status["reason"] == "TooManyRequests"


def test_serve_config_file_supplies_server_and_generation_defaults(tmp_path):
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({
            "server": {
                "host": "127.0.0.1",
                "port": 0,
                "namespace": "configured-ns",
                "debug_ring_size": 7,
                "persist_command_retention": 12,
                "max_request_body_bytes": 2048,
                "cors_allow_origin": "https://ops.example",
                "rate_limit_per_minute": 9,
                "no_generate": True,
                "structured_log": True,
                "structured_log_file": str(tmp_path / "requests.jsonl"),
                "continuous_generate_interval_seconds": 3.5,
            },
            "generate": {
                "duration_days": 2,
                "scenarios": "cache_leak_restart",
                "components": "apigateway,cacheservice,database",
                "output_dir": str(tmp_path / "configured-output"),
                "otel_send": "none",
            },
        }),
        encoding="utf-8",
    )

    parser = server._build_serve_parser()
    serve_args, generate_argv = server._parse_serve_args(
        ["--config", str(config_path)],
        parser,
    )

    assert serve_args.namespace == "configured-ns"
    assert serve_args.debug_ring_size == 7
    assert serve_args.persist_command_retention == 12
    assert serve_args.max_request_body_bytes == 2048
    assert serve_args.cors_allow_origin == "https://ops.example"
    assert serve_args.rate_limit_per_minute == 9
    assert serve_args.no_generate is True
    assert serve_args.structured_log is True
    assert serve_args.structured_log_file == tmp_path / "requests.jsonl"
    assert serve_args.continuous_generate_interval_seconds == 3.5
    # `--flag=value`, so a value starting with `-` cannot be read as an option.
    assert "--duration-days=2" in generate_argv
    assert f"--output-dir={tmp_path / 'configured-output'}" in generate_argv


def test_serve_cli_flags_override_config_file_values(tmp_path):
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({
            "server": {
                "namespace": "configured-ns",
                "debug_ring_size": 7,
                "structured_log": False,
                "no_generate": True,
            },
            "generate": {
                "duration_days": 2,
                "scenarios": "cache_leak_restart",
                "output_dir": str(tmp_path / "configured-output"),
            },
        }),
        encoding="utf-8",
    )

    parser = server._build_serve_parser()
    serve_args, generate_argv = server._parse_serve_args(
        [
            "--config", str(config_path),
            "--namespace", "cli-ns",
            "--debug-ring-size", "11",
            "--structured-log",
            "--duration-days", "3",
            "--scenarios", "db_disk_exhaustion",
        ],
        parser,
    )

    assert serve_args.namespace == "cli-ns"
    assert serve_args.debug_ring_size == 11
    assert serve_args.structured_log is True
    duration_index = len(generate_argv) - 1 - generate_argv[::-1].index("--duration-days")
    scenario_index = len(generate_argv) - 1 - generate_argv[::-1].index("--scenarios")
    assert generate_argv[duration_index + 1] == "3"
    assert generate_argv[scenario_index + 1] == "db_disk_exhaustion"


# --------------------------------------------------------------------------
# --config generate-key validation (07-02-config-generate-key-validation)
#
# The generate surface has no introspectable allowlist, so the real parser is
# the allowlist: a probe parse of the config-derived argv runs at load time and
# argparse's exit becomes a ValueError naming the config file. These tests pin
# both halves -- that a bad key is rejected with attribution, and that the
# probe does not reject keys the parser really accepts.
# --------------------------------------------------------------------------

# Spot-covers common and advanced generate flags. Asserted non-empty below so
# the parametrized test cannot go vacuously green if this list is ever emptied.
_VALID_GENERATE_CONFIG_KEYS = {
    "duration_days": 2,
    "scenarios": "cache_leak_restart",
    "components": "apigateway,cacheservice",
    "otel_send": "none",
    "seed": 7,
    "interval_seconds": 60.0,
    "anomaly_count": 3,
    "metrics_per_component": 4,
    "instances_per_component": 2,
    "emit": "metrics",
    "drop_rate": 0.0,
}


def test_valid_generate_config_key_sample_is_not_empty():
    """Guard: the parametrized valid-key test must not go vacuously green."""
    assert _VALID_GENERATE_CONFIG_KEYS


@pytest.mark.parametrize("key", sorted(_VALID_GENERATE_CONFIG_KEYS))
def test_valid_generate_config_keys_survive_the_probe(key, tmp_path):
    """Every sampled real generate flag must load without error."""
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({
            "generate": {
                key: _VALID_GENERATE_CONFIG_KEYS[key],
                "output_dir": str(tmp_path / "out"),
            }
        }),
        encoding="utf-8",
    )
    parser = server._build_serve_parser()
    serve_args, generate_argv = server._parse_serve_args(
        ["--config", str(config_path)], parser
    )
    assert serve_args.config == config_path
    assert any(
        item.startswith("--" + key.replace("_", "-") + "=") for item in generate_argv
    )


def test_unknown_generate_config_key_is_rejected_naming_the_file(tmp_path, capsys):
    """A typo'd generate key fails at load, not deep in a later parse."""
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({"generate": {"componentss": "apigateway"}}),
        encoding="utf-8",
    )
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit) as excinfo:
        server._parse_serve_args(["--config", str(config_path)], parser)
    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert str(config_path) in stderr
    assert "componentss" in stderr


def test_unknown_generate_config_key_raises_value_error_at_the_unit_seam(tmp_path):
    """The probe itself raises ValueError; _parse_serve_args converts it."""
    config_path = tmp_path / "serve-config.yaml"
    with pytest.raises(ValueError) as excinfo:
        server._probe_config_generate_argv(
            ["--componentss", "apigateway"],
            config_path,
            server._resolve_generate_parse_args(),
        )
    message = str(excinfo.value)
    assert str(config_path) in message
    assert "componentss" in message


@pytest.mark.parametrize("value", [None, False])
@pytest.mark.parametrize("key", ["componentss", "components"])
def test_unvouchable_no_flag_generate_keys_are_loud_not_silently_dropped(
    key, value, tmp_path
):
    """A no-flag value is refused unless the real parser vouches for the key.

    `null` and `false` emit nothing, so the argv probe never sees them -- the
    PRD's "collides with nothing" case. Two kinds fail to vouch: a typo
    (`componentss`), and a real key that takes a value (`components`), for
    which neither shape means anything.
    """
    config_path = tmp_path / "serve-config.json"
    with pytest.raises(ValueError) as excinfo:
        server._vouch_no_flag_generate_keys(
            {key: value}, config_path, server._resolve_generate_parse_args()
        )
    message = str(excinfo.value)
    assert str(config_path) in message
    assert key in message
    assert ("null" if value is None else "false") in message


@pytest.mark.parametrize("value", [None, False])
@pytest.mark.parametrize("key", ["otel_verbose", "allow_huge_output"])
def test_a_real_switch_may_still_be_turned_off_by_a_no_flag_value(key, value, tmp_path):
    """Refusing unvouchable keys must not regress a config that works today.

    Both generate switches parse on their own, so the parser vouches for them
    and the key keeps its documented meaning of "use the default".
    """
    server._vouch_no_flag_generate_keys(
        {key: value}, tmp_path / "c.json", server._resolve_generate_parse_args()
    )
    assert server._config_mapping_to_argv({key: value}) == []


def test_false_generate_config_typo_is_rejected_end_to_end(tmp_path, capsys):
    """The refusal reaches the CLI, not just the unit seam."""
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({"generate": {"componentss": False}}), encoding="utf-8"
    )
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit) as excinfo:
        server._parse_serve_args(["--config", str(config_path)], parser)
    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert str(config_path) in stderr
    assert "componentss" in stderr


def test_a_vouched_switch_loads_end_to_end(tmp_path):
    """`otel_verbose: false` still loads, producing no flag."""
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({"generate": {"otel_verbose": False}}), encoding="utf-8"
    )
    parser = server._build_serve_parser()
    serve_args, generate_argv = server._parse_serve_args(
        ["--config", str(config_path)], parser
    )
    assert generate_argv == []
    assert serve_args.config == config_path


def test_a_negated_generate_flag_is_still_reachable_from_config(tmp_path):
    """`--otel-verbose` is the one BooleanOptionalAction on the generate surface."""
    argv = server._config_mapping_to_argv({"no_otel_verbose": True})
    assert argv == ["--no-otel-verbose"]
    server._probe_config_generate_argv(
        argv, tmp_path / "c.json", server._resolve_generate_parse_args()
    )


def test_empty_list_generate_config_value_still_reaches_the_probe(tmp_path):
    """An empty list produces a flag, so it is checked -- not a no-flag shape."""
    argv = server._config_mapping_to_argv({"componentss": []})
    assert argv == ["--componentss="]
    with pytest.raises(ValueError):
        server._probe_config_generate_argv(
            argv, tmp_path / "c.json", server._resolve_generate_parse_args()
        )


def test_an_abbreviated_generate_key_is_refused_like_the_real_parse(tmp_path):
    """Prefix matching is off, so the probe and the later real parse agree."""
    parse_args = server._resolve_generate_parse_args()
    with pytest.raises(ValueError):
        server._probe_config_generate_argv(
            ["--comp", "apigateway"], tmp_path / "c.json", parse_args
        )
    with pytest.raises(SystemExit):
        parse_args(["--comp", "apigateway"])


def test_a_successful_parser_exit_is_not_reported_as_a_rejection(tmp_path):
    """`help: true` exits 0; calling that "rejected" names the wrong problem."""
    with pytest.raises(ValueError) as excinfo:
        server._probe_config_generate_argv(
            ["--help"], tmp_path / "c.json", server._resolve_generate_parse_args()
        )
    message = str(excinfo.value)
    assert "rejected by the generate parser" not in message
    assert "exit" in message
    assert "help" in message


@pytest.mark.parametrize("section", ["server", "generate"])
def test_a_non_string_config_key_is_refused_not_crashed_on(section, tmp_path, capsys):
    """YAML admits non-string keys; they must not escape as an AttributeError.

    `1: apigateway` reaches `key.replace("_", "-")` unguarded and raises
    AttributeError, which escapes the ValueError refusal that names the file --
    the entire operator-facing contract. JSON cannot produce this shape, so
    only the YAML reader can.
    """
    pytest.importorskip("yaml")
    config_path = tmp_path / "serve-config.yaml"
    config_path.write_text(f"{section}:\n  1: apigateway\n", encoding="utf-8")
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit) as excinfo:
        server._parse_serve_args(["--config", str(config_path)], parser)
    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert str(config_path) in stderr
    assert "must be strings" in stderr


def test_a_non_string_top_level_config_key_is_refused(tmp_path, capsys):
    """The unknown-top-key report sorts its keys, so it must str() them first."""
    pytest.importorskip("yaml")
    config_path = tmp_path / "serve-config.yaml"
    config_path.write_text("1: x\nother: y\n", encoding="utf-8")
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit) as excinfo:
        server._parse_serve_args(["--config", str(config_path)], parser)
    assert excinfo.value.code == 2
    assert "top-level" in capsys.readouterr().err


@pytest.mark.parametrize("key", ["help", "version"])
def test_an_exit_zero_flag_is_not_called_unrecognized(key, tmp_path):
    """`--help` is recognized; refusing it as "not a switch" would be false."""
    with pytest.raises(ValueError) as excinfo:
        server._vouch_no_flag_generate_keys(
            {key: False}, tmp_path / "c.json", server._resolve_generate_parse_args()
        )
    message = str(excinfo.value)
    assert "is not a switch" not in message
    assert "exit" in message


def _capture_generate_parser(monkeypatch):
    """Return the real generate `ArgumentParser`, which `parse_args` builds inline.

    `_reconcile_cli_surface(p, args)` is handed the parser mid-parse, so spying
    on it yields the actual object rather than a reconstruction. Introspecting
    it beats matching the source text: `nargs = "?"`, a spelling with single
    quotes, or an option declared in some other module would all slip past a
    string search, and none slip past this.
    """
    from anomaly_metric_creator import cli_args

    parse_args = server._resolve_generate_parse_args()
    captured = []
    original = cli_args._reconcile_cli_surface

    def spy(parser, args):
        captured.append(parser)
        return original(parser, args)

    monkeypatch.setattr(cli_args, "_reconcile_cli_surface", spy)
    parse_args(["--otel-verbose"])
    assert captured, "the parser was never handed to _reconcile_cli_surface"
    return captured[0]


def test_no_generate_option_can_parse_without_its_value(monkeypatch):
    """The vouch reads "the bare flag parses" as "this key is a switch".

    That inference holds only while every value-taking generate option
    *requires* its value. `nargs="?"` and `nargs="*"` are the two shapes that
    break it: the bare flag would parse, so a `null` or `false` written for a
    value-taking key would be vouched and dropped in silence -- the exact hole
    the vouch exists to close. `nargs="+"` is safe (it still requires one).
    Neither dangerous shape is used today; this fails the moment one appears,
    so `_vouch_no_flag_generate_keys` gets revisited with it.
    """
    parser = _capture_generate_parser(monkeypatch)
    optional_value = [
        action.option_strings for action in parser._actions if action.nargs in ("?", "*")
    ]
    assert optional_value == []


def test_the_vouch_agrees_with_the_parser_about_every_option(monkeypatch):
    """The vouch's two categories must match the parser's own, for every option.

    Derived from the parser, not from a written switch list -- a list here
    would be the same hand-maintained allowlist the design refuses to keep in
    production, with the same drift. Every zero-argument action is vouched;
    every option that takes a value is refused. `--help`/`--version` are
    switches that exit rather than configure, so the vouch refuses them too,
    with their own diagnostic.
    """
    parser = _capture_generate_parser(monkeypatch)
    parse_args = server._resolve_generate_parse_args()
    exiting = {"--help", "-h", "--version"}
    checked = 0
    for action in parser._actions:
        for option in action.option_strings:
            if not option.startswith("--") or option in exiting:
                continue
            key = option[2:].replace("-", "_")
            checked += 1
            if action.nargs == 0:
                server._vouch_no_flag_generate_keys({key: None}, None, parse_args)
            else:
                with pytest.raises(ValueError):
                    server._vouch_no_flag_generate_keys({key: None}, None, parse_args)
    assert checked > 20, "the parser surface was not actually walked"


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[]",
        '{"nope": 1}',
        '{"server": 3}',
        '{"generate": 3}',
    ],
)
def test_every_config_load_refusal_names_the_file(body, tmp_path):
    """`_config_error` exists so no arm can drift back to a bare message."""
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        server._load_serve_config(config_path)
    assert str(excinfo.value).startswith(f"--config {config_path}: ")


def test_a_wrong_suffix_refusal_names_the_file(tmp_path):
    """The suffix arm refuses before reading, and still names the file."""
    config_path = tmp_path / "serve-config.txt"
    config_path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        server._load_serve_config(config_path)
    assert str(excinfo.value).startswith(f"--config {config_path}: ")


def test_the_config_cluster_is_patched_at_its_own_module(tmp_path, monkeypatch):
    """`server.<name>` is a re-import binding, not the definition.

    The cluster's functions call each other in `server_config`'s namespace, so
    patching the name on `server` rebinds only `server`'s copy and the real
    call is unaffected. This pins which module a stub must target, so the
    re-import block is not mistaken for an interception point.
    """
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(json.dumps({"server": {"port": 9001}}), encoding="utf-8")
    parser = server._build_serve_parser()

    def boom(*args, **kwargs):
        raise AssertionError("patched copy should not be reached")

    monkeypatch.setattr(server, "_load_serve_config", boom)
    serve_args, _ = server._parse_serve_args(["--config", str(config_path)], parser)
    assert serve_args.port == 9001

    monkeypatch.setattr(server_config, "_load_serve_config", boom)
    with pytest.raises(AssertionError):
        server._parse_serve_args(["--config", str(config_path)], parser)


def test_a_server_key_the_parser_cannot_consume_is_refused(
    tmp_path, monkeypatch, capsys
):
    """An allowlist that drifts from the parser must not fail silently.

    `parse_known_args` drops what it cannot consume, and the real parse must
    keep doing so -- generate flags travel in the same argv. So the probe
    checks the leftovers itself.
    """
    monkeypatch.setattr(
        server_config,
        "_SERVE_CONFIG_SERVER_KEYS",
        server_config._SERVE_CONFIG_SERVER_KEYS | {"not_a_serve_flag"},
    )
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({"server": {"not_a_serve_flag": "x"}}), encoding="utf-8"
    )
    with pytest.raises(SystemExit) as excinfo:
        server_config._parse_serve_args(
            ["--config", str(config_path)], server._build_serve_parser()
        )
    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert "drifted" in stderr
    assert "--not-a-serve-flag" in stderr


def test_the_generate_probe_does_not_reject_a_config_the_run_would_accept(tmp_path):
    """The probe must reject nothing that would have survived the real parse.

    Several generate gates are cross-flag: the preflight cell cap multiplies
    interval, duration, metric count, components, and instances. So
    `interval_seconds: 1` overflows it against the defaults but is fine once
    explicit CLI flags narrow the run. Judging the config section in isolation
    would reject a working configuration.
    """
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({"generate": {"interval_seconds": 1}}), encoding="utf-8"
    )
    parser = server._build_serve_parser()

    with pytest.raises(SystemExit):
        server._parse_serve_args(["--config", str(config_path)], parser)

    serve_args, generate_argv = server._parse_serve_args(
        [
            "--config",
            str(config_path),
            "--components",
            "apigateway",
            "--metrics-per-component",
            "1",
            "--duration-days",
            "1",
        ],
        parser,
    )
    assert "--interval-seconds=1" in generate_argv
    assert serve_args.config == config_path


def test_a_generate_typo_is_rejected_even_when_the_cli_narrows_the_run(tmp_path):
    """Confirming against the real argv must not weaken the typo check.

    An unknown flag fails both parses, so it is still refused -- only a config
    that the actual argv makes valid is let through.
    """
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({"generate": {"componentss": "apigateway"}}), encoding="utf-8"
    )
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit):
        server._parse_serve_args(
            ["--config", str(config_path), "--duration-days", "1"], parser
        )


def test_a_config_value_starting_with_a_dash_is_not_read_as_an_option(tmp_path):
    """`["--flag", "-x"]` makes argparse read `-x` as an option, not a value."""
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({"server": {"namespace": "-weird"}}), encoding="utf-8"
    )
    parser = server._build_serve_parser()
    serve_args, _ = server._parse_serve_args(["--config", str(config_path)], parser)
    assert serve_args.namespace == "-weird"


def test_config_argv_attaches_every_value_to_its_flag():
    """One token per key, so no value can be mistaken for the next option."""
    argv = server._config_mapping_to_argv(
        {"components": ["apigateway", "cacheservice"], "seed": 7, "otel_verbose": True}
    )
    assert argv == ["--components=apigateway,cacheservice", "--seed=7", "--otel-verbose"]


def test_a_drift_report_names_flags_without_their_values(tmp_path, monkeypatch, capsys):
    """`--auth-token` is an allowlisted server key; its value must not print."""
    monkeypatch.setattr(
        server_config,
        "_SERVE_CONFIG_SERVER_KEYS",
        server_config._SERVE_CONFIG_SERVER_KEYS | {"not_a_serve_flag"},
    )
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({"server": {"not_a_serve_flag": "s3cret-value"}}), encoding="utf-8"
    )
    with pytest.raises(SystemExit):
        server_config._parse_serve_args(
            ["--config", str(config_path)], server._build_serve_parser()
        )
    stderr = capsys.readouterr().err
    assert "--not-a-serve-flag" in stderr
    assert "s3cret-value" not in stderr


def test_a_config_derived_help_is_refused_before_the_serve_parser_sees_it(
    tmp_path, capsys
):
    """The serve parser owns `--help` too, and would exit 0 on serve usage.

    The combined parse runs before the generate probe, so the exit-zero check
    has to happen earlier or the config is never judged at all.
    """
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(json.dumps({"generate": {"help": True}}), encoding="utf-8")
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit) as excinfo:
        server._parse_serve_args(["--config", str(config_path)], parser)
    assert excinfo.value.code == 2
    assert "exit" in capsys.readouterr().err


def test_a_parser_diagnostic_does_not_echo_config_values(tmp_path, capsys):
    """The flag name identifies the mistake; the value it carried is not needed.

    A typo'd key is by definition on no sensitive-key list, so no config value
    can be assumed safe to print.
    """
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({"generate": {"otel_auth_tokenn": "s3cret"}}), encoding="utf-8"
    )
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit):
        server._parse_serve_args(["--config", str(config_path)], parser)
    stderr = capsys.readouterr().err
    assert "--otel-auth-tokenn" in stderr
    assert "s3cret" not in stderr


def test_a_failure_the_config_did_not_cause_is_not_blamed_on_it(tmp_path):
    """Both parses failing is not enough; they must fail for the same reason.

    Here the config section alone trips the cross-flag cell cap, and the merged
    argv fails on the user's own typo. Naming the config file would send the
    operator to the wrong place, so the real parse reports its own error later.
    """
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({"generate": {"interval_seconds": 1}}), encoding="utf-8"
    )
    parser = server._build_serve_parser()
    _, generate_argv = server._parse_serve_args(
        ["--config", str(config_path), "--componentss", "x"], parser
    )
    assert "--componentss" in generate_argv


def test_the_config_is_still_blamed_for_a_failure_that_is_its_own(tmp_path, capsys):
    """The quiet path must not swallow a config error the run really hits."""
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({"generate": {"componentss": "apigateway"}}), encoding="utf-8"
    )
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit):
        server._parse_serve_args(
            ["--config", str(config_path), "--duration-days", "1"], parser
        )
    stderr = capsys.readouterr().err
    assert str(config_path) in stderr
    assert "componentss" in stderr


def test_a_config_error_reports_flag_names_and_no_values():
    """The unit the whole no-leak posture rests on: names in, values dropped."""
    assert (
        server_config._config_flag_names(
            ["--otel-auth-token=s3cret", "--components=a,b", "--otel-verbose"]
        )
        == "--components, --otel-auth-token, --otel-verbose"
    )
    assert server_config._config_flag_names([]) == "(none)"


@pytest.mark.parametrize("section", ["server", "generate"])
def test_two_keys_naming_the_same_flag_are_refused(tmp_path, capsys, section):
    """`otel_verbose` and `otel-verbose` are distinct keys, one flag.

    Conversion emits it twice and argparse keeps the last, so one of the two
    settings vanishes -- the exact silent-drop this validation exists to
    remove. Both sections normalize the same way, so both are checked.
    """
    key = "otel_verbose" if section == "generate" else "auth_token"
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({section: {key: True, key.replace("_", "-"): True}}),
        encoding="utf-8",
    )
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit):
        server._parse_serve_args(["--config", str(config_path)], parser)
    stderr = capsys.readouterr().err
    assert str(config_path) in stderr
    assert f"all name --{key.replace('_', '-')}" in stderr


def test_a_yaml_error_reports_a_position_not_the_files_own_words(tmp_path, capsys):
    """PyYAML's `problem` interpolates the document; argparse's `msg` does not.

    `found undefined alias 's3cret'` is the parser's own field carrying the
    file's text, so YAML reports its error class and position instead. JSON's
    `msg` comes from a fixed vocabulary and is safe to keep, which is why the
    two arms differ.
    """
    pytest.importorskip("yaml")
    config_path = tmp_path / "serve-config.yaml"
    config_path.write_text("server:\n  port: *s3cret\n", encoding="utf-8")
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit):
        server._parse_serve_args(["--config", str(config_path)], parser)
    stderr = capsys.readouterr().err
    assert "failed to parse YAML: ComposerError at line 2" in stderr
    assert "s3cret" not in stderr


def test_a_yaml_constructor_error_still_names_the_file(tmp_path, capsys):
    """`!!int "abc"` raises a bare ValueError, which is not a YAMLError.

    It escaped the refusal entirely -- unattributed, and carrying the value.
    Anything the loader raises on an untrusted file belongs to that file.
    """
    pytest.importorskip("yaml")
    config_path = tmp_path / "serve-config.yaml"
    config_path.write_text('server:\n  port: !!int "s3cret"\n', encoding="utf-8")
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit):
        server._parse_serve_args(["--config", str(config_path)], parser)
    stderr = capsys.readouterr().err
    assert str(config_path) in stderr
    assert "failed to parse YAML: ValueError" in stderr
    assert "s3cret" not in stderr


def test_a_serve_flag_arriving_bare_from_generate_is_attributed(tmp_path, capsys):
    """`host: true` in the generate section made the *combined* parse fail.

    `parse_known_args` sets aside a flag it does not recognize and errors only
    on one it owns, so an error there is this check's own case. Falling through
    left it to the combined parse -- which runs first -- where it surfaced as a
    bare `argument --host: expected one argument` naming no config file, and a
    value-taking serve flag arriving bare would have swallowed the operator's
    own next token on the way.
    """
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(json.dumps({"generate": {"host": True}}), encoding="utf-8")
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit):
        server._parse_serve_args(
            ["--config", str(config_path), "--namespace", "prod"], parser
        )
    stderr = capsys.readouterr().err
    assert str(config_path) in stderr
    assert "--host" in stderr
    assert "'server' section" in stderr


def test_a_generate_key_naming_a_serve_flag_is_refused(tmp_path, capsys):
    """`generate: {port: 9999}` silently set the *server's* port.

    The combined parse lets the serve parser take what it recognizes first, so
    the token never reaches generation and the generate probe sees a section
    that parses clean. Caught before the combined parse instead.
    """
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(json.dumps({"generate": {"port": 9999}}), encoding="utf-8")
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit) as excinfo:
        server._parse_serve_args(["--config", str(config_path)], parser)
    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert "--port" in stderr
    assert "'server' section" in stderr


def test_an_ordinary_generate_key_is_untouched_by_that_check(tmp_path):
    """Only `--help` is owned by both parsers, so nothing legitimate collides."""
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({"generate": {"components": "apigateway"}}), encoding="utf-8"
    )
    parser = server._build_serve_parser()
    _, generate_argv = server._parse_serve_args(["--config", str(config_path)], parser)
    assert generate_argv == ["--components=apigateway"]


@pytest.mark.parametrize(
    "name,body",
    [
        # Argparse quotes the value back with no flag attached.
        ("float", json.dumps({"generate": {"otel_stream_speedup": "s3cret"}})),
        # A value containing whitespace is not one argv token.
        ("spaced", json.dumps({"generate": {"componentss": "s3cret and more"}})),
        # A value containing a newline survives any per-line pass.
        ("multiline", json.dumps({"generate": {"componentss": "top\ns3cret"}})),
        # The serve parser owns --port and rejects it before generate sees it.
        ("serve-owned", json.dumps({"server": {"port": "s3cret"}})),
        # A malformed file: the parse error quotes the offending region.
        ("malformed", '{"server": {"auth_token": "s3cret"'),
    ],
)
def test_no_config_refusal_ever_prints_a_config_value(tmp_path, capsys, name, body):
    """The structural property that replaced masking.

    Masking was a pattern over the parser's message and leaked every time a new
    shape turned up -- an unattached value, whitespace, a newline, a file-level
    parse error quoting the source. Nothing derived from a config *value* is
    put in an error now, so each of these is closed by construction rather than
    by another case in a regex.
    """
    config_path = tmp_path / f"serve-config-{name}.json"
    config_path.write_text(body, encoding="utf-8")
    parser = server._build_serve_parser()
    with pytest.raises((SystemExit, ValueError)) as excinfo:
        server._parse_serve_args(["--config", str(config_path)], parser)
    reported = capsys.readouterr().err + str(excinfo.value)
    assert str(config_path) in reported
    assert "s3cret" not in reported


def test_a_bad_overridden_server_value_fails_the_combined_parse_anyway(tmp_path):
    """Refusing it early attributes the same failure; it does not create one.

    Argparse converts every occurrence, not just the winning one, so the run
    would fail on the config's value even though the CLI overrides it.
    """
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit):
        parser.parse_known_args(["--port=abc", "--port", "8080"])

    config_path = tmp_path / "serve-config.json"
    config_path.write_text(json.dumps({"server": {"port": "abc"}}), encoding="utf-8")
    with pytest.raises(SystemExit):
        server._parse_serve_args(
            ["--config", str(config_path), "--port", "8080"], parser
        )


def test_config_stripping_stops_at_the_end_of_options_marker():
    """`--` makes argparse read the rest as positionals, so the scan stops too.

    `_extract_serve_config_path` parses rather than scans, so it never reads a
    `--config` after `--` as a flag. Stripping one would make the two disagree.
    """
    assert server._strip_serve_config_arg(["--config", "x", "--port", "1"]) == [
        "--port",
        "1",
    ]
    assert server._strip_serve_config_arg(["--config", "x", "--", "--config", "y"]) == [
        "--",
        "--config",
        "y",
    ]
    assert server._strip_serve_config_arg(["--config=x", "--", "--config=y"]) == [
        "--",
        "--config=y",
    ]


def test_a_bad_server_config_value_is_rejected_naming_the_file(tmp_path, capsys):
    """`server` values are attributed too, not just `server` key names."""
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({"server": {"port": "not-a-number"}}), encoding="utf-8"
    )
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit) as excinfo:
        server._parse_serve_args(["--config", str(config_path)], parser)
    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert str(config_path) in stderr
    assert "--port" in stderr


def test_a_valid_server_section_survives_its_probe(tmp_path):
    """Every serve flag has a default, so a good section parses on its own."""
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({"server": {"port": 9000, "host": "0.0.0.0"}}), encoding="utf-8"
    )
    parser = server._build_serve_parser()
    serve_args, _ = server._parse_serve_args(["--config", str(config_path)], parser)
    assert (serve_args.port, serve_args.host) == (9000, "0.0.0.0")


def test_false_server_config_value_still_means_use_the_default(tmp_path):
    """Server keys are name-checked already, so `false` there stays a skip."""
    argv = server._config_mapping_to_argv({"structured_log": False})
    assert argv == []


def test_unknown_server_config_key_is_rejected_naming_the_file(tmp_path, capsys):
    """Both sections name the config file, which is what the README promises."""
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({"server": {"hostt": "127.0.0.1"}}), encoding="utf-8"
    )
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit) as excinfo:
        server._parse_serve_args(["--config", str(config_path)], parser)
    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert str(config_path) in stderr
    assert "hostt" in stderr


def test_null_server_config_value_still_means_use_the_default(tmp_path):
    """Server keys are name-checked already, so a null there stays a skip."""
    argv = server._config_mapping_to_argv({"namespace": None})
    assert argv == []


def test_unknown_generate_config_key_is_rejected_in_yaml_form(tmp_path, capsys):
    """YAML and JSON config forms take the same validation path."""
    pytest.importorskip("yaml")
    config_path = tmp_path / "serve-config.yaml"
    config_path.write_text("generate:\n  componentss: apigateway\n", encoding="utf-8")
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit) as excinfo:
        server._parse_serve_args(["--config", str(config_path)], parser)
    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert str(config_path) in stderr
    assert "componentss" in stderr


def test_invalid_generate_config_value_also_fails_at_load_with_attribution(tmp_path, capsys):
    """A valid key with an out-of-range value now fails early, with the file named."""
    config_path = tmp_path / "serve-config.json"
    config_path.write_text(
        json.dumps({"generate": {"instances_per_component": 999}}),
        encoding="utf-8",
    )
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit) as excinfo:
        server._parse_serve_args(["--config", str(config_path)], parser)
    assert excinfo.value.code == 2
    assert str(config_path) in capsys.readouterr().err


def test_probe_does_not_leak_parser_output_to_the_console(tmp_path, capsys):
    """The probe captures argparse's streams; only the raised error surfaces."""
    with pytest.raises(ValueError):
        server._probe_config_generate_argv(
            ["--componentss", "x"],
            tmp_path / "c.json",
            server._resolve_generate_parse_args(),
        )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_structured_request_logger_writes_request_and_error_jsonl(amc, tmp_path, monkeypatch):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    log_path = tmp_path / "server-requests.jsonl"
    request_logger = server.StructuredRequestLogger(log_path)
    security = server.ServerSecurityConfig(auth_token="secret-token")

    with _running_test_server(
        state,
        security=security,
        request_logger=request_logger,
    ) as base_url:
        unauthorized = urllib.request.Request(
            base_url + "/v1/state?token=plain-secret",
            headers={"authorization": "Bearer wrong-token"},
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(unauthorized, timeout=5)
        assert excinfo.value.code == 401

        def fail_command(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(server, "run_command", fail_command)
        command = urllib.request.Request(
            base_url + "/v1/commands",
            data=json.dumps({"command": "kubectl get pods -n saas-prod"}).encode("utf-8"),
            headers={
                "authorization": "Bearer secret-token",
                "content-type": "application/json",
                "user-agent": "amc-test",
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(command, timeout=5)
        assert excinfo.value.code == 500
        excinfo.value.read()

    records = _read_jsonl_records_until(log_path, 3)
    assert [record["event"] for record in records] == ["request", "request", "error"]
    assert records[0]["status"] == 401
    assert records[0]["path"] == "/v1/state"
    assert records[0]["query"]["token"] == ["***"]
    assert records[0]["authorization"] == "present"
    assert records[1]["status"] == 500
    assert records[1]["method"] == "POST"
    assert records[1]["user_agent"] == "amc-test"
    assert records[2]["error_type"] == "RuntimeError"
    assert records[2]["message"] == "boom"
    serialized = "\n".join(json.dumps(record, sort_keys=True) for record in records)
    assert "secret-token" not in serialized
    assert "plain-secret" not in serialized


def test_security_redacts_sensitive_query_and_command_trace_values(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    with _running_test_server(state) as base_url:
        urllib.request.urlopen(
            base_url + "/version?" + urllib.parse.urlencode({"id_token": "api-secret"}),
            timeout=5,
        ).close()

    result = server.run_command(
        state,
        command="kubectl get pods --token command-secret -n saas-prod",
    )
    payload = json.dumps(state.traces.export_payload(), sort_keys=True)
    assert "api-secret" not in payload
    assert "command-secret" not in payload
    assert result["trace"]["raw_input"] == "kubectl get pods --token '***' -n saas-prod"
    api_trace = next(
        item for item in state.traces.list_traces(limit=10)
        if item["command_family"] == "kubernetes-api"
    )
    assert api_trace["parsed_flags"]["query"]["id_token"] == ["***"]


def test_post_unexpected_exception_returns_server_error(amc, tmp_path, monkeypatch):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    with _running_test_server(state) as base_url:
        def fail_command(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(server, "run_command", fail_command)
        request = urllib.request.Request(
            base_url + "/v1/commands",
            data=json.dumps({"command": "kubectl get pods -n saas-prod"}).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(request, timeout=5)
        assert excinfo.value.code == 500
        body = json.loads(excinfo.value.read().decode("utf-8"))
        # Generic on purpose: str(exc) can carry internals (paths, tokens);
        # the detail is preserved in the structured error log instead.
        assert body["error"] == "internal server error"
        assert "boom" not in json.dumps(body)


def test_record_server_error_stderr_fallback(capsys):
    # A-071/A-076: with no structured logger, the error detail (including a
    # traceback tail) still reaches stderr so a default-flags failure is not
    # silent.
    try:
        raise RuntimeError("boom-detail")
    except RuntimeError as exc:
        server._record_server_error(None, where="unit-test", exc=exc, path="/x")
    err = capsys.readouterr().err
    assert "[serve-error] unit-test: RuntimeError: boom-detail" in err
    assert "  path: /x" in err
    assert "Traceback (most recent call last):" in err
    assert "boom-detail" in err


def test_record_server_error_uses_logger_when_present(capsys):
    records = []

    class _Logger:
        def log_error(self, record):
            records.append(record)

    try:
        raise ValueError("logged-detail")
    except ValueError as exc:
        server._record_server_error(_Logger(), where="unit-test", exc=exc)
    # Structured sink used; nothing on stderr.
    assert capsys.readouterr().err == ""
    assert len(records) == 1
    record = records[0]
    assert record["where"] == "unit-test"
    assert record["error_type"] == "ValueError"
    assert record["message"] == "logged-detail"
    assert "Traceback (most recent call last):" in record["traceback"]


@pytest.mark.parametrize("max_lines", [1, 2, 5, 8])
def test_capture_traceback_tail_strict_cap(max_lines):
    # A-076: the truncation marker counts against ``max_lines`` so the flooding
    # guard is a strict cap (marker + tail must never total max_lines + 1).
    def _recurse(n):
        if n:
            _recurse(n - 1)
        raise RuntimeError("deep-boom")

    try:
        _recurse(20)
    except RuntimeError:
        tail = server._capture_traceback_tail(max_lines=max_lines)
    lines = tail.split("\n")
    assert len(lines) <= max_lines
    assert lines[0] == "...(traceback truncated)..."


def test_get_500_writes_stderr_block_without_logger(amc, tmp_path, monkeypatch, capsys):
    # A-071: forced 500 with default flags (request_logger None) leaves its
    # detail in the stderr sink; the client body stays generic.
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    with _running_test_server(state) as base_url:
        def fail_command(*args, **kwargs):
            raise RuntimeError("stderr-boom")

        monkeypatch.setattr(server, "run_command", fail_command)
        request = urllib.request.Request(
            base_url + "/v1/commands",
            data=json.dumps({"command": "kubectl get pods -n saas-prod"}).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(request, timeout=5)
        assert excinfo.value.code == 500
        body = json.loads(excinfo.value.read().decode("utf-8"))
        assert body["error"] == "internal server error"
    err = capsys.readouterr().err
    assert "[serve-error] request: RuntimeError: stderr-boom" in err
    assert "Traceback (most recent call last):" in err


def test_patch_kubernetes_api_unexpected_exception_returns_status_500(
    amc, tmp_path, monkeypatch
):
    # A-073: a raising mutating handler yields a Kubernetes Status 500, not a
    # dropped connection, and the failure is recorded.
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    with _running_test_server(state) as base_url:
        def boom(*args, **kwargs):
            raise RuntimeError("patch-boom")

        monkeypatch.setattr(server, "kubernetes_api_mutating_response", boom)
        request = urllib.request.Request(
            base_url + "/apis/apps/v1/namespaces/saas-prod/deployments/apigateway/scale",
            data=json.dumps({"spec": {"replicas": 5}}).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="PATCH",
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(request, timeout=5)
        assert excinfo.value.code == 500
        body = json.loads(excinfo.value.read().decode("utf-8"))
        assert body["kind"] == "Status"
        assert body["code"] == 500
        assert "patch-boom" not in json.dumps(body)

        # The failed mutation must still land in the kubernetes-api trace ring
        # (the /v1/debug backlog), classified unsupported — otherwise the raise
        # leaves no record, the exact A-073 gap this boundary closes.
        query = urllib.parse.urlencode({"family": "kubernetes-api", "q": "PATCH"})
        search = _get_json(base_url + "/v1/debug/search?" + query)
        assert search["total"] == 1
        assert search["items"][0]["support_status"] == "unsupported"
        assert search["items"][0]["matched_rule_id"] == "k8s.internal_error"


def test_readyz_check_artifacts_missing(amc, tmp_path):
    # A-074: an empty output dir (nothing generated) is not ready.
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    ready, reason = server._readyz_check(state)
    assert ready is False
    assert reason == "artifacts"


def test_readyz_check_healthy_when_declared_files_present(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    expected = state.legacy._collect_emitted_filenames(
        emit_selection=state.args.emit_selection,
        components=state.components,
        combine=bool(getattr(state.args, "combine", False)),
    )
    assert expected  # the run declares at least one artifact
    for filename in expected:
        (tmp_path / filename).write_text("x")
    ready, reason = server._readyz_check(state)
    assert ready is True
    assert reason == ""


def test_readyz_check_failed_generation_thread(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    expected = state.legacy._collect_emitted_filenames(
        emit_selection=state.args.emit_selection,
        components=state.components,
        combine=bool(getattr(state.args, "combine", False)),
    )
    for filename in expected:
        (tmp_path / filename).write_text("x")
    state.generation.thread = "failed"
    ready, reason = server._readyz_check(state)
    assert ready is False
    assert reason == "generation"


def test_readyz_http_503_names_dimension_on_empty_dir(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    with _running_test_server(state) as base_url:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(base_url + "/readyz", timeout=5)
        assert excinfo.value.code == 503
        body = json.loads(excinfo.value.read().decode("utf-8"))
        assert body == {"ready": False, "reason": "artifacts"}


def test_readyz_http_200_when_ready(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    expected = state.legacy._collect_emitted_filenames(
        emit_selection=state.args.emit_selection,
        components=state.components,
        combine=bool(getattr(state.args, "combine", False)),
    )
    for filename in expected:
        (tmp_path / filename).write_text("x")
    with _running_test_server(state) as base_url:
        assert _get_json(base_url + "/readyz") == {"ready": True}


def test_mutating_kubernetes_api_updates_simulated_state(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    with _running_test_server(state) as base_url:
        scale_request = urllib.request.Request(
            base_url + "/apis/apps/v1/namespaces/saas-prod/deployments/apigateway/scale",
            data=json.dumps({"spec": {"replicas": 5}}).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="PATCH",
        )
        with urllib.request.urlopen(scale_request, timeout=5) as response:
            scale = json.loads(response.read().decode("utf-8"))
        assert scale["kind"] == "Scale"
        assert scale["spec"]["replicas"] == 5

        deployment = _get_json(
            base_url + "/apis/apps/v1/namespaces/saas-prod/deployments/apigateway"
        )
        assert deployment["spec"]["replicas"] == 5
        assert deployment["status"]["readyReplicas"] == 5
        assert deployment["metadata"]["resourceVersion"] != "1"
        assert deployment["metadata"]["generation"] >= 2
        assert deployment["status"]["observedGeneration"] == deployment["metadata"]["generation"]

        bool_scale_request = urllib.request.Request(
            base_url + "/apis/apps/v1/namespaces/saas-prod/deployments/apigateway/scale",
            data=json.dumps({"spec": {"replicas": False}}).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="PATCH",
        )
        with urllib.request.urlopen(bool_scale_request, timeout=5) as response:
            bool_scale = json.loads(response.read().decode("utf-8"))
        assert bool_scale["spec"]["replicas"] == 5
        deployment = _get_json(
            base_url + "/apis/apps/v1/namespaces/saas-prod/deployments/apigateway"
        )
        assert deployment["spec"]["replicas"] == 5

        delete_request = urllib.request.Request(
            base_url + "/api/v1/namespaces/saas-prod/pods/apigateway-0",
            method="DELETE",
        )
        with urllib.request.urlopen(delete_request, timeout=5) as response:
            deleted = json.loads(response.read().decode("utf-8"))
        assert deleted["reason"] == "Deleted"

        pods = _get_json(base_url + "/api/v1/namespaces/saas-prod/pods")
        assert all(pod["metadata"]["name"] != "apigateway-0" for pod in pods["items"])
        assert any(
            pod["metadata"]["name"].startswith("apigateway-recreated-")
            for pod in pods["items"]
        )

        query = urllib.parse.urlencode({"family": "kubernetes-api", "q": "PATCH"})
        search = _get_json(base_url + "/v1/debug/search?" + query)
        assert search["total"] == 2
        assert all(item["support_status"] == "supported" for item in search["items"])

        readonly_pod_patch = urllib.request.Request(
            base_url + "/api/v1/namespaces/saas-prod/pods/cacheservice-0",
            data=json.dumps({"metadata": {"labels": {"debug": "true"}}}).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="PATCH",
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(readonly_pod_patch, timeout=5)
        assert excinfo.value.code == 405
        readonly_status = json.loads(excinfo.value.read().decode("utf-8"))
        assert readonly_status["kind"] == "Status"
        assert readonly_status["reason"] == "MethodNotAllowed"
        pods = _get_json(base_url + "/api/v1/namespaces/saas-prod/pods")
        assert any(pod["metadata"]["name"] == "cacheservice-0" for pod in pods["items"])

        status_patch = urllib.request.Request(
            base_url + "/apis/apps/v1/namespaces/saas-prod/deployments/apigateway/status",
            data=json.dumps({"status": {"readyReplicas": 0}}).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="PATCH",
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(status_patch, timeout=5)
        assert excinfo.value.code == 405
        status_patch_status = json.loads(excinfo.value.read().decode("utf-8"))
        assert status_patch_status["kind"] == "Status"
        assert status_patch_status["reason"] == "MethodNotAllowed"
        deployment = _get_json(
            base_url + "/apis/apps/v1/namespaces/saas-prod/deployments/apigateway"
        )
        assert deployment["spec"]["replicas"] == 5
        assert deployment["status"]["readyReplicas"] == 5
        query = urllib.parse.urlencode({
            "family": "kubernetes-api",
            "q": "deployments/apigateway/status",
        })
        search = _get_json(base_url + "/v1/debug/search?" + query)
        assert search["total"] == 1
        assert search["items"][0]["support_status"] == "unsupported"
        assert search["items"][0]["matched_rule_id"] == "k8s.method.unsupported"

        pod_log_delete = urllib.request.Request(
            base_url + "/api/v1/namespaces/saas-prod/pods/cacheservice-0/log",
            method="DELETE",
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(pod_log_delete, timeout=5)
        assert excinfo.value.code == 405
        pod_log_status = json.loads(excinfo.value.read().decode("utf-8"))
        assert pod_log_status["kind"] == "Status"
        assert pod_log_status["reason"] == "MethodNotAllowed"
        pods = _get_json(base_url + "/api/v1/namespaces/saas-prod/pods")
        assert any(pod["metadata"]["name"] == "cacheservice-0" for pod in pods["items"])
        query = urllib.parse.urlencode({
            "family": "kubernetes-api",
            "q": "pods/cacheservice-0/log",
        })
        search = _get_json(base_url + "/v1/debug/search?" + query)
        assert search["total"] == 1
        assert search["items"][0]["support_status"] == "unsupported"
        assert search["items"][0]["matched_rule_id"] == "k8s.method.unsupported"

        configmap_request = urllib.request.Request(
            base_url + "/api/v1/namespaces/saas-prod/configmaps",
            data=json.dumps({
                "metadata": {"name": "debug-flags"},
                "data": {"mode": "on"},
            }).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(configmap_request, timeout=5) as response:
            configmap = json.loads(response.read().decode("utf-8"))
        assert configmap["kind"] == "ConfigMap"
        assert configmap["metadata"]["name"] == "debug-flags"
        assert configmap["data"]["mode"] == "on"

        configmaps = _get_json(base_url + "/api/v1/namespaces/saas-prod/configmaps")
        assert any(item["metadata"]["name"] == "debug-flags" for item in configmaps["items"])

        tools_configmap_request = urllib.request.Request(
            base_url + "/api/v1/namespaces/tools/configmaps",
            data=json.dumps({
                "metadata": {"name": "tools-flags", "labels": {"team": "ops"}},
                "data": {"mode": "tools"},
            }).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(tools_configmap_request, timeout=5) as response:
            tools_configmap = json.loads(response.read().decode("utf-8"))
        assert tools_configmap["metadata"]["namespace"] == "tools"
        assert tools_configmap["metadata"]["labels"]["team"] == "ops"
        default_configmaps = _get_json(base_url + "/api/v1/namespaces/saas-prod/configmaps")
        assert all(item["metadata"]["name"] != "tools-flags" for item in default_configmaps["items"])
        tools_configmaps = _get_json(base_url + "/api/v1/namespaces/tools/configmaps")
        assert any(item["metadata"]["name"] == "tools-flags" for item in tools_configmaps["items"])
        state_payload = _get_json(base_url + "/v1/state")
        assert "tools" in state_payload["mutations"]["drift"]["namespaces"]
        assert state_payload["mutations"]["drift"]["created_resources"] >= 2

        labeled_deployment_request = urllib.request.Request(
            base_url + "/apis/apps/v1/namespaces/saas-prod/deployments",
            data=json.dumps({
                "metadata": {
                    "name": "debug-worker",
                    "labels": {"team": "ops"},
                },
                "spec": {
                    "replicas": 2,
                    "selector": {"matchLabels": {"team": "ops"}},
                    "template": {"metadata": {"labels": {"team": "ops"}}},
                },
            }).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(labeled_deployment_request, timeout=5) as response:
            labeled_deployment = json.loads(response.read().decode("utf-8"))
        assert labeled_deployment["metadata"]["labels"]["team"] == "ops"
        assert labeled_deployment["spec"]["selector"]["matchLabels"]["team"] == "ops"
        assert labeled_deployment["metadata"]["generation"] == 1
        assert labeled_deployment["status"]["observedGeneration"] == 1

        labeled_service_request = urllib.request.Request(
            base_url + "/api/v1/namespaces/saas-prod/services",
            data=json.dumps({
                "metadata": {"name": "debug-worker"},
                "spec": {
                    "selector": {"team": "ops"},
                    "ports": [{"port": 9090}],
                },
            }).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(labeled_service_request, timeout=5) as response:
            labeled_service = json.loads(response.read().decode("utf-8"))
        assert labeled_service["spec"]["selector"] == {"team": "ops"}
        assert labeled_service["spec"]["ports"][0]["port"] == 9090

        empty_configmap_request = urllib.request.Request(
            base_url + "/api/v1/namespaces/saas-prod/configmaps",
            data=json.dumps({"metadata": {"name": "empty-config"}}).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(empty_configmap_request, timeout=5) as response:
            empty_configmap = json.loads(response.read().decode("utf-8"))
        assert empty_configmap["data"] == {"simulated": "true"}
        configmaps_output = server.run_command(
            state,
            command="kubectl get configmaps -n saas-prod",
        )["result"]["stdout"]
        empty_config_row = next(line for line in configmaps_output.splitlines() if line.startswith("empty-config"))
        assert empty_config_row.split()[1] == "1"

        unnamed_ingress_request = urllib.request.Request(
            base_url + "/apis/networking.k8s.io/v1/namespaces/saas-prod/ingresses",
            data=b"{}",
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(unnamed_ingress_request, timeout=5) as response:
            unnamed_ingress = json.loads(response.read().decode("utf-8"))
        assert unnamed_ingress["kind"] == "Ingress"
        assert unnamed_ingress["metadata"]["name"] == "simulated-ingress"
        assert "simulated-ingresse" not in json.dumps(unnamed_ingress)

        zero_deployment_request = urllib.request.Request(
            base_url + "/apis/apps/v1/namespaces/saas-prod/deployments",
            data=json.dumps({
                "metadata": {"name": "scaled-down-worker"},
                "spec": {"replicas": 0},
            }).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(zero_deployment_request, timeout=5) as response:
            zero_deployment = json.loads(response.read().decode("utf-8"))
        assert zero_deployment["spec"]["replicas"] == 0
        assert zero_deployment["status"]["replicas"] == 0
        assert zero_deployment["status"]["readyReplicas"] == 0
        deployments_output = server.run_command(
            state,
            command="kubectl get deployments -n saas-prod",
        )["result"]["stdout"]
        worker_row = next(line for line in deployments_output.splitlines() if line.startswith("scaled-down-worker"))
        assert worker_row.split()[1] == "0/0"

        zero_statefulset_request = urllib.request.Request(
            base_url + "/apis/apps/v1/namespaces/saas-prod/statefulsets",
            data=json.dumps({
                "metadata": {"name": "scaled-down-cache"},
                "spec": {"replicas": 0},
            }).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(zero_statefulset_request, timeout=5) as response:
            zero_statefulset = json.loads(response.read().decode("utf-8"))
        assert zero_statefulset["spec"]["replicas"] == 0
        assert zero_statefulset["status"]["replicas"] == 0
        assert zero_statefulset["status"]["readyReplicas"] == 0
        statefulsets_output = server.run_command(
            state,
            command="kubectl get statefulsets -n saas-prod",
        )["result"]["stdout"]
        cache_row = next(line for line in statefulsets_output.splitlines() if line.startswith("scaled-down-cache"))
        assert cache_row.split()[1] == "0/0"

        malformed_patch = urllib.request.Request(
            base_url + "/api/v1/namespaces/saas-prod/configmaps/debug-flags",
            data=b"{not-json",
            headers={"content-type": "application/json"},
            method="PATCH",
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(malformed_patch, timeout=5)
        assert excinfo.value.code == 400
        error_status = json.loads(excinfo.value.read().decode("utf-8"))
        assert error_status["kind"] == "Status"
        assert error_status["reason"] == "BadRequest"
        assert "invalid JSON body" in error_status["message"]

        list_patch = urllib.request.Request(
            base_url + "/api/v1/namespaces/saas-prod/configmaps/debug-flags",
            data=b"[]",
            headers={"content-type": "application/json"},
            method="PATCH",
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(list_patch, timeout=5)
        assert excinfo.value.code == 400
        error_status = json.loads(excinfo.value.read().decode("utf-8"))
        assert error_status["kind"] == "Status"
        assert error_status["reason"] == "BadRequest"
        assert error_status["message"] == "JSON body must be an object"

        pvc_request = urllib.request.Request(
            base_url + "/api/v1/namespaces/saas-prod/persistentvolumeclaims",
            data=json.dumps({
                "metadata": {"name": "scratch"},
                "spec": {
                    "accessModes": [123, "ReadWriteMany"],
                    "resources": {"requests": {"storage": "2Gi"}},
                },
            }).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(pvc_request, timeout=5) as response:
            pvc = json.loads(response.read().decode("utf-8"))
        assert pvc["kind"] == "PersistentVolumeClaim"
        assert pvc["spec"]["accessModes"] == ["123,ReadWriteMany"]
        assert pvc["status"]["accessModes"] == ["123,ReadWriteMany"]

        delete_configmap = urllib.request.Request(
            base_url + "/api/v1/namespaces/saas-prod/configmaps/debug-flags",
            method="DELETE",
        )
        with urllib.request.urlopen(delete_configmap, timeout=5) as response:
            deleted_configmap = json.loads(response.read().decode("utf-8"))
        assert deleted_configmap["reason"] == "Deleted"
        configmaps = _get_json(base_url + "/api/v1/namespaces/saas-prod/configmaps")
        assert all(item["metadata"]["name"] != "debug-flags" for item in configmaps["items"])


def test_state_summary_counts_anomalies_without_copying_rows(amc, tmp_path, monkeypatch):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    expected_count = len(state.anomaly_rows)

    def fail_generated_rows():
        raise AssertionError("summary should not copy anomaly rows to count them")

    monkeypatch.setattr(state, "generated_rows", fail_generated_rows)
    monkeypatch.setattr(state, "active_anomalies", lambda limit=20: [])

    assert state.summary()["anomaly_count"] == expected_count


def test_active_anomalies_does_not_copy_all_rows(amc, tmp_path, monkeypatch):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    now = state.clock.pause()
    rows = [
        {
            "timestamp": server._format_dt(now),
            "scenario": "active",
            "span_start": server._format_dt(now - _dt.timedelta(minutes=1)),
            "span_end": server._format_dt(now + _dt.timedelta(minutes=1)),
        },
        {
            "timestamp": server._format_dt(now + _dt.timedelta(hours=1)),
            "scenario": "inactive",
            "span_start": server._format_dt(now + _dt.timedelta(minutes=10)),
            "span_end": server._format_dt(now + _dt.timedelta(hours=1)),
        },
    ]
    state.replace_generated_rows(rows)

    def fail_generated_rows():
        raise AssertionError("active_anomalies should not copy all anomaly rows")

    monkeypatch.setattr(state, "generated_rows", fail_generated_rows)

    assert state.active_anomalies(limit=10) == [rows[0]]


def test_anomalies_endpoint_slices_without_copying_all_rows(amc, tmp_path, monkeypatch):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    rows = [
        {"timestamp": "2026-03-01 00:00:00", "scenario": "first"},
        {"timestamp": "2026-03-01 00:01:00", "scenario": "second"},
    ]
    state.replace_generated_rows(rows)

    def fail_generated_rows():
        raise AssertionError("endpoint should not copy all anomaly rows before slicing")

    monkeypatch.setattr(state, "generated_rows", fail_generated_rows)
    with _running_test_server(state) as base_url:
        payload = _get_json(base_url + "/v1/anomalies?limit=1")

    assert len(payload["items"]) == 1
    assert payload["items"][0] == rows[0]


def test_json_safe_payload_stabilizes_callables_and_sets():
    payload = server._json_safe_payload({
        "callback": lambda value: value,
        "unordered": {"beta", "alpha"},
        "frozen": frozenset({"2", "1"}),
    })

    assert payload["callback"] == "<callable>"
    assert payload["unordered"] == ["alpha", "beta"]
    assert payload["frozen"] == ["1", "2"]
    assert "0x" not in json.dumps(payload, sort_keys=True)


def test_debug_ui_caches_static_scenario_catalog():
    html = server.DEBUG_HTML

    assert html.count('getJSON("/v1/scenarios")') == 1
    assert "let scenarioCatalogPromise = null;" in html
    assert "async function getScenarioCatalog()" in html
    assert "getScenarioCatalog()" in html
    assert "renderScenarioCatalogOnce(scenarios);" in html


def test_debug_ui_exposes_analysis_workflows():
    html = server.DEBUG_HTML

    for marker in (
        'id="exportTraceJson"',
        'id="exportUnsupportedJson"',
        'id="exportUnsupportedCsv"',
        'id="globalScenarioFilter"',
        'id="globalKindFilter"',
        'id="globalStatusFilter"',
        'id="globalFamilyFilter"',
        'id="globalWindowFilter"',
        'id="timelineRows"',
        'id="resourceDiffs"',
        'id="miniCharts"',
        'id="resourceDrawer"',
        'id="scenarioCatalogFreshness"',
        'id="runtimeFreshness"',
        "function buildTimelineRows(",
        "function renderTimeline(",
        "function renderResourceDiffs(",
        "function renderMiniCharts(",
        "function openResourceDrawer(",
        "function resourceApiPath(",
        "function pytestSnippetForUnsupported(",
        "function downloadJSON(",
        "function downloadCSV(",
    ):
        assert marker in html


def test_debug_ui_recent_events_render_namespace_column():
    html = server.DEBUG_HTML

    assert "<th>Namespace</th>" in html
    assert "${esc(event.namespace || \"-\")}" in html


def test_server_architecture_cleanup_modules_back_public_facade():
    traces = importlib.import_module("anomaly_metric_creator.server_traces")
    mutations = importlib.import_module("anomaly_metric_creator.server_mutations")
    debug_ui = importlib.import_module("anomaly_metric_creator.server_debug_ui")
    commands = importlib.import_module("anomaly_metric_creator.server_commands")
    kubernetes = importlib.import_module("anomaly_metric_creator.server_kubernetes")
    helm = importlib.import_module("anomaly_metric_creator.server_helm")

    assert server.CommandTrace is traces.CommandTrace
    assert server.CommandTraceStore is traces.CommandTraceStore
    assert server.COMMAND_TRACE_DB_SCHEMA_VERSION == traces.COMMAND_TRACE_DB_SCHEMA_VERSION
    assert server.COMMAND_TRACE_EXPORT_VERSION == traces.COMMAND_TRACE_EXPORT_VERSION
    assert server.SimulationMutations is mutations.SimulationMutations
    assert server.WorkloadMutation is mutations.WorkloadMutation
    assert server.HelmReleaseMutation is mutations.HelmReleaseMutation
    assert server.DEBUG_HTML is debug_ui.DEBUG_HTML
    assert server.ParsedCommand is commands.ParsedCommand
    assert server.CommandResult is commands.CommandResult
    assert server.parse_command is commands.parse_command
    assert server.render_command is commands.render_command
    assert server.run_command is commands.run_command
    assert server.resource_snapshot is commands.resource_snapshot
    assert server.KubernetesApiResponse is kubernetes.KubernetesApiResponse
    assert server.kubernetes_api_response is kubernetes.kubernetes_api_response
    assert server.kubernetes_api_post_response is kubernetes.kubernetes_api_post_response
    assert server.kubernetes_api_mutating_response is kubernetes.kubernetes_api_mutating_response
    assert server.render_kubeconfig is kubernetes.render_kubeconfig
    assert server._helm_secret_objects is helm._helm_secret_objects
    assert server._helm_release_payload is helm._helm_release_payload


def test_continuous_generation_refreshes_state(amc, tmp_path, monkeypatch):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    calls = []
    streams = []

    def fake_main(argv):
        calls.append(list(argv))
        (tmp_path / "anomalies.csv").write_text(
            "timestamp,component,metric,value,scenario,span_start,span_end\n"
            "2026-03-01 00:00:00,cacheservice,error_rate,1,cache_leak_restart,"
            "2026-03-01 00:00:00,2026-03-01 00:05:00\n",
            encoding="utf-8",
        )

    def fake_stream(current_state):
        streams.append(current_state.generated_rows()[0]["scenario"])

    monkeypatch.setattr(state.legacy, "main", fake_main)
    monkeypatch.setattr(server, "_run_otel_streams", fake_stream)
    state.args.otel_enabled = True
    server._run_continuous_generation_once(
        state,
        ["--output-dir", str(tmp_path)],
        stream_otel=True,
    )

    assert state.generation.generation_count == 1
    assert state.generation.last_seed == int(state.args.seed) + 1
    assert state.generation.last_anomaly_count == 1
    assert state.generated_rows()[0]["scenario"] == "cache_leak_restart"
    assert "--otel-send" in calls[0]
    assert "none" in calls[0]
    assert streams == ["cache_leak_restart"]
    assert state.otel_status["stream_batches"] == 1


def test_continuous_generation_records_system_exit(amc, tmp_path, monkeypatch):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)

    def fail_main(argv):
        raise SystemExit("bad generated args")

    monkeypatch.setattr(state.legacy, "main", fail_main)
    server._run_continuous_generation_once(
        state,
        ["--output-dir", str(tmp_path)],
        stream_otel=False,
    )

    assert state.generation.thread == "failed"
    assert state.generation.generation_count == 0
    assert state.generation.last_seed == int(state.args.seed) + 1
    # A-072: SystemExit is summarized with its code, not just str(exc), so a
    # bare-code exit ("2") is no longer an opaque last_error.
    assert state.generation.last_error == "SystemExit(code='bad generated args')"


def test_continuous_generation_marks_otel_disabled_without_streaming(amc, tmp_path, monkeypatch):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    started_threads = []

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self):
            started_threads.append((self.name, self.daemon))

    monkeypatch.setattr(server.threading, "Thread", FakeThread)

    stop_event = server._start_continuous_generation(
        state,
        ["--output-dir", str(tmp_path)],
        enabled=True,
        interval_seconds=60.0,
        stream_otel=False,
    )

    assert stop_event is not None
    assert started_threads == [("amc-continuous-generation", True)]
    assert state.generation.enabled is True
    assert state.otel_status["enabled"] is False
    assert state.otel_status["thread"] == "disabled"
    assert state.otel_status["continuous"] is False


def test_stop_continuous_generation_signals_shutdown_and_joins_worker():
    stop_event = server.threading.Event()
    joined = []

    class FakeWorker:
        def is_alive(self):
            return True

        def join(self, timeout):
            joined.append(timeout)

    stop_event.worker_thread = FakeWorker()

    server._stop_continuous_generation(stop_event, timeout=0.25)

    assert stop_event.is_set()
    assert joined == [0.25]


def test_log_stream_follows_refreshed_generation_logs(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    log_path = tmp_path / "metric_report.log"
    log_path.write_text("initial log\n", encoding="utf-8")
    with _running_test_server(state) as base_url:
        with urllib.request.urlopen(base_url + "/v1/logs/stream", timeout=5) as response:
            initial_lines = []
            for _ in range(8):
                line = response.readline().decode("utf-8")
                initial_lines.append(line)
                if "initial log" in line:
                    break
            assert any("initial log" in line for line in initial_lines)

            log_path.write_text("refreshed log\n", encoding="utf-8")
            with state.generation.lock:
                state.generation.generation_count += 1
                state.generation.last_seed = 123

            refreshed_lines = []
            for _ in range(12):
                line = response.readline().decode("utf-8")
                refreshed_lines.append(line)
                if "refreshed log" in line:
                    break
            assert any("refreshed log" in line for line in refreshed_lines)


def test_shutdown_event_closes_long_lived_sse_streams(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    state.shutdown_event.set()
    with _running_test_server(state) as base_url:
        for path in ("/v1/debug/events", "/v1/logs/stream"):
            with urllib.request.urlopen(base_url + path, timeout=5) as response:
                body = response.read().decode("utf-8")
            assert "event: shutdown" in body
            assert "server shutdown" in body


def test_real_kubernetes_api_resources_logs_metrics_and_auth(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    command_version = server.run_command(state, command="kubectl version")
    assert "Client Version: v1.36.2" in command_version["result"]["stdout"]
    assert "Server Version: v1.36.2-amc" in command_version["result"]["stdout"]
    with _running_test_server(state) as base_url:
        version = _get_json(base_url + "/version")
        assert version["major"] == "1"
        assert version["minor"] == "36"
        assert version["gitVersion"] == "v1.36.2-amc"

        openapi_v2 = _get_json(base_url + "/openapi/v2")
        assert openapi_v2["info"]["version"] == "v1.36.2-amc"
        pod_schema = openapi_v2["definitions"]["io.k8s.api.core.v1.Pod"]
        assert pod_schema["x-kubernetes-group-version-kind"] == [
            {"group": "", "version": "v1", "kind": "Pod"}
        ]
        assert "spec" in pod_schema["properties"]

        openapi_v3 = _get_json(base_url + "/openapi/v3")
        assert "api/v1" in openapi_v3["paths"]
        core_schema = _get_json(base_url + "/openapi/v3/api/v1?hash=ignored")
        assert core_schema["info"]["version"] == "v1.36.2-amc"
        assert "io.k8s.api.core.v1.Pod" in core_schema["components"]["schemas"]

        resources = _get_json(base_url + "/api/v1")
        core_resources = {item["name"]: set(item["verbs"]) for item in resources["resources"]}
        assert {"pods", "secrets", "configmaps", "serviceaccounts"} <= {
            item["name"] for item in resources["resources"]
        }
        for name in ("configmaps", "secrets", "services", "persistentvolumeclaims", "serviceaccounts"):
            assert {"create", "delete", "patch", "update"} <= core_resources[name]
        assert "delete" in core_resources["pods"]

        apps = _get_json(base_url + "/apis/apps/v1")
        apps_resources = {item["name"]: set(item["verbs"]) for item in apps["resources"]}
        for name in ("deployments", "daemonsets", "statefulsets"):
            assert {"create", "delete", "patch", "update"} <= apps_resources[name]

        autoscaling = _get_json(base_url + "/apis/autoscaling/v2")
        hpa_resources = {item["name"]: set(item["verbs"]) for item in autoscaling["resources"]}
        assert {"create", "delete", "patch", "update"} <= hpa_resources["horizontalpodautoscalers"]

        batch = _get_json(base_url + "/apis/batch/v1")
        assert {"jobs", "cronjobs"} <= {item["name"] for item in batch["resources"]}
        batch_resources = {item["name"]: set(item["verbs"]) for item in batch["resources"]}
        for name in ("jobs", "cronjobs"):
            assert {"create", "delete", "patch", "update"} <= batch_resources[name]

        networking = _get_json(base_url + "/apis/networking.k8s.io/v1")
        ingress_resources = {item["name"]: set(item["verbs"]) for item in networking["resources"]}
        assert {"create", "delete", "patch", "update"} <= ingress_resources["ingresses"]

        discovery = _get_json(base_url + "/apis/discovery.k8s.io/v1")
        assert {"endpointslices"} <= {item["name"] for item in discovery["resources"]}

        pods = _get_json(base_url + "/api/v1/namespaces/saas-prod/pods")
        assert pods["kind"] == "PodList"
        cache_pod = next(
            pod for pod in pods["items"]
            if pod["metadata"]["name"] == "cacheservice-0"
        )
        state_info = cache_pod["status"]["containerStatuses"][0]["state"]
        assert state_info["waiting"]["reason"] == "CrashLoopBackOff"

        nodes = _get_json(base_url + "/api/v1/nodes")
        node_versions = {
            node["status"]["nodeInfo"]["kubeletVersion"]
            for node in nodes["items"]
        }
        assert node_versions == {"v1.36.2"}

        table_request = urllib.request.Request(
            base_url + "/api/v1/namespaces/saas-prod/pods",
            headers={
                "accept": (
                    "application/json;as=Table;g=meta.k8s.io;v=v1, "
                    "application/json"
                ),
            },
        )
        with urllib.request.urlopen(table_request, timeout=5) as response:
            table = json.loads(response.read().decode("utf-8"))
        assert table["kind"] == "Table"
        cache_row = next(
            row for row in table["rows"]
            if row["cells"][0] == "cacheservice-0"
        )
        assert cache_row["cells"][2] == "CrashLoopBackOff"

        with urllib.request.urlopen(
            base_url + "/api/v1/namespaces/saas-prod/pods/cacheservice-0/log",
            timeout=5,
        ) as response:
            logs = response.read().decode("utf-8")
        assert "heap watermark exceeded" in logs

        metrics = _get_json(
            base_url + "/apis/metrics.k8s.io/v1beta1/namespaces/saas-prod/pods"
        )
        assert metrics["kind"] == "PodMetricsList"
        assert metrics["items"][0]["containers"][0]["usage"]["cpu"].endswith("m")

        jobs = _get_json(base_url + "/apis/batch/v1/namespaces/saas-prod/jobs")
        assert jobs["items"][0]["metadata"]["name"] == "scheduler-backfill"

        query = urllib.parse.urlencode({"family": "kubernetes-api", "q": "openapi/v2"})
        search = _get_json(base_url + "/v1/debug/search?" + query)
        assert search["total"] == 1
        assert search["items"][0]["matched_rule_id"] == "k8s.openapi.v2"

        slices = _get_json(
            base_url + "/apis/discovery.k8s.io/v1/namespaces/saas-prod/endpointslices"
        )
        assert slices["items"][0]["kind"] == "EndpointSlice"

        request = urllib.request.Request(
            base_url + "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews",
            data=b"\x00simulated-kubernetes-client-payload",
            headers={"content-type": "application/vnd.kubernetes.protobuf"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            review = json.loads(response.read().decode("utf-8"))
        assert review["status"]["allowed"] is True

        query = urllib.parse.urlencode({"family": "kubernetes-api", "q": "selfsubject"})
        search = _get_json(base_url + "/v1/debug/search?" + query)
        assert search["total"] == 1
        assert search["items"][0]["matched_rule_id"].endswith(".create")


def test_real_helm_storage_secrets_and_kubeconfig(amc, tmp_path):
    state = _build_state(
        amc,
        tmp_path,
        scenarios="deploy_bad_canary_rollback",
        signal_level="high",
        days=1,
    )
    with _running_test_server(state) as base_url:
        with urllib.request.urlopen(base_url + "/v1/kubeconfig", timeout=5) as response:
            kubeconfig = response.read().decode("utf-8")
        assert f"server: {base_url}" in kubeconfig
        assert "namespace: saas-prod" in kubeconfig

        selector = urllib.parse.urlencode({
            "labelSelector": "owner=helm,name=simulated-saas",
        })
        secrets = _get_json(base_url + "/api/v1/namespaces/saas-prod/secrets?" + selector)
        assert secrets["kind"] == "SecretList"
        assert len(secrets["items"]) == 4

        deployed_selector = urllib.parse.urlencode({
            "labelSelector": "owner=helm,name=simulated-saas,status=deployed",
        })
        deployed = _get_json(
            base_url + "/api/v1/namespaces/saas-prod/secrets?" + deployed_selector
        )
        assert len(deployed["items"]) == 1
        secret = deployed["items"][0]
        assert secret["metadata"]["labels"]["version"] == "4"

        helm_encoded = base64.b64decode(secret["data"]["release"])
        release = json.loads(gzip.decompress(base64.b64decode(helm_encoded)))
        assert release["name"] == "simulated-saas"
        assert release["version"] == 4
        assert release["info"]["status"] == "deployed"
        assert "Rollback to revision 2" in release["info"]["description"]
        assert "kind: Deployment" in release["manifest"]

        single = _get_json(
            base_url
            + "/api/v1/namespaces/saas-prod/secrets/"
            + secret["metadata"]["name"]
        )
        assert single["metadata"]["name"] == secret["metadata"]["name"]


def test_real_helm4_binary_smoke_when_available(amc, tmp_path):
    _require_real_client_smoke_opt_in()
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("helm binary is not installed")

    version = subprocess.run(
        [helm, "version", "--template", "{{ .Version }}"],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    if version.returncode != 0:
        pytest.skip("helm version could not be determined")
    if not version.stdout.strip().startswith("v4."):
        pytest.skip(
            "real Helm smoke requires Helm 4; Helm 3 expects protobuf "
            "release Secret payloads"
        )

    state = _build_state(
        amc,
        tmp_path,
        scenarios="deploy_bad_canary_rollback",
        signal_level="high",
        days=1,
    )
    with _running_test_server(state) as base_url:
        kubeconfig = tmp_path / "helm.kubeconfig"
        kubeconfig.write_text(server.render_kubeconfig(base_url), encoding="utf-8")
        env = os.environ.copy()
        env["HOME"] = str(tmp_path / "helm-home")
        common = [helm, "--kubeconfig", str(kubeconfig), "--namespace", "saas-prod"]

        def run_helm(args):
            result = subprocess.run(
                common + args,
                env=env,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            return result.stdout

        assert "simulated-saas" in run_helm(["list"])
        assert "Rollback to revision 2" in run_helm(["status", "simulated-saas"])
        assert "Canary readiness failed" in run_helm(["history", "simulated-saas"])
        values = run_helm(["get", "values", "simulated-saas"])
        assert "deploy_bad_canary_rollback" in values
        assert "kind: Deployment" in run_helm(["get", "manifest", "simulated-saas"])


def test_real_kubectl_binary_smoke_when_available(amc, tmp_path):
    _require_real_client_smoke_opt_in()
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        pytest.skip("kubectl binary is not installed")

    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    with _running_test_server(state) as base_url:
        kubeconfig = tmp_path / "kubectl.kubeconfig"
        kubeconfig.write_text(server.render_kubeconfig(base_url), encoding="utf-8")
        env = os.environ.copy()
        env["HOME"] = str(tmp_path / "kubectl-home")
        common = [
            kubectl,
            "--kubeconfig",
            str(kubeconfig),
            "--cache-dir",
            str(tmp_path / "kubectl-cache"),
            "--request-timeout=5s",
        ]

        def run_kubectl(args):
            result = subprocess.run(
                common + args,
                env=env,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            assert "version difference" not in result.stderr.lower(), result.stderr
            return result.stdout

        assert "cronjobs" in run_kubectl(["api-resources"])
        assert "FIELDS:" in run_kubectl(["explain", "pods"])
        assert "replicas" in run_kubectl(["explain", "deployment.spec"])
        assert "deployment" in run_kubectl(["get", "all", "-n", "saas-prod"])
        assert "scheduler-backfill" in run_kubectl(["get", "jobs", "-n", "saas-prod"])
        assert "cacheservice-slice" in run_kubectl(["get", "endpointslices", "-n", "saas-prod"])
        assert "yes" in run_kubectl(["auth", "can-i", "get", "pods", "-n", "saas-prod"])


def test_rate_limit_refusal_increments_state_refusal_counter(amc, tmp_path):
    """A-075: a 429 rate-limit refusal is counted and surfaces on /v1/state.refusals."""
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    security = server.ServerSecurityConfig(rate_limit_per_minute=1)
    with _running_test_server(state, security=security) as base_url:
        # /v1/state is not a rate-limited bucket, so reading it never consumes
        # the command budget.
        before = _get_json(base_url + "/v1/state")["refusals"]
        assert before == {"worker_cap": 0, "sse": 0, "rate_limit": 0}
        command = urllib.request.Request(
            base_url + "/v1/commands",
            data=json.dumps({"command": "kubectl get pods -n saas-prod"}).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(command, timeout=5) as response:
            assert response.status == 200
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(command, timeout=5)
        assert excinfo.value.code == 429
        excinfo.value.read()
        after = _get_json(base_url + "/v1/state")["refusals"]
    assert after == {"worker_cap": 0, "sse": 0, "rate_limit": 1}


def test_sse_ceiling_refusal_increments_state_refusal_counter(amc, tmp_path):
    """A-075: an SSE-ceiling 503 (app SSE stream) is counted on /v1/state.refusals."""
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    httpd, base_url = server.start_test_server(state)
    # Force the SSE ceiling so the stream refuses before any event-stream headers.
    httpd.try_acquire_sse = lambda: False
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(base_url + "/v1/debug/events", timeout=5)
        assert excinfo.value.code == 503
        body = json.loads(excinfo.value.read().decode("utf-8"))
        assert body["error"] == "SSE connection limit reached"
        refusals = _get_json(base_url + "/v1/state")["refusals"]
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert refusals == {"worker_cap": 0, "sse": 1, "rate_limit": 0}


def test_worker_cap_refusal_increments_state_refusal_counter(amc, tmp_path):
    """A-075: a worker-cap 503 (refused in ``process_request`` before a worker
    thread is spawned) is counted on the shared ``state.refusals`` tally.

    Drains the bounded server's worker semaphore and drives ``process_request``
    in-process with a fake request, so the exact ``_refuse_saturated`` path that
    emits ``_SATURATED_503`` runs its ``record("worker_cap")`` call. No client
    connects during the drain, so nothing else touches the semaphore.
    """
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    httpd, _base_url = server.start_test_server(state)
    try:
        assert httpd._worker_semaphore is not None
        while httpd._worker_semaphore.acquire(blocking=False):
            pass  # exhaust every permit so the next process_request refuses

        sent: list[bytes] = []
        shut: list[object] = []
        # Avoid real socket teardown on the fake request.
        httpd.shutdown_request = lambda req: shut.append(req)  # type: ignore[method-assign]

        class _FakeRequest:
            def sendall(self, data: bytes) -> None:
                sent.append(data)

        fake = _FakeRequest()
        httpd.process_request(fake, ("127.0.0.1", 0))

        assert sent == [server._SATURATED_503]
        assert shut == [fake]
        refusals = state.refusals.snapshot()
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert refusals == {"worker_cap": 1, "sse": 0, "rate_limit": 0}


def test_request_id_joins_structured_record_and_command_trace(amc, tmp_path):
    """A-077: the per-request id is the join key between the structured request
    record and the CommandTrace recorded while handling that same request."""
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    log_path = tmp_path / "server-requests.jsonl"
    request_logger = server.StructuredRequestLogger(log_path)
    with _running_test_server(state, request_logger=request_logger) as base_url:
        command = urllib.request.Request(
            base_url + "/v1/commands",
            data=json.dumps({"command": "kubectl get pods -n saas-prod"}).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(command, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
    trace_request_id = body["trace"]["request_id"]
    # 12 hex chars from uuid4().hex[:12]; never blank on an HTTP-handled request.
    assert trace_request_id and len(trace_request_id) == 12

    records = _read_jsonl_records_until(log_path, 1)
    request_records = [
        record
        for record in records
        if record["event"] == "request" and record["path"] == "/v1/commands"
    ]
    assert request_records, records
    assert request_records[0]["request_id"] == trace_request_id

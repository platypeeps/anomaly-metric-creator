import base64
import contextlib
import datetime as _dt
import gzip
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

import pytest

from anomaly_metric_creator import server

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
    return server.build_state(amc, args, persist_command_db=persist_command_db, trace_limit=trace_limit)


def _get_json(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json_with_headers(url, headers):
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _pod_name(component):
    return "database-0" if component == "database" else f"{component}-0"


@contextlib.contextmanager
def _running_test_server(state, *, security=None):
    httpd, base_url = server.start_test_server(state, security=security)
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


def test_kubectl_logs_named_pod_takes_precedence_over_selector(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="db_disk_exhaustion")

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


def test_kubectl_delete_ingress_uses_stable_resource_prefix(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)

    result = server.run_command(
        state,
        command="kubectl delete ingress public-edge -n saas-prod",
    )

    assert result["result"]["stdout"] == 'ingress "public-edge" deleted\n'
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
        assert version["gitVersion"] == "v1.29.4-amc"


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
        assert body["error"] == "boom"


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
    now = state.clock.now()
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
    assert state.generation.last_error == "bad generated args"


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


def test_real_kubernetes_api_resources_logs_metrics_and_auth(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    with _running_test_server(state) as base_url:
        version = _get_json(base_url + "/version")
        assert version["gitVersion"] == "v1.29.4-amc"

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
            return result.stdout

        assert "cronjobs" in run_kubectl(["api-resources"])
        assert "deployment" in run_kubectl(["get", "all", "-n", "saas-prod"])
        assert "scheduler-backfill" in run_kubectl(["get", "jobs", "-n", "saas-prod"])
        assert "cacheservice-slice" in run_kubectl(["get", "endpointslices", "-n", "saas-prod"])
        assert "yes" in run_kubectl(["auth", "can-i", "get", "pods", "-n", "saas-prod"])

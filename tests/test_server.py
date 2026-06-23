import base64
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


def _build_state(
    amc,
    tmp_path,
    *,
    scenarios,
    components="apigateway,cacheservice,database,authservice",
    signal_level="medium",
    days=2,
    persist_command_db=None,
):
    args = amc.parse_args([
        "--duration-days", str(days),
        "--signal-level", signal_level,
        "--scenarios", scenarios,
        "--components", components,
        "--output-dir", str(tmp_path),
        "--interval-seconds", "3600",
    ])
    return server.build_state(amc, args, persist_command_db=persist_command_db)


def _get_json(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json_with_headers(url, headers):
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _pod_name(component):
    return "database-0" if component == "database" else f"{component}-0"


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
            item for item in resources["deployments"]
            if item["name"] == primary
        )
        assert deployment["status"] != "Healthy", scenario_id

        events = server.run_command(state, command="kubectl get events -n saas-prod")
        first_reason = profile.events[0].split(" ", 2)[1]
        assert first_reason in events["result"]["stdout"], scenario_id

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

    exec_result = server.run_command(state, command="kubectl exec apigateway-0 -- env -n saas-prod")
    assert "SERVICE_NAME=apigateway" in exec_result["result"]["stdout"]

    helm_all = server.run_command(state, command="helm get all simulated-saas -n saas-prod")
    assert "COMPUTED VALUES" in helm_all["result"]["stdout"]
    assert "MANIFEST" in helm_all["result"]["stdout"]

    helm_template = server.run_command(state, command="helm template simulated-saas ./chart")
    assert "kind: Deployment" in helm_template["result"]["stdout"]

    helm_test = server.run_command(state, command="helm test simulated-saas -n saas-prod")
    assert "SucceededAfterRollback" in helm_test["result"]["stdout"]


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
    httpd, base_url = server.start_test_server(state)
    try:
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

        with urllib.request.urlopen(base_url + "/debug", timeout=5) as response:
            html = response.read().decode("utf-8")
        assert "AMC Debug Console" in html
        assert "Search" in html
        assert "Unsupported Explorer" in html
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_server_auth_token_protects_debug_api_and_embeds_kubeconfig(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    security = server.ServerSecurityConfig(auth_token="test-token")
    httpd, base_url = server.start_test_server(state, security=security)
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(base_url + "/v1/state", timeout=5)
        assert excinfo.value.code == 401
        assert excinfo.value.headers["www-authenticate"] == "Bearer"

        with urllib.request.urlopen(base_url + "/healthz", timeout=5) as response:
            assert response.read().decode("utf-8") == "ok\n"
            assert response.headers["x-content-type-options"] == "nosniff"

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
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_request_body_limit_and_mutating_k8s_rejection_are_traced(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    security = server.ServerSecurityConfig(max_body_bytes=16)
    httpd, base_url = server.start_test_server(state, security=security)
    try:
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
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(delete, timeout=5)
        assert excinfo.value.code == 405
        payload = json.loads(excinfo.value.read().decode("utf-8"))
        assert payload["kind"] == "Status"
        assert payload["reason"] == "MethodNotAllowed"
        assert "read-only" in payload["message"]

        query = urllib.parse.urlencode({"family": "kubernetes-api", "q": "DELETE"})
        search = _get_json(base_url + "/v1/debug/search?" + query)
        assert search["total"] == 1
        assert search["items"][0]["support_status"] == "unsupported"
        assert search["items"][0]["matched_rule_id"] == "k8s.method.read_only"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_real_kubernetes_api_resources_logs_metrics_and_auth(amc, tmp_path):
    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    httpd, base_url = server.start_test_server(state)
    try:
        version = _get_json(base_url + "/version")
        assert version["gitVersion"] == "v1.29.4-amc"

        resources = _get_json(base_url + "/api/v1")
        assert {"pods", "secrets", "configmaps", "serviceaccounts"} <= {
            item["name"] for item in resources["resources"]
        }
        batch = _get_json(base_url + "/apis/batch/v1")
        assert {"jobs", "cronjobs"} <= {item["name"] for item in batch["resources"]}
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
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_real_helm_storage_secrets_and_kubeconfig(amc, tmp_path):
    state = _build_state(
        amc,
        tmp_path,
        scenarios="deploy_bad_canary_rollback",
        signal_level="high",
        days=1,
    )
    httpd, base_url = server.start_test_server(state)
    try:
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
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_real_helm4_binary_smoke_when_available(amc, tmp_path):
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
    httpd, base_url = server.start_test_server(state)
    try:
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
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_real_kubectl_binary_smoke_when_available(amc, tmp_path):
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        pytest.skip("kubectl binary is not installed")

    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)
    httpd, base_url = server.start_test_server(state)
    try:
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
    finally:
        httpd.shutdown()
        httpd.server_close()

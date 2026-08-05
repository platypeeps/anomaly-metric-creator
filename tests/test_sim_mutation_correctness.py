"""Regression tests for the simulator clock and command-mutation correctness
gaps closed by task ``07-17-audit-sim-mutation-correctness`` (audit items
A-012 through A-017).

Each test pins one fixed behavior so a future refactor that reintroduces the
gap fails here:

- A-012: ``SimulationClock.resume()`` on a *running* clock is a no-op and must
  not rewind simulated time.
- A-013: a kubectl mutation naming a resource absent from the overlay-aware
  snapshot exits nonzero on BOTH the ``/v1/commands`` path and the REST facade,
  and leaves the overlay untouched; a nameless ``kubectl scale`` is a usage
  error.
- A-014: concurrent ``otel_status`` writers/readers never raise (dict mutated
  under a lock) and the counter total is exact.
- A-015: a background regeneration that fails after publishing a new
  ``anomalies.csv`` reloads the on-disk rows into state.
- A-016: a zero-byte (headerless) per-component CSV yields no rows and a
  stderr warning instead of a ``StopIteration``.
- A-017: ``CommandTraceStore.list`` clamps a negative/zero limit identically on
  the memory and SQLite backends.
"""

import datetime as _dt
import threading

import pytest

from anomaly_metric_creator import server


def _state(amc, tmp_path, *, scenarios="cache_leak_restart", days=3, generate=False):
    argv = [
        "--duration-days", str(days),
        "--signal-level", "medium",
        "--scenarios", scenarios,
        "--components", "apigateway,cacheservice,database,authservice",
        "--output-dir", str(tmp_path),
        "--interval-seconds", "3600",
    ]
    if generate:
        # Publish the artifacts (anomalies.csv, per-component CSVs) to disk, the
        # way ``amc serve`` runs one-shot generation before build_state.
        amc.main(argv)
    args = amc.parse_args(argv)
    return server.build_state(amc, args)


def _snapshot_names(state, kind):
    return sorted(row["name"] for row in server.resource_snapshot(state)[kind])


# --------------------------------------------------------------------------- #
# A-012 — resume must not rewind a running clock
# --------------------------------------------------------------------------- #


def test_resume_running_clock_does_not_rewind_simulated_time():
    start = _dt.datetime(2026, 3, 1, tzinfo=_dt.timezone.utc)
    clock = server.SimulationClock(start_time=start, speedup=1.0)

    # Backdate the wall base to simulate ~100 s of accrued running time without
    # sleeping. A running clock now reads ~start+100 s.
    import time as _time

    clock._base_wall = _time.time() - 100.0
    before = clock.now()
    assert (before - start).total_seconds() >= 99.0

    resumed = clock.resume()  # running → no-op; the old code reset _base_wall
    after = clock.now()

    # The bug reset _base_wall = now(), collapsing now() back toward ``start``.
    assert (resumed - start).total_seconds() >= 99.0
    assert (after - start).total_seconds() >= 99.0
    # resume() on a running clock mutates nothing.
    assert clock._base_sim == start
    assert clock._paused is False


def test_resume_after_pause_resumes_from_paused_instant():
    start = _dt.datetime(2026, 3, 1, tzinfo=_dt.timezone.utc)
    clock = server.SimulationClock(start_time=start, speedup=1.0)
    import time as _time

    clock._base_wall = _time.time() - 100.0
    paused = clock.pause()
    assert (paused - start).total_seconds() >= 99.0
    # While paused, now() is frozen at the paused instant.
    assert clock.now() == paused
    resumed = clock.resume()
    assert resumed == paused
    assert clock._paused is False


# --------------------------------------------------------------------------- #
# A-013 — ghost mutations 404 on both entry paths; overlay untouched
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "kind, command, rest_path",
    [
        ("pods", "kubectl delete pods ghost-xyz -n saas-prod",
         "/api/v1/namespaces/saas-prod/pods/ghost-xyz"),
        ("deployments", "kubectl delete deployment ghost-dep -n saas-prod",
         "/apis/apps/v1/namespaces/saas-prod/deployments/ghost-dep"),
        ("configmaps", "kubectl delete configmap ghost-cm -n saas-prod",
         "/api/v1/namespaces/saas-prod/configmaps/ghost-cm"),
    ],
)
def test_ghost_delete_parity_command_and_rest(amc, tmp_path, kind, command, rest_path):
    state = _state(amc, tmp_path)
    state.clock.pause()  # freeze timestamps so the snapshot compare is stable

    before = _snapshot_names(state, kind)

    # Command path (/v1/commands): nonzero exit, NotFound on stderr.
    cmd = server.run_command(state, command=command)["result"]
    assert cmd["exit_code"] != 0
    assert "NotFound" in cmd["stderr"]
    assert cmd["stdout"] == ""
    assert _snapshot_names(state, kind) == before

    # REST facade: 404 Status, overlay still untouched.
    rest = server.kubernetes_api_mutating_response(state, "DELETE", rest_path, {})
    assert rest.status == 404
    assert rest.support_status == "supported"
    assert _snapshot_names(state, kind) == before


def test_ghost_deployment_scale_parity_command_and_rest(amc, tmp_path):
    state = _state(amc, tmp_path)
    state.clock.pause()
    before = _snapshot_names(state, "deployments")

    cmd = server.run_command(
        state,
        command="kubectl scale deployment/ghost-dep --replicas=5 -n saas-prod",
    )["result"]
    assert cmd["exit_code"] != 0
    assert "NotFound" in cmd["stderr"]
    assert _snapshot_names(state, "deployments") == before

    rest = server.kubernetes_api_mutating_response(
        state,
        "PATCH",
        "/apis/apps/v1/namespaces/saas-prod/deployments/ghost-dep/scale",
        {"spec": {"replicas": 5}},
    )
    assert rest.status == 404
    assert _snapshot_names(state, "deployments") == before


def test_nameless_scale_is_a_usage_error(amc, tmp_path):
    state = _state(amc, tmp_path)
    result = server.run_command(
        state,
        command="kubectl scale deployment --replicas=3 -n saas-prod",
    )["result"]
    assert result["exit_code"] != 0
    assert "no name was specified" in result["stderr"]
    assert result["matched_rule_id"] == "kubectl.scale.usage"


def test_scale_real_deployment_still_succeeds(amc, tmp_path):
    """Positive control: the existence guard must not break the success path."""
    state = _state(amc, tmp_path)
    result = server.run_command(
        state,
        command="kubectl scale deployment/apigateway --replicas=4 -n saas-prod",
    )["result"]
    assert result["exit_code"] == 0
    assert result["stdout"] == "deployment.apps/apigateway scaled\n"
    gateway = next(
        item for item in server.resource_snapshot(state)["deployments"]
        if item["name"] == "apigateway"
    )
    assert gateway["ready"] == "4/4"


# --------------------------------------------------------------------------- #
# A-014 — otel_status concurrent writers/readers are lock-safe
# --------------------------------------------------------------------------- #


def test_otel_status_concurrent_access_is_lock_safe(amc, tmp_path):
    state = _state(amc, tmp_path)
    workers = 8
    bumps_per_worker = 200
    errors: list[Exception] = []
    # Parties = 8 bump threads + 1 read thread + this main thread.
    barrier = threading.Barrier(workers + 2)

    def bump():
        try:
            barrier.wait()
            for _ in range(bumps_per_worker):
                state.bump_otel_status("stream_batches")
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    def read():
        try:
            barrier.wait()
            for _ in range(bumps_per_worker):
                # Snapshot + summary iterate the dict; a concurrent unlocked
                # write would raise "dictionary changed size during iteration".
                state.otel_status_snapshot()
                state.summary()
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=bump) for _ in range(workers)]
    threads.append(threading.Thread(target=read))
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert errors == []
    assert state.otel_status_snapshot()["stream_batches"] == workers * bumps_per_worker


# --------------------------------------------------------------------------- #
# A-015 — a failing background regen reloads published anomalies.csv
# --------------------------------------------------------------------------- #


def test_failing_regen_reloads_published_anomalies_from_disk(amc, tmp_path):
    state = _state(amc, tmp_path, generate=True)
    # The published anomalies.csv exists on disk from build_state's generation.
    disk_rows = server.load_anomaly_rows(tmp_path / "anomalies.csv")
    assert disk_rows, "fixture must have published a non-empty anomalies.csv"

    # Simulate a stale in-memory copy diverging from disk.
    state.replace_generated_rows([])
    assert state.generated_rows() == []

    def boom():
        raise RuntimeError("regen exploded after publishing anomalies.csv")

    try:
        boom()
    except RuntimeError as exc:
        server._record_continuous_generation_failure(state, exc)

    # The failure arm reloaded the on-disk rows into state.
    assert state.generated_rows() == disk_rows
    assert state.generation.last_error and "regen exploded" in state.generation.last_error
    assert state.generation.thread == "failed"


# --------------------------------------------------------------------------- #
# A-016 — a zero-byte (headerless) per-component CSV degrades gracefully
# --------------------------------------------------------------------------- #


def test_iter_component_rows_empty_csv_warns_and_yields_nothing(amc, tmp_path, capsys):
    empty = tmp_path / "apigateway.csv"
    empty.write_text("")

    rows = list(amc._iter_component_rows("apigateway", empty))

    assert rows == []
    assert "no header row" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# A-017 — list() clamps a negative/zero limit identically on both backends
# --------------------------------------------------------------------------- #


def _record_traces(store, count):
    for index in range(count):
        store.record(
            server.CommandTrace(
                id=store.next_id(),
                received_at_wall_time=f"2026-06-25T12:0{index}:00Z",
                simulated_time=f"2026-06-25T12:0{index}:00Z",
                raw_input="kubectl get pods -n saas-prod",
                argv=("kubectl", "get", "pods", "-n", "saas-prod"),
                client="test",
                command_family="kubectl",
                verb="get",
                resource_kind="pods",
                resource_name="",
                namespace="saas-prod",
                parsed_flags={"namespace": "saas-prod"},
                support_status="supported",
                matched_rule_id="kubectl.get.pods",
                active_scenarios=("cache_leak_restart",),
                exit_code=0,
                stdout_preview="",
                stderr_preview="",
                stdout="",
                stderr="",
                latency_ms=0.1,
                fingerprint="kubectl get pods",
                guessed_intent="inspect pods",
                request_id="",
            )
        )


@pytest.mark.parametrize("limit", [-1, -5, 0])
def test_list_negative_or_zero_limit_agrees_across_backends(tmp_path, limit):
    memory = server.CommandTraceStore()
    sqlite = server.CommandTraceStore(sqlite_path=tmp_path / "traces.db")
    _record_traces(memory, 3)
    _record_traces(sqlite, 3)

    assert memory.list(limit=limit) == []
    assert sqlite.list(limit=limit) == []


def test_list_positive_limit_returns_same_count_across_backends(tmp_path):
    memory = server.CommandTraceStore()
    sqlite = server.CommandTraceStore(sqlite_path=tmp_path / "traces.db")
    _record_traces(memory, 3)
    _record_traces(sqlite, 3)

    assert len(memory.list(limit=2)) == 2
    assert len(sqlite.list(limit=2)) == 2

"""Output directory hygiene: pre-clean stale artifacts when re-running into
the same --output-dir with a different --emit-selection or --components."""
from conftest import run_capture


def _run(amc, out_dir, *, extra_args):
    """run_capture wrapper that always uses days=1 and forces a short run."""
    return run_capture(
        amc, out_dir, days=1, extra_args=list(extra_args)
    )


def test_metrics_only_after_full_run_clears_logs_and_traces(amc, tmp_path):
    _run(amc, tmp_path, extra_args=["--emit-selection", "metrics,logs,traces"])
    assert (tmp_path / "metric_report.log").exists()
    assert (tmp_path / "metric_traces.jsonl").exists()

    _run(amc, tmp_path, extra_args=["--emit-selection", "metrics"])
    assert not (tmp_path / "metric_report.log").exists()
    assert not (tmp_path / "metric_traces.jsonl").exists()
    assert (tmp_path / "anomalies.csv").exists()
    # Component CSVs from the second metrics run are present.
    for component in amc.COMPONENTS:
        assert (tmp_path / f"{component}.csv").exists()


def test_logs_traces_after_metrics_run_clears_component_csvs(amc, tmp_path):
    _run(amc, tmp_path, extra_args=["--emit-selection", "metrics"])
    # Sanity: component CSVs and manifest exist after the metrics run.
    assert (tmp_path / "anomalies.csv").exists()
    for component in amc.COMPONENTS:
        assert (tmp_path / f"{component}.csv").exists()

    _run(amc, tmp_path, extra_args=["--emit-selection", "logs,traces"])
    for component in amc.COMPONENTS:
        assert not (tmp_path / f"{component}.csv").exists(), (
            f"stale {component}.csv survived the logs,traces re-run"
        )
    assert not (tmp_path / "anomalies.csv").exists()
    assert (tmp_path / "metric_report.log").exists()
    assert (tmp_path / "metric_traces.jsonl").exists()


def test_narrowed_components_clears_dropped_csvs(amc, tmp_path):
    pair = list(amc.COMPONENTS)[:2]
    keep = pair[0]
    drop = pair[1]
    _run(amc, tmp_path, extra_args=[
        "--emit-selection", "metrics",
        "--components", ",".join(pair),
    ])
    assert (tmp_path / f"{keep}.csv").exists()
    assert (tmp_path / f"{drop}.csv").exists()

    _run(amc, tmp_path, extra_args=[
        "--emit-selection", "metrics",
        "--components", keep,
    ])
    assert (tmp_path / f"{keep}.csv").exists()
    assert not (tmp_path / f"{drop}.csv").exists(), (
        f"{drop}.csv should have been pre-cleaned when --components was narrowed"
    )


def test_drop_combine_clears_unified(amc, tmp_path):
    _run(amc, tmp_path, extra_args=["--emit-selection", "metrics", "--combine"])
    assert (tmp_path / "combined_metrics_unified.csv").exists()

    _run(amc, tmp_path, extra_args=["--emit-selection", "metrics"])
    assert not (tmp_path / "combined_metrics_unified.csv").exists(), (
        "combined_metrics_unified.csv from a prior --combine run should be "
        "pre-cleaned when --combine is not set on the next run"
    )


def test_status_line_only_names_emitted_artifacts(amc, tmp_path, capsys):
    _run(amc, tmp_path, extra_args=["--emit-selection", "logs,traces"])
    captured = capsys.readouterr()
    done_lines = [
        line for line in captured.out.splitlines() if line.startswith("Done -")
    ]
    assert len(done_lines) == 1, f"expected exactly one Done line, got: {done_lines}"
    done = done_lines[0]
    assert "metric_report.log" in done
    assert "metric_traces.jsonl" in done
    assert "anomalies.csv" not in done
    assert "component CSV" not in done

    other = tmp_path / "metrics_only"
    other.mkdir()
    _run(amc, other, extra_args=["--emit-selection", "metrics"])
    captured = capsys.readouterr()
    done_lines = [
        line for line in captured.out.splitlines() if line.startswith("Done -")
    ]
    assert len(done_lines) == 1
    done = done_lines[0]
    assert "anomalies.csv" in done
    assert "metric_report.log" not in done
    assert "metric_traces.jsonl" not in done


def test_logs_only_after_full_run_clears_traces_and_component_csvs(amc, tmp_path):
    _run(amc, tmp_path, extra_args=["--emit-selection", "metrics,logs,traces"])
    assert (tmp_path / "metric_report.log").exists()
    assert (tmp_path / "metric_traces.jsonl").exists()

    _run(amc, tmp_path, extra_args=["--emit-selection", "logs"])
    assert (tmp_path / "metric_report.log").exists()
    assert not (tmp_path / "metric_traces.jsonl").exists(), (
        "logs-only re-run should drop the prior metric_traces.jsonl"
    )
    assert not (tmp_path / "anomalies.csv").exists()
    for component in amc.COMPONENTS:
        assert not (tmp_path / f"{component}.csv").exists(), (
            f"logs-only re-run should drop the prior {component}.csv"
        )


def test_traces_only_after_full_run_clears_logs_and_component_csvs(amc, tmp_path):
    _run(amc, tmp_path, extra_args=["--emit-selection", "metrics,logs,traces"])
    assert (tmp_path / "metric_report.log").exists()
    assert (tmp_path / "metric_traces.jsonl").exists()

    _run(amc, tmp_path, extra_args=["--emit-selection", "traces"])
    assert (tmp_path / "metric_traces.jsonl").exists()
    assert not (tmp_path / "metric_report.log").exists(), (
        "traces-only re-run should drop the prior metric_report.log"
    )
    assert not (tmp_path / "anomalies.csv").exists()
    for component in amc.COMPONENTS:
        assert not (tmp_path / f"{component}.csv").exists(), (
            f"traces-only re-run should drop the prior {component}.csv"
        )


def test_pre_clean_leaves_unknown_files_alone(amc, tmp_path):
    _run(amc, tmp_path, extra_args=["--emit-selection", "metrics"])
    sentinel = tmp_path / "user_notes.txt"
    sentinel.write_text("user-provided extra file; do not delete")

    _run(amc, tmp_path, extra_args=["--emit-selection", "logs,traces"])
    assert sentinel.exists(), (
        "pre-clean should leave user-provided files in --output-dir alone"
    )
    assert sentinel.read_text() == "user-provided extra file; do not delete"


def test_no_metric_formatting_work_when_metrics_not_emitted(amc, tmp_path, monkeypatch):
    """``emit_metrics=False`` must skip the fixed-3 CSV string formatting
    entirely — the formatted buffers exist only to produce CSV bytes, and
    the formatting historically dominated generation runtime (~80% per
    the comment in ``generate_component``). Before the hoist, a
    ``--emit-selection logs`` run paid the full cost and threw the
    result away.
    """
    import numpy as np

    calls = {"n": 0}
    real_format = amc._format_fixed3

    def counting_format(arr):
        calls["n"] += 1
        return real_format(arr)

    monkeypatch.setattr(amc, "_format_fixed3", counting_format)
    specs = [amc.MetricSpec(name="m0", base=10.0, std=0.0)]
    ts_array, ts_strings = amc._build_timestamp_arrays(10, 1.0)

    amc.generate_component(
        "comp_fmt_off", specs, [],
        base_dir=tmp_path, total_seconds=10, drop_rate=0.0, interval=1.0,
        ts_array=ts_array, ts_strings=ts_strings,
        ctx=amc.RunContext(rng=np.random.RandomState(42)),
        emit_metrics=False,
    )
    assert calls["n"] == 0, (
        "emit_metrics=False must not invoke the CSV cell formatter"
    )
    assert not (tmp_path / "comp_fmt_off.csv").exists()

    amc.generate_component(
        "comp_fmt_on", specs, [],
        base_dir=tmp_path, total_seconds=10, drop_rate=0.0, interval=1.0,
        ts_array=ts_array, ts_strings=ts_strings,
        ctx=amc.RunContext(rng=np.random.RandomState(42)),
        emit_metrics=True,
    )
    assert calls["n"] > 0, "emit_metrics=True must still format CSV cells"
    assert (tmp_path / "comp_fmt_on.csv").exists()

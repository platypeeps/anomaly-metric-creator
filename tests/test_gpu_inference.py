import contextlib
import csv
import io


GPU_INFERENCE_HEADER = [
    "timestamp",
    "batch_size",
    "model_size_b",
    "gpu_memory_pressure",
    "kv_cache_usage",
    "memory_fragmentation",
    "gpu_utilization",
    "throughput_tps",
    "latency_p50_ms",
    "latency_p99_ms",
    "failure",
]


def _run_default_gpu_inference(amc, out_dir):
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        amc.main([
            "--seed", "42",
            "--drop-rate", "0",
            "--components", "gpu_inference",
            "--output-dir", str(out_dir),
        ])
    return stderr.getvalue()


def test_default_gpu_inference_shape_matches_reference_csv(amc, tmp_path):
    _run_default_gpu_inference(amc, tmp_path)

    with open(tmp_path / "gpu_inference.csv", newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        rows = sum(1 for row in reader if row)

    assert header == GPU_INFERENCE_HEADER
    assert rows == amc.DEFAULT_ROW_COUNT == 50_000


def test_gpu_inference_fragmentation_failure_pulses(amc, tmp_path):
    _run_default_gpu_inference(amc, tmp_path)

    with open(tmp_path / "gpu_inference.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    failure_rows = [row for row in rows if float(row["failure"]) == 1.0]

    assert len(failure_rows) == 8
    assert all(float(row["memory_fragmentation"]) >= 0.82 for row in failure_rows)
    assert all(float(row["gpu_memory_pressure"]) >= 0.90 for row in failure_rows)
    assert all(float(row["latency_p99_ms"]) >= 900.0 for row in failure_rows)
    assert all(float(row["throughput_tps"]) == 0.0 for row in failure_rows)

    with open(tmp_path / "anomalies.csv", newline="", encoding="utf-8") as fh:
        anomalies = list(csv.DictReader(fh))
    failure_anomalies = [
        row for row in anomalies
        if row["scenario_id"] == "gpu_inference_fragmentation"
        and row["metric"] == "failure"
    ]
    assert len(failure_anomalies) == 8

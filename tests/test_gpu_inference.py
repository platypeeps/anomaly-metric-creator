import contextlib
import csv
import io
import statistics


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


def _failure_run_lengths(rows):
    lengths = []
    current = 0
    for row in rows:
        if float(row["failure"]) == 1.0:
            current += 1
        elif current:
            lengths.append(current)
            current = 0
    if current:
        lengths.append(current)
    return lengths


def test_gpu_inference_fragmentation_reference_like_signal(amc, tmp_path):
    _run_default_gpu_inference(amc, tmp_path)

    with open(tmp_path / "gpu_inference.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    failure_rows = [row for row in rows if float(row["failure"]) == 1.0]
    failure_count = len(failure_rows)
    failure_runs = _failure_run_lengths(rows)

    assert failure_count == 1_204
    assert len(failure_runs) == 1_172
    assert max(failure_runs) == 2
    assert statistics.mean(length == 1 for length in failure_runs) > 0.97

    def precision_for(predicate):
        matched = [row for row in rows if predicate(row)]
        true_positive = [
            row for row in matched if float(row["failure"]) == 1.0
        ]
        return len(matched), len(true_positive), len(true_positive) / len(matched)

    frag_rows, frag_failures, frag_precision = precision_for(
        lambda row: float(row["memory_fragmentation"]) >= 0.8
    )
    pressure_rows, pressure_failures, pressure_precision = precision_for(
        lambda row: float(row["gpu_memory_pressure"]) >= 0.9
    )
    p99_rows, p99_failures, p99_precision = precision_for(
        lambda row: float(row["latency_p99_ms"]) >= 900.0
    )

    assert 700 <= frag_rows <= 800
    assert frag_failures == 229
    assert 0.28 <= frag_precision <= 0.34
    assert 3_600 <= pressure_rows <= 3_900
    assert pressure_failures == 179
    assert 0.04 <= pressure_precision <= 0.06
    assert p99_rows == 350
    assert p99_failures == 22
    assert 0.05 <= p99_precision <= 0.08

    with open(tmp_path / "anomalies.csv", newline="", encoding="utf-8") as fh:
        anomalies = list(csv.DictReader(fh))
    failure_anomalies = [
        row for row in anomalies
        if row["scenario_id"] == "gpu_inference_fragmentation"
        and row["metric"] == "failure"
    ]
    assert len(failure_anomalies) == failure_count

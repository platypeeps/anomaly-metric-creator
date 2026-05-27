import contextlib
import csv
import io
import re
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


def _column(rows, name):
    return [float(row[name]) for row in rows]


def _pearson(xs, ys):
    x_mean = statistics.mean(xs)
    y_mean = statistics.mean(ys)
    x_delta = [x - x_mean for x in xs]
    y_delta = [y - y_mean for y in ys]
    numerator = sum(x * y for x, y in zip(x_delta, y_delta))
    denominator = (
        sum(x * x for x in x_delta) * sum(y * y for y in y_delta)
    ) ** 0.5
    return numerator / denominator


def _max_rolling_sum(values, window):
    current = sum(values[:window])
    best = current
    for idx in range(window, len(values)):
        current += values[idx] - values[idx - window]
        best = max(best, current)
    return best


def test_gpu_inference_fragmentation_reference_like_signal(amc, tmp_path):
    _run_default_gpu_inference(amc, tmp_path)

    with open(tmp_path / "gpu_inference.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    failure_rows = [row for row in rows if float(row["failure"]) == 1.0]
    failure_count = len(failure_rows)
    failure_runs = _failure_run_lengths(rows)

    assert failure_count == 1_204
    assert rows.index(failure_rows[0]) <= 10
    assert rows.index(failure_rows[-1]) >= 49_000
    assert len(failure_runs) == 1_124
    assert max(failure_runs) == 2
    assert statistics.mean(length == 1 for length in failure_runs) > 0.92

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
    assert 400 <= frag_failures <= 440
    assert 0.54 <= frag_precision <= 0.59
    assert 3_600 <= pressure_rows <= 3_900
    assert 500 <= pressure_failures <= 540
    assert 0.13 <= pressure_precision <= 0.15
    assert p99_rows == 420
    assert 300 <= p99_failures <= 340
    assert 0.74 <= p99_precision <= 0.78

    frag_pressure_rows, frag_pressure_failures, frag_pressure_precision = (
        precision_for(
            lambda row: (
                float(row["memory_fragmentation"]) >= 0.8
                and float(row["gpu_memory_pressure"]) >= 0.9
            )
        )
    )
    assert 600 <= frag_pressure_rows <= 720
    assert 380 <= frag_pressure_failures <= 430
    assert 0.52 <= frag_pressure_precision <= 0.58

    failures = _column(rows, "failure")
    high_risk = [
        float(
            float(row["memory_fragmentation"]) >= 0.8
            or float(row["gpu_memory_pressure"]) >= 0.9
            or float(row["gpu_utilization"]) <= 0.65
            or float(row["throughput_tps"]) <= 1.0
            or float(row["latency_p99_ms"]) >= 900.0
        )
        for row in rows
    ]
    strict_high_risk = [
        float(
            float(row["memory_fragmentation"]) >= 0.8
            and float(row["gpu_memory_pressure"]) >= 0.9
            and float(row["gpu_utilization"]) <= 0.65
        )
        for row in rows
    ]
    all_signal_high_risk = [
        float(
            float(row["memory_fragmentation"]) >= 0.8
            and float(row["gpu_memory_pressure"]) >= 0.9
            and float(row["gpu_utilization"]) <= 0.65
            and float(row["throughput_tps"]) <= 1.0
            and float(row["latency_p99_ms"]) >= 900.0
        )
        for row in rows
    ]
    p99_high = [
        float(float(row["latency_p99_ms"]) >= 900.0)
        for row in rows
    ]
    decile_size = len(rows) // 10
    decile_failures = [
        sum(failures[decile * decile_size:(
            len(rows) if decile == 9 else (decile + 1) * decile_size
        )])
        for decile in range(10)
    ]
    assert max(decile_failures) >= 700
    assert _max_rolling_sum(failures, 360) >= 200
    assert _max_rolling_sum(high_risk, 360) == 360
    assert _max_rolling_sum(strict_high_risk, 360) >= 250
    assert _max_rolling_sum(all_signal_high_risk, 360) >= 150
    assert _max_rolling_sum(p99_high, 360) >= 150

    with open(tmp_path / "anomalies.csv", newline="", encoding="utf-8") as fh:
        anomalies = list(csv.DictReader(fh))
    failure_anomalies = [
        row for row in anomalies
        if row["scenario_id"] == "gpu_inference_fragmentation"
        and row["metric"] == "failure"
    ]
    assert len(failure_anomalies) == failure_count


def test_gpu_inference_failure_labels_are_single_tick_specs(amc):
    primary_specs, _ = amc._gpu_inference_fragmentation_specs()
    failure_specs = [
        spec for component, spec in primary_specs
        if component == "gpu_inference" and spec["metric"] == "failure"
    ]

    assert len(failure_specs) == 1_204
    assert all("duration_seconds" not in spec for spec in failure_specs)
    assert all(spec.get("shape", "step") == "step" for spec in failure_specs)


def test_gpu_inference_out_of_range_warning_uses_fractional_duration(amc, tmp_path):
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        amc.main([
            "--seed", "42",
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--drop-rate", "0",
            "--components", "gpu_inference",
            "--scenarios", "gpu_inference_fragmentation",
            "--output-dir", str(tmp_path),
        ])

    warning = stderr.getvalue()
    match = re.search(r"Run with --duration-days ([0-9.]+) to include them", warning)
    assert match, warning
    suggested_days = float(match.group(1))
    assert 1.0 < suggested_days < 35.0

    scenario = amc.SCENARIOS["gpu_inference_fragmentation"]
    max_start_idx = max(
        int(round(spec["time_offset"] / 60.0))
        for component, spec in scenario.primary_specs
        if component == "gpu_inference"
        and spec["time_offset"] >= amc.SECONDS_PER_DAY
    )
    suggested_rows = int(
        (suggested_days * amc.SECONDS_PER_DAY) // amc.DEFAULT_INTERVAL_SECONDS
    )
    assert suggested_rows > max_start_idx


def test_gpu_inference_metrics_move_together_like_serving_stress(amc, tmp_path):
    _run_default_gpu_inference(amc, tmp_path)

    with open(tmp_path / "gpu_inference.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    memory_fragmentation = _column(rows, "memory_fragmentation")
    gpu_memory_pressure = _column(rows, "gpu_memory_pressure")
    gpu_utilization = _column(rows, "gpu_utilization")
    kv_cache_usage = _column(rows, "kv_cache_usage")
    latency_p99_ms = _column(rows, "latency_p99_ms")
    throughput_tps = _column(rows, "throughput_tps")

    assert _pearson(memory_fragmentation, gpu_memory_pressure) > 0.45
    assert _pearson(memory_fragmentation, gpu_utilization) < -0.45
    assert _pearson(memory_fragmentation, kv_cache_usage) > 0.35
    assert _pearson(latency_p99_ms, kv_cache_usage) > 0.35
    assert _pearson(throughput_tps, latency_p99_ms) < -0.15

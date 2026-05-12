import csv
import json

from conftest import read_manifest


def _parse_log_event_id(line: str) -> str:
    for token in line.split():
        if token.startswith("event_id="):
            return token.split("=", 1)[1]
    raise AssertionError(f"event_id missing from log line: {line}")


def test_reporting_artifacts_align_with_manifest(one_day_run_a):
    out_dir = one_day_run_a.out_dir
    manifest = read_manifest(out_dir)

    with open(out_dir / "metric_report.log") as f:
        log_lines = [line.rstrip("\n") for line in f if line.strip()]

    with open(out_dir / "metric_traces.jsonl") as f:
        traces = [json.loads(line) for line in f if line.strip()]

    assert len(log_lines) == len(manifest)
    assert len(traces) == len(manifest)

    for idx, row in enumerate(manifest):
        log_line = log_lines[idx]
        trace = traces[idx]

        event_id = _parse_log_event_id(log_line)
        assert row["timestamp"] in log_line
        assert f"component={row['component']}" in log_line
        assert f"metric={row['metric']}" in log_line

        assert trace["timestamp"] == row["timestamp"]
        assert trace["component"] == row["component"]
        assert trace["metric"] == row["metric"]
        assert trace["description"] == row["description"]
        assert trace["event_id"] == event_id
        assert trace["trace_id"].startswith("trace_")
        assert trace["span_id"].startswith("span_")


def test_write_reporting_artifacts_rejects_missing_required_fields(amc, tmp_path):
    with open(tmp_path / "anomalies.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "component", "metric", "description"])
        writer.writeheader()

    bad_entry = {
        "timestamp": "2026-03-10 00:00:00",
        "component": "authservice",
        "metric": "error_rate",
        "description": "",
    }

    try:
        amc.write_reporting_artifacts(tmp_path, [bad_entry])
    except ValueError as exc:
        assert "missing required field" in str(exc)
    else:
        raise AssertionError("expected ValueError for malformed anomaly entry")

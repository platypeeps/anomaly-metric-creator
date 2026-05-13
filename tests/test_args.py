import pytest
from pathlib import Path

def test_parse_args_defaults(amc):
    args = amc.parse_args(["--output-dir", "test_out"])
    assert args.duration_days == 1
    assert args.seed == 42
    assert args.interval_seconds == 1.0
    assert args.drop_rate == 0.0005
    assert args.output_dir == Path("test_out")

def test_parse_args_custom(amc):
    args = amc.parse_args([
        "--duration-days", "2",
        "--seed", "99",
        "--interval-seconds", "10",
        "--drop-rate", "0.01",
        "--output-dir", "custom_out"
    ])
    assert args.duration_days == 2
    assert args.seed == 99
    assert args.interval_seconds == 10.0
    assert args.drop_rate == 0.01
    assert args.output_dir == Path("custom_out")

@pytest.mark.parametrize("flag, value", [
    ("--duration-days", "0"),
    ("--duration-days", "-1"),
    ("--drop-rate", "-0.1"),
    ("--drop-rate", "1.1"),
    ("--interval-seconds", "0"),
    ("--interval-seconds", "-5"),
])
def test_parse_args_invalid_values(amc, flag, value):
    with pytest.raises(SystemExit):
        amc.parse_args([flag, value, "--output-dir", "test_out"])

def test_parse_args_emit_selection(amc):
    args = amc.parse_args(["--emit-selection", "metrics,logs", "--output-dir", "test_out"])
    assert args.emit_selection == {"metrics", "logs"}

def test_parse_args_invalid_emit_selection(amc):
    with pytest.raises(SystemExit):
        amc.parse_args(["--emit-selection", "invalid", "--output-dir", "test_out"])


def test_parse_args_otel_enabled_default_off(amc):
    args = amc.parse_args(["--output-dir", "test_out"])
    assert args.otel_enabled is False


def test_parse_args_otel_enabled_explicit_on(amc):
    args = amc.parse_args([
        "--otel-enabled",
        "--otel-logs-endpoint", "http://localhost:4318/v1/logs",
        "--output-dir", "test_out",
    ])
    assert args.otel_enabled is True


def test_parse_args_otel_disabled_explicit_off(amc):
    args = amc.parse_args([
        "--otel-disabled",
        "--otel-logs-endpoint", "http://localhost:4318/v1/logs",
        "--output-dir", "test_out",
    ])
    assert args.otel_enabled is False


def test_parse_args_otel_enabled_without_any_endpoint_fails(amc):
    with pytest.raises(SystemExit):
        amc.parse_args(["--otel-enabled", "--output-dir", "test_out"])


def test_parse_args_otel_activity_log_default(amc):
    args = amc.parse_args(["--output-dir", "test_out"])
    assert args.otel_activity_log == Path("otel-activity.log")


def test_parse_args_otel_activity_log_custom(amc):
    args = amc.parse_args([
        "--otel-activity-log", "/tmp/custom-activity.log",
        "--output-dir", "test_out",
    ])
    assert args.otel_activity_log == Path("/tmp/custom-activity.log")

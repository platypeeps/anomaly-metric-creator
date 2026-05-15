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


def test_parse_args_otel_verbose_default_off(amc):
    args = amc.parse_args(["--output-dir", "test_out"])
    assert args.otel_verbose is False


def test_parse_args_otel_verbose_explicit_on(amc):
    args = amc.parse_args([
        "--otel-verbose",
        "--output-dir", "test_out",
    ])
    assert args.otel_verbose is True


def test_parse_args_otel_no_verbose_explicit_off(amc):
    args = amc.parse_args([
        "--no-otel-verbose",
        "--output-dir", "test_out",
    ])
    assert args.otel_verbose is False


def test_parse_args_components_default_is_all(amc):
    args = amc.parse_args(["--output-dir", "test_out"])
    assert args.components == set(amc.COMPONENTS.keys())


def test_parse_args_components_single(amc):
    args = amc.parse_args(["--components", "authservice", "--output-dir", "test_out"])
    assert args.components == {"authservice"}


def test_parse_args_components_multiple(amc):
    args = amc.parse_args([
        "--components", "authservice,database,apigateway",
        "--output-dir", "test_out",
    ])
    assert args.components == {"authservice", "database", "apigateway"}


def test_parse_args_components_whitespace_tolerant(amc):
    args = amc.parse_args([
        "--components", " authservice , database ",
        "--output-dir", "test_out",
    ])
    assert args.components == {"authservice", "database"}


def test_parse_args_components_invalid_name_fails(amc):
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--components", "authservice,not_a_component",
            "--output-dir", "test_out",
        ])


def test_parse_args_components_empty_fails(amc):
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--components", "",
            "--output-dir", "test_out",
        ])


def test_parse_args_components_all_keyword(amc):
    args = amc.parse_args(["--components", "all", "--output-dir", "test_out"])
    assert args.components == set(amc.COMPONENTS.keys())


def test_parse_args_signal_level_default_medium(amc):
    args = amc.parse_args(["--output-dir", "test_out"])
    assert args.signal_level == "medium"


@pytest.mark.parametrize("level", ["low", "medium", "high"])
def test_parse_args_signal_level_explicit(amc, level):
    args = amc.parse_args(["--signal-level", level, "--output-dir", "test_out"])
    assert args.signal_level == level


def test_parse_args_signal_level_invalid_fails(amc):
    with pytest.raises(SystemExit):
        amc.parse_args(["--signal-level", "extreme", "--output-dir", "test_out"])


def test_parse_args_signal_level_case_insensitive(amc):
    args = amc.parse_args(["--signal-level", "HIGH", "--output-dir", "test_out"])
    assert args.signal_level == "high"


def test_parse_args_anomaly_count_default_unlimited(amc):
    args = amc.parse_args(["--output-dir", "test_out"])
    assert args.anomaly_count is None


def test_parse_args_anomaly_count_explicit(amc):
    args = amc.parse_args(["--anomaly-count", "7", "--output-dir", "test_out"])
    assert args.anomaly_count == 7


@pytest.mark.parametrize("value", ["0", "-1"])
def test_parse_args_anomaly_count_non_positive_fails(amc, value):
    with pytest.raises(SystemExit):
        amc.parse_args(["--anomaly-count", value, "--output-dir", "test_out"])


def test_parse_args_metrics_per_component_default_is_none(amc):
    args = amc.parse_args(["--output-dir", "test_out"])
    assert args.metrics_per_component is None


@pytest.mark.parametrize("value", ["1", "3", "5", "10"])
def test_parse_args_metrics_per_component_explicit_valid(amc, value):
    args = amc.parse_args(["--metrics-per-component", value, "--output-dir", "test_out"])
    assert args.metrics_per_component == int(value)


@pytest.mark.parametrize("value", ["0", "-1", "11", "100"])
def test_parse_args_metrics_per_component_out_of_range_fails(amc, value):
    with pytest.raises(SystemExit):
        amc.parse_args(["--metrics-per-component", value, "--output-dir", "test_out"])


def test_parse_args_scenarios_default_is_all(amc):
    args = amc.parse_args(["--output-dir", "test_out"])
    assert args.scenarios == set(amc.SCENARIOS.keys())


def test_parse_args_scenarios_all_keyword(amc):
    args = amc.parse_args(["--scenarios", "all", "--output-dir", "test_out"])
    assert args.scenarios == set(amc.SCENARIOS.keys())


def test_parse_args_scenarios_single(amc):
    args = amc.parse_args([
        "--scenarios", "cache_leak_restart",
        "--output-dir", "test_out",
    ])
    assert args.scenarios == {"cache_leak_restart"}


def test_parse_args_scenarios_multiple(amc):
    args = amc.parse_args([
        "--scenarios", "cache_leak_restart,jwks_rotation_chaos",
        "--output-dir", "test_out",
    ])
    assert args.scenarios == {"cache_leak_restart", "jwks_rotation_chaos"}


def test_parse_args_scenarios_whitespace_tolerant(amc):
    args = amc.parse_args([
        "--scenarios", " cache_leak_restart , db_disk_exhaustion ",
        "--output-dir", "test_out",
    ])
    assert args.scenarios == {"cache_leak_restart", "db_disk_exhaustion"}


def test_parse_args_scenarios_case_insensitive(amc):
    args = amc.parse_args([
        "--scenarios", "CACHE_LEAK_RESTART",
        "--output-dir", "test_out",
    ])
    assert args.scenarios == {"cache_leak_restart"}


def test_parse_args_scenarios_invalid_name_fails(amc):
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--scenarios", "cache_leak_restart,not_a_scenario",
            "--output-dir", "test_out",
        ])


def test_parse_args_scenarios_empty_fails(amc):
    with pytest.raises(SystemExit):
        amc.parse_args(["--scenarios", "", "--output-dir", "test_out"])


def test_parse_args_exclude_scenarios_default_empty(amc):
    args = amc.parse_args(["--output-dir", "test_out"])
    assert args.exclude_scenarios == set()


def test_parse_args_exclude_scenarios_single(amc):
    args = amc.parse_args([
        "--exclude-scenarios", "jwks_rotation_chaos",
        "--output-dir", "test_out",
    ])
    assert args.exclude_scenarios == {"jwks_rotation_chaos"}


def test_parse_args_exclude_scenarios_case_insensitive(amc):
    args = amc.parse_args([
        "--exclude-scenarios", "JWKS_ROTATION_CHAOS",
        "--output-dir", "test_out",
    ])
    assert args.exclude_scenarios == {"jwks_rotation_chaos"}


def test_parse_args_exclude_scenarios_invalid_name_fails(amc):
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--exclude-scenarios", "not_a_scenario",
            "--output-dir", "test_out",
        ])

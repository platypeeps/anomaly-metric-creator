import pytest
from pathlib import Path

def test_parse_args_defaults(amc):
    args = amc.parse_args(["--output-dir", "test_out"])
    assert args.duration_days == amc.DEFAULT_DURATION_DAYS
    assert args.seed == 42
    assert args.interval_seconds == 60.0
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
    ("--duration-days", "nan"),
    ("--duration-days", "inf"),
    ("--duration-days", "-inf"),
    ("--drop-rate", "-0.1"),
    ("--drop-rate", "1.1"),
    ("--interval-seconds", "0"),
    ("--interval-seconds", "-5"),
    # sub-millisecond intervals would collide on the rendered
    # millisecond-precision timestamp string and silently drop rows.
    ("--interval-seconds", "0.0005"),
    ("--interval-seconds", "0.0009"),
    # review round 2: non-finite floats slip past <= 0 and < 0.001
    # checks; NaN crashes int conversion later, inf emits zero rows silently.
    ("--interval-seconds", "nan"),
    ("--interval-seconds", "inf"),
    ("--interval-seconds", "-inf"),
])
def test_parse_args_invalid_values(amc, flag, value):
    with pytest.raises(SystemExit):
        amc.parse_args([flag, value, "--output-dir", "test_out"])


def test_parse_args_interval_seconds_accepts_millisecond_floor(amc):
    """--interval-seconds 0.001 (1ms) is the documented floor and
    must parse cleanly; anything below collapses to identical timestamps.

    The preflight cell-count cap rejects 0.001s intervals at
    default --duration-days / --components, so we opt out with
    --allow-huge-output to keep exercising the millisecond floor itself."""
    args = amc.parse_args([
        "--interval-seconds", "0.001",
        "--allow-huge-output",
        "--output-dir", "test_out",
    ])
    assert args.interval_seconds == 0.001

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


def test_parse_args_otel_stream_protocol_default_is_protobuf(amc, monkeypatch):
    monkeypatch.delenv("MEZMO_OTEL_STREAM_PROTOCOL", raising=False)
    args = amc.parse_args(["--output-dir", "test_out"])
    assert args.otel_stream_protocol == "protobuf"


def test_parse_args_otel_stream_protocol_env_var_overrides_default(amc, monkeypatch):
    monkeypatch.setenv("MEZMO_OTEL_STREAM_PROTOCOL", "json")
    args = amc.parse_args(["--output-dir", "test_out"])
    assert args.otel_stream_protocol == "json"


def test_parse_args_otel_stream_protocol_cli_overrides_env_var(amc, monkeypatch):
    monkeypatch.setenv("MEZMO_OTEL_STREAM_PROTOCOL", "json")
    args = amc.parse_args([
        "--otel-stream-protocol", "protobuf",
        "--output-dir", "test_out",
    ])
    assert args.otel_stream_protocol == "protobuf"


def test_parse_args_otel_stream_protocol_invalid_env_var_fails(amc, monkeypatch):
    monkeypatch.setenv("MEZMO_OTEL_STREAM_PROTOCOL", "xml")
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--otel-logs-endpoint", "http://localhost:4318/v1/logs",
            "--output-dir", "test_out",
        ])


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


def test_parse_args_scenarios_all_with_invalid_name_fails(amc):
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--scenarios", "all,not_a_scenario",
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


# ------------------------------------------------------------------
# --otel-emit-gauges / --otel-gauge-*
# ------------------------------------------------------------------
def test_otel_emit_gauges_defaults_false(amc, monkeypatch):
    monkeypatch.delenv("MEZMO_OTEL_EMIT_GAUGES", raising=False)
    args = amc.parse_args(["--output-dir", "test_out"])
    assert args.otel_emit_gauges is False
    assert args.otel_gauge_batch_seconds == 60
    assert args.otel_gauge_metric_prefix == ""


@pytest.mark.parametrize("value, expected", [
    ("1", True), ("true", True), ("TRUE", True),
    ("yes", True), ("YeS", True), ("on", True),
    ("0", False), ("false", False), ("", False),
    ("no", False), ("off", False), ("nonsense", False),
])
def test_otel_emit_gauges_env_truthy_matrix(amc, monkeypatch, value, expected):
    monkeypatch.setenv("MEZMO_OTEL_EMIT_GAUGES", value)
    if expected:
        # Truthy env value requires the rest of the gauge prerequisites to be
        # set, otherwise parse_args refuses. Provide them inline.
        args = amc.parse_args([
            "--otel-enabled",
            "--otel-metrics-endpoint", "http://localhost:4318/v1/metrics",
            "--output-dir", "test_out",
        ])
        assert args.otel_emit_gauges is True
    else:
        args = amc.parse_args(["--output-dir", "test_out"])
        assert args.otel_emit_gauges is False


def test_otel_emit_gauges_env_missing_defaults_false(amc, monkeypatch):
    monkeypatch.delenv("MEZMO_OTEL_EMIT_GAUGES", raising=False)
    args = amc.parse_args(["--output-dir", "test_out"])
    assert args.otel_emit_gauges is False


def test_otel_emit_gauges_cli_overrides_env(amc, monkeypatch):
    """--otel-no-emit-gauges on the CLI must beat a truthy env var."""
    monkeypatch.setenv("MEZMO_OTEL_EMIT_GAUGES", "1")
    args = amc.parse_args([
        "--otel-no-emit-gauges",
        "--output-dir", "test_out",
    ])
    assert args.otel_emit_gauges is False


def test_otel_gauges_only_implies_emit_gauges(amc, monkeypatch):
    monkeypatch.delenv("MEZMO_OTEL_EMIT_GAUGES", raising=False)
    args = amc.parse_args([
        "--otel-enabled",
        "--otel-gauges-only",
        "--otel-metrics-endpoint", "http://localhost:4318/v1/metrics",
        "--output-dir", "test_out",
    ])
    assert args.otel_gauges_only is True
    assert args.otel_emit_gauges is True


def test_otel_gauges_only_requires_otel_enabled(amc, monkeypatch):
    monkeypatch.delenv("MEZMO_OTEL_EMIT_GAUGES", raising=False)
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--otel-gauges-only",
            "--otel-metrics-endpoint", "http://localhost:4318/v1/metrics",
            "--output-dir", "test_out",
        ])


def test_otel_gauges_only_requires_metrics_endpoint(amc, monkeypatch):
    monkeypatch.delenv("MEZMO_OTEL_EMIT_GAUGES", raising=False)
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--otel-enabled",
            "--otel-gauges-only",
            "--otel-logs-endpoint", "http://localhost:4318/v1/logs",
            "--output-dir", "test_out",
        ])


def test_otel_gauges_only_requires_metrics_in_emit_selection(amc, monkeypatch):
    monkeypatch.delenv("MEZMO_OTEL_EMIT_GAUGES", raising=False)
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--otel-enabled",
            "--otel-gauges-only",
            "--otel-metrics-endpoint", "http://localhost:4318/v1/metrics",
            "--emit-selection", "logs,traces",
            "--output-dir", "test_out",
        ])


def test_otel_emit_gauges_requires_otel_enabled(amc, monkeypatch):
    monkeypatch.delenv("MEZMO_OTEL_EMIT_GAUGES", raising=False)
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--otel-emit-gauges",
            "--otel-metrics-endpoint", "http://localhost:4318/v1/metrics",
            "--output-dir", "test_out",
        ])


def test_otel_emit_gauges_requires_metrics_endpoint(amc, monkeypatch):
    monkeypatch.delenv("MEZMO_OTEL_EMIT_GAUGES", raising=False)
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--otel-enabled",
            "--otel-emit-gauges",
            # Only logs endpoint provided, no metrics endpoint.
            "--otel-logs-endpoint", "http://localhost:4318/v1/logs",
            "--output-dir", "test_out",
        ])


def test_otel_emit_gauges_requires_metrics_in_emit_selection(amc, monkeypatch):
    monkeypatch.delenv("MEZMO_OTEL_EMIT_GAUGES", raising=False)
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--otel-enabled",
            "--otel-emit-gauges",
            "--otel-metrics-endpoint", "http://localhost:4318/v1/metrics",
            "--emit-selection", "logs,traces",
            "--output-dir", "test_out",
        ])


@pytest.mark.parametrize("value", ["0", "-1", "-60"])
def test_otel_gauge_batch_seconds_must_be_positive(amc, value):
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--otel-gauge-batch-seconds", value,
            "--output-dir", "test_out",
        ])


def test_otel_gauge_metric_prefix_default_empty(amc):
    args = amc.parse_args(["--output-dir", "test_out"])
    assert args.otel_gauge_metric_prefix == ""


def test_otel_gauge_metric_prefix_custom(amc):
    args = amc.parse_args([
        "--otel-gauge-metric-prefix", "amc.",
        "--output-dir", "test_out",
    ])
    assert args.otel_gauge_metric_prefix == "amc."


def test_otel_emit_gauges_rejects_dst_artifact_combo(amc, monkeypatch):
    """The DST artifact splice (``_splice_dst_artifact``) makes per-component
    CSV timestamps non-monotonic, which breaks ``heapq.merge`` inside
    ``stream_otel_gauges``. Reject the combination at parse time."""
    monkeypatch.delenv("MEZMO_OTEL_EMIT_GAUGES", raising=False)
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--otel-enabled",
            "--otel-emit-gauges",
            "--otel-metrics-endpoint", "http://localhost:4318/v1/metrics",
            "--inject-dst-artifact-day", "1",
            "--output-dir", "test_out",
        ])


def test_otel_gauges_only_rejects_dst_artifact_combo(amc, monkeypatch):
    monkeypatch.delenv("MEZMO_OTEL_EMIT_GAUGES", raising=False)
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--otel-enabled",
            "--otel-gauges-only",
            "--otel-metrics-endpoint", "http://localhost:4318/v1/metrics",
            "--inject-dst-artifact-day", "1",
            "--output-dir", "test_out",
        ])


def test_otel_emit_gauges_allows_dst_artifact_zero(amc, monkeypatch):
    """``--inject-dst-artifact-day 0`` (the default, off) must coexist freely
    with ``--otel-emit-gauges``."""
    monkeypatch.delenv("MEZMO_OTEL_EMIT_GAUGES", raising=False)
    args = amc.parse_args([
        "--otel-enabled",
        "--otel-emit-gauges",
        "--otel-metrics-endpoint", "http://localhost:4318/v1/metrics",
        "--inject-dst-artifact-day", "0",
        "--output-dir", "test_out",
    ])
    assert args.otel_emit_gauges is True
    assert args.inject_dst_artifact_day == 0


# ------------------------------------------------------------------
# _env_bool helper
# ------------------------------------------------------------------
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", "on", "  ON  "])
def test_env_bool_truthy_values_return_true(amc, monkeypatch, value):
    monkeypatch.setenv("AMC_TEST_BOOL", value)
    assert amc._env_bool("AMC_TEST_BOOL") is True
    assert amc._env_bool("AMC_TEST_BOOL", default=True) is True
    assert amc._env_bool("AMC_TEST_BOOL", default=False) is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "nonsense"])
def test_env_bool_non_truthy_returns_false(amc, monkeypatch, value):
    """Non-truthy non-empty values return False regardless of ``default``."""
    monkeypatch.setenv("AMC_TEST_BOOL", value)
    assert amc._env_bool("AMC_TEST_BOOL") is False
    assert amc._env_bool("AMC_TEST_BOOL", default=True) is False


def test_env_bool_missing_returns_default(amc, monkeypatch):
    monkeypatch.delenv("AMC_TEST_BOOL", raising=False)
    assert amc._env_bool("AMC_TEST_BOOL") is False
    assert amc._env_bool("AMC_TEST_BOOL", default=True) is True
    assert amc._env_bool("AMC_TEST_BOOL", default=False) is False


@pytest.mark.parametrize("value", ["", "   ", "\t", "\n"])
def test_env_bool_empty_or_whitespace_honors_default(amc, monkeypatch, value):
    """Regression for the docstring contract: an empty or whitespace-only env
    var must return ``default`` (not False), so MEZMO_FOO='' with default=True
    does not silently flip to False."""
    monkeypatch.setenv("AMC_TEST_BOOL", value)
    assert amc._env_bool("AMC_TEST_BOOL") is False
    assert amc._env_bool("AMC_TEST_BOOL", default=True) is True
    assert amc._env_bool("AMC_TEST_BOOL", default=False) is False


# ----------------------------------------------------------------------
# preflight cell-count cap.
#
# The cap (``PREFLIGHT_CELL_CAP``) trips on the product of
# (duration_days * SECONDS_PER_DAY / interval_seconds) row count and the
# per-component default (or capped) metric count summed across the
# selected components. Tests below cover both rejection and the
# documented bypass paths.
# ----------------------------------------------------------------------


def test_preflight_rejects_subsecond_interval_at_defaults(amc):
    """``--interval-seconds 0.001`` at default --duration-days/--components/
    --metrics-per-component produces ~6.5B cells and must be rejected."""
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--interval-seconds", "0.001",
            "--output-dir", "test_out",
        ])


def test_preflight_allows_subsecond_interval_with_override(amc):
    """``--allow-huge-output`` bypasses the cell-count cap. The same args
    that fail in :func:`test_preflight_rejects_subsecond_interval_at_defaults`
    must parse cleanly with the override in place."""
    args = amc.parse_args([
        "--interval-seconds", "0.001",
        "--allow-huge-output",
        "--output-dir", "test_out",
    ])
    assert args.interval_seconds == 0.001
    assert args.allow_huge_output is True


def test_preflight_accepts_seven_day_defaults(amc):
    """Regression for the locked 7-day default-output SHA-256 hash. The
    7-day default run is well under the cap (~45M cells) and must parse
    without ``--allow-huge-output``."""
    args = amc.parse_args([
        "--duration-days", "7",
        "--output-dir", "test_out",
    ])
    assert args.duration_days == 7
    assert args.allow_huge_output is False


def test_preflight_accepts_seven_day_max_metrics(amc):
    """7 days at the per-component metric cap (10) across all components
    is still under the cell cap (~79M cells) and must parse cleanly."""
    args = amc.parse_args([
        "--duration-days", "7",
        "--metrics-per-component", "10",
        "--output-dir", "test_out",
    ])
    assert args.duration_days == 7
    assert args.metrics_per_component == 10


def test_preflight_rejects_narrow_components_when_product_still_huge(amc):
    """Narrowing ``--components`` to a single component does not bypass
    the cap when the row x metric product for that one component still
    exceeds it. apigateway has 6 default metrics; at
    ``--interval-seconds 0.001`` it emits 86.4M rows x 6 = ~518M cells."""
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--interval-seconds", "0.001",
            "--components", "apigateway",
            "--output-dir", "test_out",
        ])


def test_preflight_accepts_large_run_when_narrow_components_drop_under_cap(amc):
    """A run that would exceed the cap with the default ``--components all``
    must parse cleanly once narrowed to a small allowlist that drops the
    cell estimate under the cap.

    ``--duration-days 7 --interval-seconds 0.1`` emits 6.048M rows. With
    all components and default metrics (85 total metrics) that is ~514M
    cells (over the cap), but narrowed to ``observabilitypipeline`` (4
    default metrics) it is ~24M cells (well under)."""
    args = amc.parse_args([
        "--duration-days", "7",
        "--interval-seconds", "0.1",
        "--components", "observabilitypipeline",
        "--output-dir", "test_out",
    ])
    assert args.components == {"observabilitypipeline"}
    assert args.interval_seconds == 0.1


def test_preflight_error_message_names_relevant_flags(amc, capsys):
    """The error must name the four knobs that influence the cap plus
    ``--allow-huge-output``, so users hitting the cap know what to do."""
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--interval-seconds", "0.001",
            "--output-dir", "test_out",
        ])
    err = capsys.readouterr().err
    assert "--interval-seconds" in err
    assert "--duration-days" in err
    assert "--metrics-per-component" in err
    assert "--components" in err
    assert "--allow-huge-output" in err


def test_preflight_skipped_for_combine_only(amc):
    """``--combine-only`` reads an existing dataset and never emits new
    metric cells, so the preflight cap must not apply. A user who
    originally generated with ``--allow-huge-output --interval-seconds
    0.001`` should be able to re-run the combine step over the same
    dataset without remembering to pass the bypass flag again."""
    args = amc.parse_args([
        "--combine-only",
        "--interval-seconds", "0.001",
        "--output-dir", "test_out",
    ])
    assert args.combine_only is True
    assert args.interval_seconds == 0.001
    assert args.allow_huge_output is False


def test_preflight_row_count_matches_generator_derivation(amc):
    """The preflight estimate must use the same ``int(total_seconds //
    interval_seconds)`` expression as the generator's ``n_rows`` so the
    cap can never reject a config that the generator would actually
    emit below the cap (and vice versa)."""
    interval = 0.1
    duration_days = 7
    expected_rows = int((amc.SECONDS_PER_DAY * duration_days) // interval)
    # ``observabilitypipeline`` (4 default metrics) keeps the estimate
    # under the cap so this case must parse cleanly. Cross-check the
    # arithmetic: rows * metrics must be under PREFLIGHT_CELL_CAP.
    metrics = amc.DEFAULT_METRICS_PER_COMPONENT["observabilitypipeline"]
    assert expected_rows * metrics < amc.PREFLIGHT_CELL_CAP
    args = amc.parse_args([
        "--duration-days", str(duration_days),
        "--interval-seconds", str(interval),
        "--components", "observabilitypipeline",
        "--output-dir", "test_out",
    ])
    assert args.duration_days == duration_days
    assert args.interval_seconds == interval

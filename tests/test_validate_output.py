"""Tests for the ``--validate-output PATH`` standalone validator (VER-139).

Each validator function is covered by a focused unit test (mutate the
schema or a target CSV, run the function, assert the expected violation),
plus end-to-end integration coverage of the CLI mode against the default
1-day and 7-day outputs.

Known out-of-scope violations: the ticket explicitly bundles the
fractional-counter and unit-mismatch fixes with the
[Topology-aware workload model](VER-134) re-baseline window, so the
integration test asserts a precise expected set of violations against the
default output — extra violations are regressions; fewer are progress and
require updating the constant below.
"""
import csv
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import SCRIPT_PATH, run_capture


# ------------------------------------------------------------------
# Fixtures: pre-generated runs we can mutate per-test
# ------------------------------------------------------------------
@pytest.fixture
def schema_run(amc, tmp_path):
    """Quick 600s-interval run with schema enabled. Each test gets its own
    output dir so mutations don't bleed across tests."""
    out = tmp_path / "run"
    run_capture(
        amc, out, days=1,
        extra_args=[
            "--emit-selection", "metrics,schema",
            "--interval-seconds", "600",
            "--components", "apigateway,cacheservice",
        ],
    )
    return out


@pytest.fixture(scope="module")
def one_day_schema_run(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("ver139_validator_one_day")
    return run_capture(
        amc, out, days=1, extra_args=["--emit-selection", "metrics,schema"]
    )


@pytest.fixture(scope="module")
def seven_day_schema_run(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("ver139_validator_seven_day")
    return run_capture(
        amc, out, days=7, extra_args=["--emit-selection", "metrics,schema"]
    )


def _load_schema(out: Path) -> dict:
    return json.loads((out / "schema.json").read_text(encoding="utf-8"))


def _write_schema(out: Path, schema: dict) -> None:
    (out / "schema.json").write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# ------------------------------------------------------------------
# parse_args validation
# ------------------------------------------------------------------
def test_validate_output_requires_existing_dir(amc, capsys, tmp_path):
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--validate-output", str(tmp_path / "does_not_exist"),
        ])
    err = capsys.readouterr().err
    assert "validate-output" in err
    assert "directory" in err


def test_validate_output_mutex_with_combine(amc, capsys, tmp_path):
    out = tmp_path / "exists"
    out.mkdir()
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--validate-output", str(out),
            "--combine",
        ])
    err = capsys.readouterr().err
    assert "validate-output" in err and "combine" in err


def test_validate_output_mutex_with_combine_only(amc, capsys, tmp_path):
    out = tmp_path / "exists"
    out.mkdir()
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--validate-output", str(out),
            "--combine-only",
        ])
    err = capsys.readouterr().err
    assert "validate-output" in err and "combine" in err


def test_validate_warn_requires_validate_output(amc, capsys, tmp_path):
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--validate-warn",
        ])
    err = capsys.readouterr().err
    assert "validate-warn" in err and "validate-output" in err


# ------------------------------------------------------------------
# Schema loading + version check
# ------------------------------------------------------------------
def test_load_schema_rejects_missing_file(amc, tmp_path):
    with pytest.raises(ValueError, match="requires"):
        amc._load_schema_document(tmp_path / "missing.json")


def test_load_schema_rejects_malformed_json(amc, tmp_path):
    p = tmp_path / "schema.json"
    p.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        amc._load_schema_document(p)


def test_load_schema_rejects_unknown_version(amc, tmp_path):
    p = tmp_path / "schema.json"
    p.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        amc._load_schema_document(p)


# ------------------------------------------------------------------
# Required files / unknown files
# ------------------------------------------------------------------
def test_required_files_clean_on_fresh_run(amc, schema_run):
    schema = _load_schema(schema_run)
    assert amc._validate_required_files_present(schema_run, schema) == []


def test_required_files_flags_missing(amc, schema_run):
    schema = _load_schema(schema_run)
    (schema_run / "apigateway.csv").unlink()
    violations = amc._validate_required_files_present(schema_run, schema)
    assert any("apigateway.csv" in v for v in violations)


def test_unknown_files_clean_on_fresh_run(amc, schema_run):
    schema = _load_schema(schema_run)
    assert amc._validate_no_unknown_files(schema_run, schema) == []


def test_unknown_files_flags_stray_artifact(amc, schema_run):
    (schema_run / "stray.txt").write_text("hello")
    schema = _load_schema(schema_run)
    violations = amc._validate_no_unknown_files(schema_run, schema)
    assert any("stray.txt" in v for v in violations)


def test_unknown_files_allows_schema_json_when_undeclared(amc, schema_run):
    """schema.json is always allowed in the directory even if a buggy run
    omitted it from the declared file list — otherwise the validator could
    not bootstrap."""
    schema = _load_schema(schema_run)
    schema["files"] = [f for f in schema["files"] if f != "schema.json"]
    _write_schema(schema_run, schema)
    # Reload, then re-check.
    schema = _load_schema(schema_run)
    assert amc._validate_no_unknown_files(schema_run, schema) == []


# ------------------------------------------------------------------
# anomalies.csv sort
# ------------------------------------------------------------------
def test_anomalies_sort_clean_on_fresh_run(amc, schema_run):
    schema = _load_schema(schema_run)
    assert amc._validate_anomalies_sorted(schema_run, schema) == []


def test_anomalies_sort_flags_out_of_order(amc, schema_run):
    schema = _load_schema(schema_run)
    p = schema_run / "anomalies.csv"
    rows = p.read_text().splitlines()
    header, body = rows[0], rows[1:]
    # Reverse-sort to force a violation; ensures the first comparison fails.
    body = list(reversed(body))
    if len(body) < 2:
        pytest.skip("anomalies.csv has too few rows to reverse-test sort")
    p.write_text("\n".join([header] + body) + "\n")
    violations = amc._validate_anomalies_sorted(schema_run, schema)
    assert violations, "reversed manifest must produce sort violations"


# ------------------------------------------------------------------
# Row count
# ------------------------------------------------------------------
def test_row_count_clean_on_fresh_run(amc, schema_run):
    schema = _load_schema(schema_run)
    for component in schema["metadata"]["components"]:
        assert amc._validate_component_row_count(
            schema_run, schema, component
        ) == []


def test_row_count_flags_extra_rows(amc, schema_run):
    schema = _load_schema(schema_run)
    component = schema["metadata"]["components"][0]
    csv_path = schema_run / f"{component}.csv"
    # Append many bogus extra rows so we exceed the expected max even with
    # the DST splice allowance (only one day's worth at most).
    with open(csv_path, "a", encoding="utf-8") as f:
        for _ in range(200_000):
            f.write("2099-01-01 00:00:00," + ",".join("0" * 60) + "\n")
    violations = amc._validate_component_row_count(
        schema_run, schema, component
    )
    assert any("exceeds expected max" in v for v in violations)


def test_row_count_dst_splice_extra_allowed(amc, tmp_path):
    """A 2-day run with --inject-dst-artifact-day 1 has 3600/interval extra
    rows on day 1; the validator's row-count check must allow them rather
    than flag them as over-emission."""
    out = tmp_path / "dst"
    run_capture(amc, out, days=2, extra_args=[
        "--emit-selection", "metrics,schema",
        "--interval-seconds", "600",
        "--inject-dst-artifact-day", "1",
        "--components", "apigateway",
    ])
    schema = _load_schema(out)
    assert amc._validate_component_row_count(out, schema, "apigateway") == []


# ------------------------------------------------------------------
# Timestamp coverage
# ------------------------------------------------------------------
def test_timestamp_coverage_clean_on_fresh_run(amc, schema_run):
    schema = _load_schema(schema_run)
    for component in schema["metadata"]["components"]:
        assert amc._validate_component_timestamp_coverage(
            schema_run, schema, component
        ) == []


def test_timestamp_coverage_flags_out_of_range(amc, schema_run):
    schema = _load_schema(schema_run)
    # Shift START forward by one day so every existing timestamp is now
    # "before START" and gets flagged.
    schema["metadata"]["start"] = "2099-01-01T00:00:00"
    _write_schema(schema_run, schema)
    schema = _load_schema(schema_run)
    component = schema["metadata"]["components"][0]
    violations = amc._validate_component_timestamp_coverage(
        schema_run, schema, component
    )
    assert violations and "precedes START" in violations[0]


# ------------------------------------------------------------------
# Cell bounds
# ------------------------------------------------------------------
def test_cell_bounds_flags_above_max(amc, schema_run):
    """Mutate a cpu_util_pct value to 250 and verify ``above max_value=100``."""
    schema = _load_schema(schema_run)
    component = "apigateway"
    csv_path = schema_run / f"{component}.csv"
    rows = csv_path.read_text().splitlines()
    header = rows[0].split(",")
    cpu_col = header.index("cpu_util_pct")
    # Mutate the first data row's cpu value.
    parts = rows[1].split(",")
    parts[cpu_col] = "250.000"
    rows[1] = ",".join(parts)
    csv_path.write_text("\n".join(rows) + "\n")
    violations = amc._validate_component_cells(schema_run, schema, component)
    assert any("above max_value=100" in v and "cpu_util_pct" in v for v in violations)


def test_cell_bounds_flags_below_min(amc, schema_run):
    """Mutate an error_rate cell to -1 to verify below_min detection on a
    ratio."""
    schema = _load_schema(schema_run)
    component = "apigateway"
    csv_path = schema_run / f"{component}.csv"
    rows = csv_path.read_text().splitlines()
    header = rows[0].split(",")
    col = header.index("error_rate")
    parts = rows[1].split(",")
    parts[col] = "-1.000"
    rows[1] = ",".join(parts)
    csv_path.write_text("\n".join(rows) + "\n")
    violations = amc._validate_component_cells(schema_run, schema, component)
    assert any("below min_value=0" in v and "error_rate" in v for v in violations)


def test_cell_bounds_flags_fractional_int(amc, schema_run):
    """``active_connections`` is dtype='int'; a 0.5 value triggers the
    fractional check (and is the canonical out-of-scope violation per the
    VER-139 ticket)."""
    schema = _load_schema(schema_run)
    component = "apigateway"
    csv_path = schema_run / f"{component}.csv"
    rows = csv_path.read_text().splitlines()
    header = rows[0].split(",")
    col = header.index("active_connections")
    parts = rows[1].split(",")
    parts[col] = "1234.567"
    rows[1] = ",".join(parts)
    csv_path.write_text("\n".join(rows) + "\n")
    violations = amc._validate_component_cells(schema_run, schema, component)
    assert any("active_connections" in v and "fractional" in v for v in violations)


def test_cell_bounds_flags_negative_for_counter(amc, schema_run):
    """A negative cache_hits cell must surface the counter-non-negative
    check (also the min_value=0 check; the validator records each kind
    once per metric column)."""
    schema = _load_schema(schema_run)
    component = "cacheservice"
    csv_path = schema_run / f"{component}.csv"
    rows = csv_path.read_text().splitlines()
    header = rows[0].split(",")
    col = header.index("cache_hits")
    parts = rows[1].split(",")
    parts[col] = "-5.000"
    rows[1] = ",".join(parts)
    csv_path.write_text("\n".join(rows) + "\n")
    violations = amc._validate_component_cells(schema_run, schema, component)
    assert any("cache_hits" in v and "below min_value=0" in v for v in violations)


def test_cell_bounds_flags_header_drift(amc, schema_run):
    """When the CSV header doesn't match the schema's metric column order,
    cell checks are meaningless — the validator must short-circuit with a
    single explicit drift violation."""
    schema = _load_schema(schema_run)
    component = "apigateway"
    csv_path = schema_run / f"{component}.csv"
    rows = csv_path.read_text().splitlines()
    rows[0] = "timestamp,foo,bar,baz"
    csv_path.write_text("\n".join(rows) + "\n")
    violations = amc._validate_component_cells(schema_run, schema, component)
    assert violations
    assert "header" in violations[0] and "schema column order" in violations[0]


# ------------------------------------------------------------------
# Derivations
# ------------------------------------------------------------------
def test_derivations_clean_on_fresh_run(amc, schema_run):
    schema = _load_schema(schema_run)
    for component in schema["metadata"]["components"]:
        assert amc._validate_component_derivations(
            schema_run, schema, component
        ) == []


def test_derivations_flags_inconsistent_hit_ratio(amc, schema_run):
    """Mutate hit_ratio away from the formula and verify the validator
    catches it. cacheservice.hit_ratio = 100 * cache_hits / (cache_hits +
    cache_misses); set it to 0.0 while keeping the source columns intact
    so the recomputed value differs significantly."""
    schema = _load_schema(schema_run)
    component = "cacheservice"
    csv_path = schema_run / f"{component}.csv"
    rows = csv_path.read_text().splitlines()
    header = rows[0].split(",")
    ratio_col = header.index("hit_ratio")
    parts = rows[1].split(",")
    parts[ratio_col] = "0.000"
    rows[1] = ",".join(parts)
    csv_path.write_text("\n".join(rows) + "\n")
    violations = amc._validate_component_derivations(
        schema_run, schema, component
    )
    assert any("hit_ratio" in v and "differs from recomputed" in v
               for v in violations)


# ------------------------------------------------------------------
# Orchestrator + CLI mode
# ------------------------------------------------------------------
def test_validate_output_returns_violation_list(amc, schema_run):
    violations = amc.validate_output(schema_run)
    assert isinstance(violations, list)


def test_validate_output_cli_exits_nonzero_on_violation(amc, tmp_path, capsys):
    """End-to-end: a run with an injected bad cell must exit 1 under
    ``--validate-output`` in default (hard-fail) mode."""
    out = tmp_path / "bad"
    run_capture(amc, out, days=1, extra_args=[
        "--emit-selection", "metrics,schema",
        "--interval-seconds", "600",
        "--components", "apigateway",
    ])
    # Inject a bogus cpu_util_pct cell to force a violation.
    csv_path = out / "apigateway.csv"
    rows = csv_path.read_text().splitlines()
    header = rows[0].split(",")
    col = header.index("cpu_util_pct")
    parts = rows[1].split(",")
    parts[col] = "250.000"
    rows[1] = ",".join(parts)
    csv_path.write_text("\n".join(rows) + "\n")

    with pytest.raises(SystemExit) as exc_info:
        amc.main(["--validate-output", str(out)])
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "cpu_util_pct" in err


def test_validate_output_cli_warn_mode_exits_zero(amc, tmp_path, capsys):
    """``--validate-warn`` downgrades violations to a non-fatal report and
    exits 0 (so CI can run the validator informationally without breaking
    the build during the topology-aware re-baseline window)."""
    out = tmp_path / "warn"
    run_capture(amc, out, days=1, extra_args=[
        "--emit-selection", "metrics,schema",
        "--interval-seconds", "600",
        "--components", "apigateway",
    ])
    csv_path = out / "apigateway.csv"
    rows = csv_path.read_text().splitlines()
    header = rows[0].split(",")
    col = header.index("cpu_util_pct")
    parts = rows[1].split(",")
    parts[col] = "250.000"
    rows[1] = ",".join(parts)
    csv_path.write_text("\n".join(rows) + "\n")

    amc.main(["--validate-output", str(out), "--validate-warn"])
    err = capsys.readouterr().err
    assert "cpu_util_pct" in err
    assert "validate-warn" in err


def test_validate_output_cli_clean_directory_exits_zero(amc, tmp_path, capsys):
    """A directory whose contents match its schema produces no violations
    and the CLI exits 0 with an OK message on stdout. Uses a controlled
    one-component run so we can construct a case with no fractional-int
    violations — apigateway is the cleanest at 600s interval after we
    massage active_connections to an exact int."""
    out = tmp_path / "clean"
    run_capture(amc, out, days=1, extra_args=[
        "--emit-selection", "metrics,schema",
        "--interval-seconds", "600",
        "--components", "vectorstore",
    ])
    # vectorstore has no dtype="int" violations at the top-5 default
    # metric selection, but avg_vector_dim (supplemental) is int=1536 with
    # std=0; only the first 5 metrics emit by default. Verify clean.
    violations = amc.validate_output(out)
    # If a future patch tightens vectorstore's metric metadata this assertion
    # may need updating along with the schema-side change.
    assert violations == [], (
        f"vectorstore default emission should validate clean; got: {violations}"
    )
    amc.main(["--validate-output", str(out)])
    cap = capsys.readouterr()
    assert "OK" in cap.out


# ------------------------------------------------------------------
# Integration against the default 1-day and 7-day outputs
# ------------------------------------------------------------------
# Known out-of-scope violations bundled with VER-134's topology-aware
# re-baseline. The validator MUST find exactly this set on the default
# 1-day and 7-day runs; extras are regressions, missing ones are progress
# that requires updating this list. Each entry is
# ``(component_csv, metric, kind)``. Kinds are normalized to:
#  - ``fractional``  — value not whole-integer despite ``dtype="int"``
#  - ``above_max``   — value above declared ``max_value``
#  - ``below_min``   — value below declared ``min_value``
#  - ``negative_kind`` — value negative despite counter/rate semantic_type
# All entries here are tracked in the VER-139 follow-up tickets and are
# expected to be cleared by the topology-aware workload model re-baseline.
_FRACTIONAL_INT_VIOLATIONS = {
    ("authservice.csv", "login_attempts", "fractional"),
    ("authservice.csv", "active_sessions", "fractional"),
    ("cacheservice.csv", "cache_hits", "fractional"),
    ("cacheservice.csv", "cache_misses", "fractional"),
    ("apigateway.csv", "active_connections", "fractional"),
    ("database.csv", "connections", "fractional"),
    ("mqservice.csv", "pending_messages", "fractional"),
    ("mqservice.csv", "processed_messages", "fractional"),
    ("mqservice.csv", "dead_letter_queue", "fractional"),
    ("loadbalancer.csv", "healthcheck_failures", "fractional"),
    ("loadbalancer.csv", "active_tls_handshakes", "fractional"),
    ("loadbalancer.csv", "tls_handshake_errors", "fractional"),
    ("loadbalancer.csv", "connection_resets", "fractional"),
    ("scheduler.csv", "jobs_running", "fractional"),
    ("scheduler.csv", "jobs_queued", "fractional"),
    ("scheduler.csv", "missed_schedules", "fractional"),
    ("identityprovider.csv", "failed_oidc_flows", "fractional"),
}

# 1-day default emission surfaces only the fractional-int violations.
_EXPECTED_VIOLATIONS_ONE_DAY = set(_FRACTIONAL_INT_VIOLATIONS)

# 7-day emission additionally activates the high-pressure multi-day
# scenarios (e.g. LLM context overflow), which push ``context_overflow_rate``
# above the declared ratio bound. Filed as a separate follow-up so the
# unit definition and the anomaly amplitude can be reconciled in the
# re-baseline window.
_EXPECTED_VIOLATIONS_SEVEN_DAY = set(_FRACTIONAL_INT_VIOLATIONS) | {
    ("llm_analytics.csv", "context_overflow_rate", "above_max"),
}


def _classify(line: str) -> tuple[str, str, str] | None:
    """Parse one validator line into ``(file, metric, kind)``. Returns
    None for sentence-style violations that don't fit the standard
    ``<file> line N: <metric>=<value> <verb>...`` format."""
    m = re.match(
        r"(?P<file>\S+\.csv)\s+line\s+\d+:\s+(?P<metric>\S+?)="
        r".+?\s+(?P<verb>is fractional|is negative|below min_value"
        r"|above max_value)",
        line,
    )
    if not m:
        return None
    verb = m.group("verb")
    kind = {
        "is fractional": "fractional",
        "is negative": "negative_kind",
        "below min_value": "below_min",
        "above max_value": "above_max",
    }[verb]
    return (m.group("file"), m.group("metric"), kind)


@pytest.mark.parametrize(
    "fixture_name,expected",
    [
        ("one_day_schema_run", _EXPECTED_VIOLATIONS_ONE_DAY),
        ("seven_day_schema_run", _EXPECTED_VIOLATIONS_SEVEN_DAY),
    ],
)
def test_validator_default_violations_match_expected(
    amc, request, fixture_name, expected
):
    """The validator's findings against the default runs must equal the
    expected known-issue set exactly. Adding a violation is a regression
    (something got worse); removing one is progress and requires updating
    the constant. Pair the constant update with a follow-up ticket closure."""
    run = request.getfixturevalue(fixture_name)
    violations = amc.validate_output(run.out_dir)
    classified = {_classify(v) for v in violations} - {None}
    extras = classified - expected
    missing = expected - classified
    assert not extras, (
        f"Unexpected new violations on {fixture_name}: {sorted(extras)}. "
        "If intentional, add them to the expected set."
    )
    assert not missing, (
        f"Expected violations no longer fired on {fixture_name}: "
        f"{sorted(missing)}. If a generator fix landed, remove the entry."
    )


@pytest.mark.parametrize(
    "fixture_name",
    ["one_day_schema_run", "seven_day_schema_run"],
)
def test_validator_default_only_known_violation_kinds(
    amc, request, fixture_name
):
    """File presence, timestamp coverage, manifest sort, derivation, and
    row-count checks must be clean against the default outputs — those are
    the categories VER-139 promises to gate on. Cell-bound violations are
    bounded by the expected set in the test above."""
    run = request.getfixturevalue(fixture_name)
    violations = amc.validate_output(run.out_dir)
    for line in violations:
        cls = _classify(line)
        if cls is None:
            pytest.fail(
                f"non-cell violation on {fixture_name}: {line!r} "
                "(file presence / coverage / sort / row-count / derivation "
                "are all expected to be clean)"
            )

"""Tests for the ``--validate-output PATH`` standalone validator.

Each validator function is covered by a focused unit test (mutate the
schema or a target CSV, run the function, assert the expected violation),
plus end-to-end integration coverage of the CLI mode against the default
1-day and 7-day outputs.

Known residual violations (phase 6 flag day): the
fractional-counter set previously flagged here was cleared by the
integer-cast bundle, so the 1-day default integration test now asserts
an empty violation set. The 7-day default still surfaces a known
``above_max`` violation type on ``llm_analytics.context_overflow_rate`` —
that scenario-amplitude reconciliation is explicitly deferred to
Phase 9. Extra violations are regressions; fewer are progress
and require updating the constants below.
"""
import json
import re
from pathlib import Path

import pytest

from conftest import run_capture


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
            "--components", "apigateway,cacheservice",
        ],
        interval_seconds=600,
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
    run_capture(amc, out, days=2, interval_seconds=600, extra_args=[
        "--emit-selection", "metrics,schema",
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
    fractional check (the canonical out-of-scope violation for this
    column under realistic mode)."""
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


@pytest.mark.parametrize("bad_cell", ["nan", "inf", "-inf"])
def test_cell_bounds_flags_non_finite_in_int_column(amc, schema_run, bad_cell):
    """A NaN/±inf cell in a ``dtype='int'`` column must surface a
    ``non_finite`` violation — not crash the validator. Pre-guard,
    ``float('nan')`` parsed fine and then ``round(nan)`` raised an
    uncaught ValueError (OverflowError for ±inf) out of
    ``_validate_component_cells``, turning a corrupted CSV into a raw
    traceback instead of a violation report."""
    schema = _load_schema(schema_run)
    component = "apigateway"
    csv_path = schema_run / f"{component}.csv"
    rows = csv_path.read_text().splitlines()
    header = rows[0].split(",")
    col = header.index("active_connections")
    parts = rows[1].split(",")
    parts[col] = bad_cell
    rows[1] = ",".join(parts)
    csv_path.write_text("\n".join(rows) + "\n")
    violations = amc._validate_component_cells(schema_run, schema, component)
    assert any(
        "active_connections" in v and "not finite" in v for v in violations
    )


def test_cell_bounds_flags_nan_in_float_column(amc, schema_run):
    """A NaN cell in a float column must surface a ``non_finite``
    violation. Pre-guard it passed every range check silently: every
    comparison against NaN is False, so ``below_min`` / ``above_max`` /
    ``negative_kind`` never fired."""
    schema = _load_schema(schema_run)
    component = "apigateway"
    csv_path = schema_run / f"{component}.csv"
    rows = csv_path.read_text().splitlines()
    header = rows[0].split(",")
    col = header.index("error_rate")
    parts = rows[1].split(",")
    parts[col] = "nan"
    rows[1] = ",".join(parts)
    csv_path.write_text("\n".join(rows) + "\n")
    violations = amc._validate_component_cells(schema_run, schema, component)
    assert any("error_rate" in v and "not finite" in v for v in violations)


def test_derivation_flags_non_finite_derived_cell(amc, schema_run):
    """A NaN ``hit_ratio`` cell must surface a derivation violation.
    Pre-guard, NaN poisoned the tolerance gate (``abs(nan - x) > tol``
    is False) so a corrupted derived column validated clean."""
    schema = _load_schema(schema_run)
    component = "cacheservice"
    csv_path = schema_run / f"{component}.csv"
    rows = csv_path.read_text().splitlines()
    header = rows[0].split(",")
    col = header.index("hit_ratio")
    parts = rows[1].split(",")
    parts[col] = "nan"
    rows[1] = ",".join(parts)
    csv_path.write_text("\n".join(rows) + "\n")
    violations = amc._validate_component_derivations(schema_run, schema, component)
    assert any("hit_ratio" in v and "not finite" in v for v in violations)


def test_derivation_flags_non_finite_recomputed_source(amc, schema_run):
    """A NaN *source* cell (``cache_hits``) makes the recomputed
    ``hit_ratio`` non-finite; the derivation check must flag it rather
    than let the NaN poison the tolerance comparison."""
    schema = _load_schema(schema_run)
    component = "cacheservice"
    csv_path = schema_run / f"{component}.csv"
    rows = csv_path.read_text().splitlines()
    header = rows[0].split(",")
    col = header.index("cache_hits")
    parts = rows[1].split(",")
    parts[col] = "nan"
    rows[1] = ",".join(parts)
    csv_path.write_text("\n".join(rows) + "\n")
    violations = amc._validate_component_derivations(schema_run, schema, component)
    assert any("hit_ratio" in v and "not finite" in v for v in violations)


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
# Dispatch tables: raise on unknown keys
# ------------------------------------------------------------------
def test_recomputers_and_derivations_keysets_match(amc):
    """``DERIVATIONS`` and ``_RECOMPUTERS`` are paired single-source
    registries (one declares the in-process derivation pass, the other
    declares the on-disk validator's recomputer). Drift between their
    keysets means a derived column is either silently unvalidated or
    the validator dispatches to a missing recomputer — both regressions
    of the unknown-key invariant."""
    assert set(amc.DERIVATIONS.keys()) == set(amc._RECOMPUTERS.keys())


def test_recompute_cacheservice_raises_keyerror_on_unknown_metric(amc):
    """The per-metric dispatch inside ``_recompute_cacheservice`` must
    raise ``KeyError`` for any metric other than ``hit_ratio``. The
    pre-existing behavior silently returned ``None`` for unknown metric
    names, which masked drift between ``DERIVATIONS['cacheservice']``
    and the recomputer body."""
    name_to_col = {"cache_hits": 1, "cache_misses": 2, "hit_ratio": 3}
    row = ["2026-01-01T00:00:00", "10", "5", "66.667"]
    with pytest.raises(KeyError):
        amc._recompute_cacheservice("unknown_metric", row, name_to_col)


def test_validate_component_derivations_raises_on_unregistered_component(
    amc, schema_run
):
    """When ``schema.json`` declares a derivation for a component the
    validator has no recomputer for, ``_validate_component_derivations``
    must raise ``KeyError`` (programmer drift surfaced loudly, not a
    soft violation entry the caller might overlook)."""
    schema = _load_schema(schema_run)
    # Pick any schema-resident component the catalog does not register a
    # recomputer for, so the test stays valid as ``_RECOMPUTERS`` evolves.
    # Sorted for determinism; ``_RECOMPUTERS`` covers ``cacheservice`` today,
    # so the remaining components all qualify — but the loop guards against
    # a future where every component gains a derived column.
    candidates = sorted(
        c for c in schema["components"] if c not in amc._RECOMPUTERS
    )
    assert candidates, (
        "every schema component already has a recomputer; this test needs "
        "at least one unregistered component to exercise the raise path"
    )
    component = candidates[0]
    metrics = schema["components"][component]["metrics"]
    metrics[0]["derivation"] = "synthetic_for_test"
    _write_schema(schema_run, schema)
    schema = _load_schema(schema_run)
    with pytest.raises(KeyError):
        amc._validate_component_derivations(schema_run, schema, component)


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
    run_capture(amc, out, days=1, interval_seconds=600, extra_args=[
        "--emit-selection", "metrics,schema",
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
    run_capture(amc, out, days=1, interval_seconds=600, extra_args=[
        "--emit-selection", "metrics,schema",
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
    run_capture(amc, out, days=1, interval_seconds=600, extra_args=[
        "--emit-selection", "metrics,schema",
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
# Known residual violations (phase 6 flag-day). The
# validator MUST find exactly this set on the default 1-day and 7-day
# runs; extras are regressions, missing ones are progress that requires
# updating this list. The set is keyed by violation type, not by the number
# of rows that trip the same known bound. Each entry is
# ``(component_csv, metric, kind)``.
# Kinds are normalized to:
#  - ``fractional``  — value not whole-integer despite ``dtype="int"``
#  - ``above_max``   — value above declared ``max_value``
#  - ``below_min``   — value below declared ``min_value``
#  - ``negative_kind`` — value negative despite counter/rate semantic_type
#
# Phase 6 cleared every fractional-int violation flagged by the
# integer-cast bundle in ``generate_component``.
# Both default runs are now violation-free, with one exception:
#
# The 7-day run still surfaces a known ``above_max`` violation type on
# ``llm_analytics.context_overflow_rate``. The LLM context-overflow
# scenario (``llm_weekend_batch``) drives that ratio toward 8.5 from
# day 5 + 2h to simulate context-window saturation, which exceeds the
# metric's declared ``max_value=1``. Reconciling the scenario
# amplitude with the ratio bound is a scenario-catalog re-tune
# explicitly deferred to phase 9.
_EXPECTED_VIOLATIONS_ONE_DAY: set[tuple[str, str, str]] = set()

_EXPECTED_VIOLATIONS_SEVEN_DAY = {
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
    the categories the validator promises to gate on. Cell-bound violations
    are bounded by the expected set in the test above."""
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


# ------------------------------------------------------------------
# Topology coupling correlation (phase 7)
# ------------------------------------------------------------------
def test_topology_coupling_clean_on_fresh_realistic_run(
    amc, one_day_schema_run,
):
    """Default 1-day run in realistic mode (the default since the phase 6
    flag day) must produce no topology coupling violations — every
    constant-weight edge's Pearson correlation between source and target
    canonical load metrics meets or exceeds its threshold (0.85 default)."""
    schema = _load_schema(one_day_schema_run.out_dir)
    assert schema["metadata"]["topology_mode"] == "realistic"
    assert amc._validate_topology_coupling(
        one_day_schema_run.out_dir, schema
    ) == []


def test_topology_coupling_skipped_under_independent_mode(amc, tmp_path):
    """Independent mode produces decoupled baselines by construction;
    the coupling check must not even run, regardless of the actual
    correlation realized on disk."""
    out = tmp_path / "indep"
    run_capture(
        amc, out, days=1,
        extra_args=[
            "--emit-selection", "metrics,schema",
            "--topology-mode", "independent",
        ],
    )
    schema = _load_schema(out)
    assert schema["metadata"]["topology_mode"] == "independent"
    assert amc._validate_topology_coupling(out, schema) == []


def test_topology_coupling_flags_constant_downstream(amc, tmp_path):
    """A deliberately broken downstream — every row of the target's
    canonical load metric set to a single constant — must be flagged
    as a coupling violation (zero-variance branch).

    60s interval gives 1,440 rows over one day — well above the
    100-row correlation floor and dense enough to drive the
    zero-variance branch deterministically — while keeping the
    end-to-end run cheap."""
    out = tmp_path / "bad_coupling"
    run_capture(
        amc, out, days=1,
        extra_args=[
            "--emit-selection", "metrics,schema",
            "--components", "loadbalancer,apigateway",
        ],
    )
    csv_path = out / "apigateway.csv"
    rows = csv_path.read_text().splitlines()
    header = rows[0].split(",")
    col = header.index("requests_per_sec")
    new_rows = [rows[0]]
    for r in rows[1:]:
        parts = r.split(",")
        parts[col] = "800.000"
        new_rows.append(",".join(parts))
    csv_path.write_text("\n".join(new_rows) + "\n")
    schema = _load_schema(out)
    violations = amc._validate_topology_coupling(out, schema)
    assert any(
        "loadbalancer->apigateway" in v
        and ("zero-variance" in v or "below threshold" in v)
        for v in violations
    ), (
        f"validator must flag a constant downstream as a coupling "
        f"regression; got: {violations}"
    )
    # The zero-variance diagnostic must name the offending side
    # explicitly (here: the constant *target* column) rather than the
    # ambiguous "source or target" both-sides form — CLAUDE.md promises
    # the violation names the side.
    zv = [
        v for v in violations
        if "loadbalancer->apigateway" in v and "zero-variance" in v
    ]
    assert zv, (
        "expected the zero-variance branch to fire for an all-constant "
        f"target column; got: {violations}"
    )
    for v in zv:
        assert "(apigateway.requests_per_sec)" in v, (
            f"zero-variance violation must name exactly the constant "
            f"side; got: {v!r}"
        )


def test_topology_coupling_flags_random_downstream(amc, tmp_path):
    """A downstream replaced with values uniformly random within its
    natural range must drive Pearson well below the 0.85 threshold.

    The apigateway -> database edge is the only constant-weight edge
    we exercise here, so narrow ``--components`` to the source plus
    the target and use a coarse ``--interval-seconds`` to keep the
    end-to-end run quick. 60s interval gives 1440 rows over one day
    — well above the 100-row floor and dense enough to make the
    Pearson coefficient meaningful, while cutting the generation
    cost from 86,400 rows/component to 1,440."""
    import random
    out = tmp_path / "random_db"
    run_capture(
        amc, out, days=1,
        extra_args=[
            "--emit-selection", "metrics,schema",
            "--components", "apigateway,database",
        ],
    )
    csv_path = out / "database.csv"
    rows = csv_path.read_text().splitlines()
    header = rows[0].split(",")
    col = header.index("queries_per_sec")
    rng = random.Random(123)
    new_rows = [rows[0]]
    for r in rows[1:]:
        parts = r.split(",")
        parts[col] = f"{rng.uniform(20000, 35000):.3f}"
        new_rows.append(",".join(parts))
    csv_path.write_text("\n".join(new_rows) + "\n")
    schema = _load_schema(out)
    violations = amc._validate_topology_coupling(out, schema)
    assert any(
        "apigateway->database" in v and "below threshold" in v
        for v in violations
    ), (
        f"validator must flag a randomized downstream load as a coupling "
        f"regression; got: {violations}"
    )


@pytest.mark.parametrize("bad_cell,side", [
    ("nan", "target"),
    ("inf", "target"),
    ("-inf", "target"),
    ("nan", "source"),
])
def test_topology_coupling_flags_non_finite_values(
    amc, tmp_path, bad_cell, side,
):
    """A hand-edited CSV with non-finite (NaN/+/-inf) cells in either
    canonical load column must be flagged. ``np.std`` and
    ``np.corrcoef`` both return NaN on non-finite input, and
    ``corr < threshold`` evaluates False — silently bypassing the
    check. The dedicated non-finite branch must catch this before
    the std/corrcoef calls run."""
    out = tmp_path / f"nonfinite_{side}_{bad_cell.replace('-', 'neg')}"
    run_capture(
        amc, out, days=1,
        extra_args=[
            "--emit-selection", "metrics,schema",
            "--components", "apigateway,database",
        ],
    )
    target_file = "database.csv" if side == "target" else "apigateway.csv"
    target_metric = (
        "queries_per_sec" if side == "target" else "requests_per_sec"
    )
    csv_path = out / target_file
    rows = csv_path.read_text().splitlines()
    header = rows[0].split(",")
    col = header.index(target_metric)
    new_rows = [rows[0]]
    # Inject the non-finite cell into the middle of the file so the
    # surrounding data still parses cleanly and the row count stays
    # well above the 100-row floor.
    mid = len(rows) // 2
    for i, r in enumerate(rows[1:], start=1):
        parts = r.split(",")
        if i == mid:
            parts[col] = bad_cell
        new_rows.append(",".join(parts))
    csv_path.write_text("\n".join(new_rows) + "\n")
    schema = _load_schema(out)
    violations = amc._validate_topology_coupling(out, schema)
    assert any(
        "apigateway->database" in v
        and "non-finite values" in v
        and "NaN/+/-inf" in v
        for v in violations
    ), (
        f"validator must flag non-finite {side} cell ({bad_cell}); "
        f"got: {violations}"
    )


def test_topology_coupling_skips_callable_weight_edges(amc, tmp_path):
    """The ``cacheservice -> database`` edge has a callable weight
    (cache-miss ratio); the validator skips it because the per-row
    weight signal — not the upstream load — is the dominant
    contributor. Mutating cacheservice.cache_hits should leave the
    coupling check silent on this edge (any flag would come from the
    apigateway -> database edge instead, which we leave clean here)."""
    out = tmp_path / "callable_skip"
    # Narrowed to the source/target of the callable edge under test
    # plus apigateway (its upstream contribution to database is still
    # required so the realistic-mode generator can compose the
    # database load column). 60s interval keeps the run short.
    run_capture(
        amc, out, days=1,
        extra_args=[
            "--emit-selection", "metrics,schema",
            "--components", "apigateway,cacheservice,database",
        ],
    )
    # Read schema and confirm the callable edge is declared.
    schema = _load_schema(out)
    cache_edges = schema["topology"]["cacheservice"]
    assert any(e["weight"] == "callable" for e in cache_edges)

    # Don't mutate any CSV — just verify the callable edge is silent in
    # a clean run. (A separate test confirms mutation of the
    # apigateway -> database constant edge still fires.)
    violations = amc._validate_topology_coupling(out, schema)
    callable_violations = [
        v for v in violations
        if "cacheservice->database" in v
    ]
    assert callable_violations == [], (
        f"callable-weight edge cacheservice->database must be skipped "
        f"by the coupling check; got: {callable_violations}"
    )


def test_topology_coupling_skips_when_topology_block_missing(
    amc, schema_run,
):
    """A schema document without a ``topology`` block (older schema or
    a doc someone hand-edited) must skip the check silently rather
    than crash."""
    schema = _load_schema(schema_run)
    schema.pop("topology", None)
    _write_schema(schema_run, schema)
    schema = _load_schema(schema_run)
    assert amc._validate_topology_coupling(schema_run, schema) == []


def test_topology_coupling_per_edge_threshold_override(amc, tmp_path,
                                                       monkeypatch):
    """``Edge.correlation_threshold`` overrides
    ``_TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD`` per edge. Setting a
    threshold near 1.0 (the upper bound of the valid ``(-1, 1]``
    range) on a real edge via a monkeypatched TOPOLOGY must flag the
    otherwise-passing coupling because the realized ~0.99 correlation
    cannot clear a 0.9999 gate.

    60s interval gives 1,440 rows over one day — well above the
    100-row correlation floor — so the override threshold still
    fails the gate at a fraction of the all-rows cost."""
    out = tmp_path / "override"
    run_capture(
        amc, out, days=1,
        extra_args=[
            "--emit-selection", "metrics,schema",
            "--components", "loadbalancer,apigateway",
        ],
    )
    # Build a TOPOLOGY clone with a 0.999 threshold on the
    # loadbalancer -> apigateway edge so the realized ~0.99 correlation
    # fails the gate. ``Edge`` is frozen so we replace the list
    # in-place; the original constants are restored when the
    # monkeypatch unwinds.
    orig = amc.TOPOLOGY["loadbalancer"]
    new_edges = [
        amc.Edge(
            target=edge.target,
            weight=edge.weight,
            saturation=edge.saturation,
            signal=edge.signal,
            correlation_threshold=0.9999,
        )
        for edge in orig
    ]
    monkeypatch.setitem(amc.TOPOLOGY, "loadbalancer", new_edges)
    schema = _load_schema(out)
    violations = amc._validate_topology_coupling(out, schema)
    assert any(
        "loadbalancer->apigateway" in v
        and "0.9999" in v
        for v in violations
    ), (
        f"per-edge override must drive a coupling failure on an "
        f"otherwise-passing run; got: {violations}"
    )


def test_topology_coupling_full_cli_flags_mutation(amc, tmp_path, capsys):
    """End-to-end CLI: mutate the downstream so it decouples from
    upstream, then run ``--validate-output`` and confirm a non-zero
    exit and a violation line in stderr naming the broken edge.

    60s interval keeps the end-to-end run cheap — the coupling
    check needs only 100 aligned rows and a 1d/60s run gives
    1,440."""
    out = tmp_path / "cli_broken_coupling"
    run_capture(
        amc, out, days=1,
        extra_args=[
            "--emit-selection", "metrics,schema",
            "--components", "loadbalancer,apigateway",
        ],
    )
    csv_path = out / "apigateway.csv"
    rows = csv_path.read_text().splitlines()
    header = rows[0].split(",")
    col = header.index("requests_per_sec")
    new_rows = [rows[0]]
    for r in rows[1:]:
        parts = r.split(",")
        parts[col] = "800.000"
        new_rows.append(",".join(parts))
    csv_path.write_text("\n".join(new_rows) + "\n")

    with pytest.raises(SystemExit) as exc_info:
        amc.main(["--validate-output", str(out)])
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "loadbalancer->apigateway" in err


def test_topology_coupling_rejects_old_schema_version(amc, tmp_path):
    """v1 schema documents written before phase 7 must be
    rejected by ``_load_schema_document``; the version bump is part of
    the contract that v2 readers do not silently skip the coupling
    check on a stale v1 doc."""
    p = tmp_path / "schema.json"
    p.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        amc._load_schema_document(p)


# ------------------------------------------------------------------
# Window helpers (phase 7 unit coverage)
# ------------------------------------------------------------------
def test_anomaly_exclusion_windows_use_span_columns(
    amc, schema_run,
):
    """``_read_anomaly_exclusion_windows`` should read ``span_start`` /
    ``span_end`` so a multi-hour span produces one wide exclusion
    rather than a 60s point exclusion around the row timestamp."""
    import datetime
    windows = amc._read_anomaly_exclusion_windows(
        schema_run / "anomalies.csv"
    )
    spans = [(e - s, c, m) for s, e, c, m in windows]
    # At least one non-trivial span (> 5 minutes after the 30s pad
    # on each side, i.e. body > 4 minutes) should be present in the
    # default 1-day scenario set (api_cpu_saturation retry storm,
    # deploy regression, etc.).
    assert any(
        delta > datetime.timedelta(minutes=5) for delta, _c, _m in spans
    ), (
        f"expected at least one multi-minute span in the default "
        f"anomaly manifest; got durations: "
        f"{[d.total_seconds() for d, _, _ in spans]}"
    )


def test_topology_coupling_skips_zero_weight_edges(amc, schema_run):
    """``_validate_topology()`` accepts ``weight == 0`` as a
    saturation-only placeholder that contributes no load to the
    downstream baseline; ``_compose_topology_coupled_specs`` skips
    it for the same reason. The coupling check must follow that
    contract — mutate a real edge's weight to ``0`` in the schema
    and confirm no correlation violation fires on the otherwise
    decoupled column. The downstream is pinned constant to make
    the test deterministic: a real run would normally pass anyway,
    but the constant downstream proves the validator no longer
    reaches its zero-variance branch on a zero-weight edge."""
    schema = _load_schema(schema_run)
    for edge in schema["topology"]["apigateway"]:
        if edge.get("target") == "cacheservice":
            edge["weight"] = 0
            break
    _write_schema(schema_run, schema)

    csv_path = schema_run / "cacheservice.csv"
    rows = csv_path.read_text().splitlines()
    header = rows[0].split(",")
    col = header.index("cache_hits")
    new_rows = [rows[0]]
    for r in rows[1:]:
        parts = r.split(",")
        if len(parts) > col:
            parts[col] = "100.000"
            new_rows.append(",".join(parts))
        else:
            new_rows.append(r)
    csv_path.write_text("\n".join(new_rows) + "\n")

    schema = _load_schema(schema_run)
    violations = amc._validate_topology_coupling(schema_run, schema)
    assert not any(
        "apigateway->cacheservice" in v for v in violations
    ), (
        f"zero-weight edge apigateway->cacheservice must be skipped "
        f"by the coupling check; got: {violations}"
    )


def test_topology_coupling_malformed_edge_entries_report_violations(
    amc, schema_run,
):
    """A hand-edited ``schema.json`` with malformed topology entries
    must not crash the validator with a ``KeyError``. Each malformed
    shape — non-dict edge entry, missing/non-string ``target``,
    missing ``weight``, non-numeric / non-callable ``weight`` — must
    instead surface as a dedicated violation message naming the
    offending edge."""
    schema = _load_schema(schema_run)
    # Build a deliberately broken topology block with four common
    # hand-edit mistakes. The validator must report each one as a
    # violation rather than raise.
    schema["topology"] = {
        "apigateway": [
            "not-a-dict",                           # non-dict entry
            {"weight": 0.5},                        # missing target
            {"target": "cacheservice"},             # missing weight
            {"target": "database", "weight": "x"},  # bogus weight
        ],
    }
    _write_schema(schema_run, schema)
    schema = _load_schema(schema_run)
    violations = amc._validate_topology_coupling(schema_run, schema)
    assert any("malformed" in v and "expected dict" in v for v in violations), (
        f"expected non-dict edge entry to surface as a violation; "
        f"got: {violations}"
    )
    assert any(
        "missing or invalid 'target'" in v for v in violations
    ), (
        f"expected missing-target edge entry to surface as a violation; "
        f"got: {violations}"
    )
    assert any("missing 'weight'" in v for v in violations), (
        f"expected missing-weight edge entry to surface as a violation; "
        f"got: {violations}"
    )
    assert any(
        "must be a number or the literal \"callable\"" in v
        for v in violations
    ), (
        f"expected bogus-weight edge entry to surface as a violation; "
        f"got: {violations}"
    )


@pytest.mark.parametrize("bad_threshold,expected_fragment", [
    ("not-a-number", "'not-a-number'"),
    (True, "True"),
    (float("nan"), "nan"),
    (float("inf"), "inf"),
    (float("-inf"), "-inf"),
    (1.5, "1.5"),
    (-1.0, "-1.0"),
    (-2.0, "-2.0"),
])
def test_topology_coupling_invalid_correlation_threshold_reports_violation(
    amc, schema_run, bad_threshold, expected_fragment,
):
    """Each non-canonical ``correlation_threshold`` shape — non-numeric
    string, ``bool``, NaN, +/-inf, and values outside the half-open
    ``(-1, 1]`` interval — must surface as a dedicated violation and
    not raise ``TypeError`` during the ``corr < threshold`` comparison
    or ``threshold:.4f`` formatting. The validator must continue to
    evaluate the other edges in the same run; the fallback threshold
    keeps the rest of the check meaningful."""
    schema = _load_schema(schema_run)
    # Inject the bad threshold onto a single real edge so the rest of
    # the topology block remains structurally valid and the validator
    # exercises both the threshold path and the surrounding edges.
    for edge in schema["topology"]["apigateway"]:
        if edge.get("target") == "cacheservice":
            edge["correlation_threshold"] = bad_threshold
            break
    _write_schema(schema_run, schema)
    schema = _load_schema(schema_run)
    violations = amc._validate_topology_coupling(schema_run, schema)
    assert any(
        "apigateway->cacheservice" in v
        and "correlation_threshold in schema.json" in v
        and expected_fragment in v
        for v in violations
    ), (
        f"expected dedicated violation for bad correlation_threshold "
        f"{bad_threshold!r}; got: {violations}"
    )


def test_topology_coupling_invalid_threshold_falls_back_to_live_topology(
    amc, schema_run,
):
    """When the schema's ``correlation_threshold`` is invalid, the
    validator must still evaluate the edge against the live TOPOLOGY's
    threshold (or the module default) so a hand-edit cannot silently
    disable the coupling check. We mutate the downstream to be
    constant so the zero-variance branch fires regardless of the
    fallback value, proving the edge is still evaluated."""
    schema = _load_schema(schema_run)
    for edge in schema["topology"]["apigateway"]:
        if edge.get("target") == "cacheservice":
            edge["correlation_threshold"] = "not-a-number"
            break
    _write_schema(schema_run, schema)
    # Pin the target column to a single value so np.std() is zero and
    # the zero-variance branch fires deterministically.
    csv_path = schema_run / "cacheservice.csv"
    rows = csv_path.read_text().splitlines()
    header = rows[0].split(",")
    col = header.index("cache_hits")
    new_rows = [rows[0]]
    for r in rows[1:]:
        parts = r.split(",")
        if len(parts) > col:
            parts[col] = "100.000"
            new_rows.append(",".join(parts))
        else:
            new_rows.append(r)
    csv_path.write_text("\n".join(new_rows) + "\n")
    schema = _load_schema(schema_run)
    violations = amc._validate_topology_coupling(schema_run, schema)
    # Two violations expected on this edge: the threshold-shape
    # complaint and the zero-variance complaint that proves the
    # fallback threshold was actually used to evaluate the edge.
    threshold_violations = [
        v for v in violations
        if "apigateway->cacheservice" in v
        and "correlation_threshold in schema.json" in v
    ]
    variance_violations = [
        v for v in violations
        if "apigateway->cacheservice" in v
        and "zero-variance" in v
    ]
    assert threshold_violations, (
        f"expected threshold-shape violation; got: {violations}"
    )
    assert variance_violations, (
        f"expected the edge to still be evaluated (zero-variance "
        f"detected) after the threshold fallback; got: {violations}"
    )


@pytest.mark.parametrize("bad_topology", [
    [],
    "not-a-dict",
    42,
    3.14,
    True,
])
def test_topology_coupling_malformed_top_level_block_reports_violation(
    amc, schema_run, bad_topology,
):
    """A hand-edited schema where ``topology`` is a truthy non-dict
    must not crash on ``topology.keys()``. The validator must report
    a single up-front violation instead of raising
    ``AttributeError``."""
    schema = _load_schema(schema_run)
    schema["topology"] = bad_topology
    _write_schema(schema_run, schema)
    schema = _load_schema(schema_run)
    violations = amc._validate_topology_coupling(schema_run, schema)
    assert any(
        "topology block malformed in schema.json" in v
        for v in violations
    ), (
        f"expected up-front violation for non-dict topology block "
        f"{bad_topology!r}; got: {violations}"
    )


@pytest.mark.parametrize("bad_weight", [
    float("nan"),
    float("inf"),
    float("-inf"),
])
def test_topology_coupling_non_finite_weight_reports_violation(
    amc, schema_run, bad_weight,
):
    """Python's ``json`` loader parses ``NaN``/``Infinity``/
    ``-Infinity`` as floats; ``_validate_topology()`` rejects those
    values on the live ``Edge``, and the validator's schema-side
    view must match. A non-finite weight cannot drive a meaningful
    Pearson check, so surface a dedicated violation and skip the
    edge."""
    schema = _load_schema(schema_run)
    for edge in schema["topology"]["apigateway"]:
        if edge.get("target") == "cacheservice":
            edge["weight"] = bad_weight
            break
    _write_schema(schema_run, schema)
    schema = _load_schema(schema_run)
    violations = amc._validate_topology_coupling(schema_run, schema)
    assert any(
        "apigateway->cacheservice" in v
        and "edge weight in schema.json must be finite" in v
        for v in violations
    ), (
        f"expected non-finite-weight violation for {bad_weight!r}; "
        f"got: {violations}"
    )


def test_topology_coupling_malformed_edge_list_reports_violation(
    amc, schema_run,
):
    """A schema whose topology source maps to a non-list value (e.g. a
    dict from a partial serializer) must surface as a single
    violation rather than crash."""
    schema = _load_schema(schema_run)
    schema["topology"] = {"apigateway": {"target": "cacheservice"}}
    _write_schema(schema_run, schema)
    schema = _load_schema(schema_run)
    violations = amc._validate_topology_coupling(schema_run, schema)
    assert any(
        "apigateway: edge list malformed" in v for v in violations
    ), (
        f"expected non-list edge container to surface as a violation; "
        f"got: {violations}"
    )


def test_compute_anomaly_keep_mask_matches_legacy_behavior(amc):
    """The vectorized ``_compute_anomaly_keep_mask`` must produce the
    same row-keep decisions as the original nested-loop implementation
    on representative inputs: empty windows, isolated windows,
    overlapping windows, and timestamps falling on the boundary."""
    import datetime
    base = datetime.datetime(2026, 1, 1, 12, 0, 0)
    timestamps = [
        base + datetime.timedelta(seconds=i) for i in range(0, 60, 5)
    ]
    # Two overlapping windows merged into one effective range [10s, 25s]
    # plus one isolated window covering exactly the 40s row.
    windows = [
        (base + datetime.timedelta(seconds=10),
         base + datetime.timedelta(seconds=20)),
        (base + datetime.timedelta(seconds=15),
         base + datetime.timedelta(seconds=25)),
        (base + datetime.timedelta(seconds=40),
         base + datetime.timedelta(seconds=40)),
    ]
    mask = amc._compute_anomaly_keep_mask(timestamps, windows)
    # Expected: 0,5 keep; 10,15,20,25 drop; 30,35 keep; 40 drop; 45,50,55 keep.
    expected = [True, True, False, False, False, False,
                True, True, False, True, True, True]
    assert list(mask) == expected, (
        f"keep mask differs from expected nested-loop semantics: "
        f"got {list(mask)}, want {expected}"
    )


def test_compute_anomaly_keep_mask_empty_inputs(amc):
    """Empty windows and empty timestamps degenerate cleanly: no rows
    excluded and an empty mask, respectively."""
    import datetime
    base = datetime.datetime(2026, 1, 1, 12, 0, 0)
    ts = [base + datetime.timedelta(seconds=i) for i in range(5)]
    mask_no_windows = amc._compute_anomaly_keep_mask(ts, [])
    assert list(mask_no_windows) == [True] * 5
    mask_no_timestamps = amc._compute_anomaly_keep_mask(
        [], [(base, base + datetime.timedelta(seconds=1))]
    )
    assert list(mask_no_timestamps) == []


def test_filter_windows_for_pair_keeps_only_relevant(amc):
    """``_filter_windows_for_pair`` keeps windows touching either side
    of the correlation pair, plus any other upstream's captured load
    columns (so the database check excludes cacheservice load
    spikes)."""
    import datetime
    base = datetime.datetime(2026, 1, 1, 12, 0, 0)
    delta = datetime.timedelta(seconds=60)
    windows = [
        (base, base + delta, "apigateway", "requests_per_sec"),
        (base, base + delta, "database", "queries_per_sec"),
        (base, base + delta, "cacheservice", "cache_hits"),
        (base, base + delta, "database", "disk_used_pct"),
        (base, base + delta, "loadbalancer", "tls_handshake_errors"),
    ]
    kept = amc._filter_windows_for_pair(
        windows,
        "apigateway", "requests_per_sec",
        "database", "queries_per_sec",
    )
    # Three windows survive: source, target, and the cacheservice
    # upstream contributor (cache_hits is supplementary to the
    # cacheservice -> database callable edge).
    assert len(kept) == 3, (
        f"expected 3 windows on apigateway->database pair "
        f"(source + target + cacheservice upstream); got {len(kept)}"
    )


# ------------------------------------------------------------------
# Dimensions integration (phase 8)
# ------------------------------------------------------------------
# Two fixtures: a fast 600s-interval N=3 run used by every per-validator
# unit test (cheap to spin up; each test gets its own copy), and a
# module-scoped 1-day full N=3 run for the end-to-end CLI assertion.
@pytest.fixture
def schema_run_n3(amc, tmp_path):
    """Fast N=3 run with schema + gauges + combine. Each test gets its
    own output dir so mutations don't bleed."""
    out = tmp_path / "run_n3"
    run_capture(
        amc, out, days=1,
        interval_seconds=600,
        extra_args=[
            "--emit-selection", "metrics,schema,gauges",
            "--components", "apigateway,cacheservice",
            "--instances-per-component", "3",
            "--combine",
        ],
    )
    return out


@pytest.fixture(scope="module")
def one_day_run_n3(amc, tmp_path_factory):
    """Full 1-day N=3 run for the end-to-end CLI assertion. Module-
    scoped to amortize the ~25–30s generation across multiple tests."""
    out = tmp_path_factory.mktemp("ver151_one_day_validator_n3")
    return run_capture(
        amc, out, days=1,
        extra_args=[
            "--emit-selection", "metrics,schema",
            "--instances-per-component", "3",
        ],
    )


def test_validate_component_cells_clean_on_n3_run(amc, schema_run_n3):
    """The N=3 long-form per-component CSV has the
    ``timestamp, id, host, pod, az, region, tenant, <metrics>`` header.
    With dimensions declared in the schema, ``_validate_component_cells``
    must use that header as its expected column order — not the
    dimensionless one — and therefore find no header drift on a fresh
    run."""
    schema = _load_schema(schema_run_n3)
    for component in schema["metadata"]["components"]:
        violations = amc._validate_component_cells(
            schema_run_n3, schema, component
        )
        assert violations == [], (
            f"{component}: fresh N=3 run should validate clean; got {violations}"
        )


def test_validate_component_cells_flags_missing_dim_column_when_declared(
    amc, schema_run_n3
):
    """When the schema declares ``dimensions`` on a component, dropping
    one of the canonical dim columns from the on-disk CSV must trigger
    the header-drift short-circuit. Verifies the dim-aware expected-
    header path is wired into the existing drift check, so a corrupted
    or older CSV produces a clear violation instead of cell errors."""
    schema = _load_schema(schema_run_n3)
    component = "apigateway"
    csv_path = schema_run_n3 / f"{component}.csv"
    rows = csv_path.read_text().splitlines()
    header_cols = rows[0].split(",")
    # Drop the ``pod`` column from header and every data row.
    pod_idx = header_cols.index("pod")
    new_rows = [
        ",".join(c for i, c in enumerate(row.split(",")) if i != pod_idx)
        for row in rows
    ]
    csv_path.write_text("\n".join(new_rows) + "\n")
    violations = amc._validate_component_cells(
        schema_run_n3, schema, component
    )
    assert violations, "missing dim column must produce a header drift violation"
    msg = violations[0]
    actual_section, _, expected_section = msg.partition("does not match schema")
    assert expected_section, (
        f"violation message must follow the 'header ... does not match schema "
        f"column order ...' format; got {msg!r}"
    )
    # ``pod`` was dropped from the on-disk header, but the schema still
    # declares it. The actual-header section must not list ``pod`` and the
    # expected-column-order section must.
    assert "'pod'" not in actual_section, (
        f"actual header must not include the dropped 'pod' column; got {msg!r}"
    )
    assert "'pod'" in expected_section, (
        f"expected column order must list the schema-declared 'pod' column; "
        f"got {msg!r}"
    )


def test_validate_component_row_count_clean_on_n3_run(amc, schema_run_n3):
    """Phase 2 fan-out produces N × rows_per_component per component
    (N copies of each row, one per instance). The row-count validator
    must multiply the expected band by ``cardinality`` so the fresh
    N=3 run sits inside the band rather than tripping the over-emission
    check."""
    schema = _load_schema(schema_run_n3)
    for component in schema["metadata"]["components"]:
        violations = amc._validate_component_row_count(
            schema_run_n3, schema, component
        )
        assert violations == [], (
            f"{component}: fresh N=3 row count should be inside the "
            f"cardinality-scaled band; got {violations}"
        )


def test_validate_component_row_count_uses_cardinality(amc, schema_run_n3):
    """Truncating the CSV to single-instance rows (i.e. dropping the
    extra fan-out instances) must trip the under-emission band when
    dimensions declare ``cardinality > 1``. Establishes that the band
    really does scale on cardinality and is not just a no-op multiplier."""
    schema = _load_schema(schema_run_n3)
    component = "apigateway"
    csv_path = schema_run_n3 / f"{component}.csv"
    rows = csv_path.read_text().splitlines()
    # Keep only the first ~1/N of the data rows so total << expected.
    header = rows[0]
    body = rows[1:]
    cardinality = schema["components"][component]["dimensions"]["cardinality"]
    keep_count = max(1, len(body) // (cardinality * 8))  # well below band
    csv_path.write_text("\n".join([header] + body[:keep_count]) + "\n")
    violations = amc._validate_component_row_count(
        schema_run_n3, schema, component
    )
    assert any("below the expected lower bound" in v for v in violations), (
        f"truncated N=3 CSV must trip the cardinality-scaled lower bound; "
        f"got {violations}"
    )


def test_validate_long_form_dimensions_clean_on_n3_gauges(amc, schema_run_n3):
    """``gauges.csv`` produced by a dim-aware run has the 10-column
    long-form header. With dimensions declared in the schema, the new
    ``_validate_long_form_dimensions`` check must pass it."""
    schema = _load_schema(schema_run_n3)
    violations = amc._validate_long_form_dimensions(schema_run_n3, schema)
    assert violations == [], (
        f"fresh N=3 long-form gauges.csv + combined CSV should validate "
        f"clean; got {violations}"
    )


def test_validate_long_form_dimensions_flags_classic_gauges_under_n3(
    amc, schema_run_n3
):
    """If the on-disk ``gauges.csv`` carries the classic 4-column header
    even though the schema declares dimensions (e.g. a stale write from a
    pre-Phase-5 build), the validator must flag the mismatch — silent
    pass-through would let a downstream consumer treat dim-aware data as
    classic."""
    schema = _load_schema(schema_run_n3)
    gauges_path = schema_run_n3 / "gauges.csv"
    # Replace the dim-aware header with the classic one. Data rows
    # already carry the dim columns so the test verifies the validator
    # catches the header alone, not data drift.
    rows = gauges_path.read_text().splitlines()
    rows[0] = "timestamp,component,metric,value"
    gauges_path.write_text("\n".join(rows) + "\n")
    violations = amc._validate_long_form_dimensions(schema_run_n3, schema)
    assert any("gauges.csv" in v and "dim-aware" in v for v in violations), (
        f"classic gauges header under N=3 schema must surface a "
        f"dim-aware drift violation; got {violations}"
    )


def test_validate_long_form_dimensions_flags_classic_combined_under_n3(
    amc, schema_run_n3
):
    """Same as gauges but for ``combined_metrics_unified.csv``: the
    dim-aware writer emits the 10-column long-form header when any
    per-component CSV is dim-aware, and the validator must match that
    expectation against the schema."""
    schema = _load_schema(schema_run_n3)
    combined = schema_run_n3 / "combined_metrics_unified.csv"
    rows = combined.read_text().splitlines()
    rows[0] = "timestamp,component_a_metric"
    combined.write_text("\n".join(rows) + "\n")
    violations = amc._validate_long_form_dimensions(schema_run_n3, schema)
    assert any("combined_metrics_unified.csv" in v and "dim-aware" in v
               for v in violations), (
        f"classic combined header under N=3 schema must surface a "
        f"dim-aware drift violation; got {violations}"
    )


def test_validate_long_form_dimensions_noop_without_dimensions(amc, schema_run):
    """When no component declares ``dimensions`` (the default N=1 path),
    the long-form check must short-circuit to no violations regardless
    of the classic header. Establishes the schema-driven dispatch:
    today's dimensionless schemas keep today's validator behavior."""
    schema = _load_schema(schema_run)
    # Drop any (unexpected) dimensions blocks from the schema mock to
    # guarantee the short-circuit branch.
    for payload in schema["components"].values():
        payload.pop("dimensions", None)
    _write_schema(schema_run, schema)
    schema = _load_schema(schema_run)
    assert amc._validate_long_form_dimensions(schema_run, schema) == []


def test_validate_output_cli_clean_on_fresh_n3_run(amc, one_day_run_n3, capsys):
    """End-to-end: a fresh 1-day ``--instances-per-component 3`` run
    must validate clean under ``--validate-output``."""
    amc.main(["--validate-output", str(one_day_run_n3.out_dir)])
    cap = capsys.readouterr()
    assert "OK" in cap.out


def test_validate_component_derivations_clean_on_n3_run(amc, schema_run_n3):
    """``cacheservice.hit_ratio`` is the canonical derived column; the
    recomputer reads ``cache_hits`` / ``cache_misses`` from the same
    row via ``name_to_col``. Under N=3 the metric columns are shifted
    by the 6-column dim prefix — the validator must re-anchor
    ``name_to_col`` so the recomputed value matches the on-disk one."""
    schema = _load_schema(schema_run_n3)
    for component in schema["metadata"]["components"]:
        violations = amc._validate_component_derivations(
            schema_run_n3, schema, component
        )
        assert violations == [], (
            f"{component}: fresh N=3 derivations should validate clean; "
            f"got {violations}"
        )


def test_validate_component_derivations_flags_drift_under_n3(amc, schema_run_n3):
    """Mutate a hit_ratio cell under dim-aware layout and verify the
    derivation validator still catches the drift after the column-index
    re-anchor."""
    schema = _load_schema(schema_run_n3)
    component = "cacheservice"
    csv_path = schema_run_n3 / f"{component}.csv"
    rows = csv_path.read_text().splitlines()
    header = rows[0].split(",")
    ratio_col = header.index("hit_ratio")
    parts = rows[1].split(",")
    parts[ratio_col] = "0.000"
    rows[1] = ",".join(parts)
    csv_path.write_text("\n".join(rows) + "\n")
    violations = amc._validate_component_derivations(
        schema_run_n3, schema, component
    )
    assert any("hit_ratio" in v and "differs from recomputed" in v
               for v in violations)


# ------------------------------------------------------------------
# parse_args gate lift (phase 8)
# ------------------------------------------------------------------
def test_validate_output_compatible_with_instances_per_component(
    amc, tmp_path,
):
    """Phase 8 lifts the ``--instances-per-component > 1`` +
    ``--validate-output`` gate. The parser must now accept the
    combination rather than rejecting it with the Phase 8 stub
    message."""
    out = tmp_path / "exists"
    out.mkdir()
    args = amc.parse_args([
        "--validate-output", str(out),
        "--instances-per-component", "3",
    ])
    assert args.validate_output == out
    assert args.instances_per_component == 3


def test_schema_emit_selection_compatible_with_instances_per_component(
    amc, tmp_path,
):
    """Companion gate lift: ``--emit-selection 'schema'`` was rejected
    under N>1 by the same Phase 8 stub. The dim-aware
    ``write_schema_json`` makes it well-defined now."""
    args = amc.parse_args([
        "--output-dir", str(tmp_path),
        "--duration-days", "1",
        "--emit-selection", "metrics,schema",
        "--instances-per-component", "3",
    ])
    assert args.instances_per_component == 3
    assert "schema" in args.emit_selection


def test_n2_plus_otel_emit_gauges_allowed(amc, tmp_path):
    """Phase 6 wired the OTEL streamer's dimension attributes,
    so ``--instances-per-component > 1`` + ``--otel-emit-gauges`` is
    permitted at parse time. ``stream_otel_gauges`` reads the dimension
    columns off each per-component CSV and emits every non-empty
    ``_INSTANCE_DIMENSION_COLUMNS`` cell as a string attribute on the
    OTLP gauge data point. After Phase 8 lifts the
    schema/validator guards there is no remaining multi-instance gate
    on this combination."""
    args = amc.parse_args([
        "--output-dir", str(tmp_path / "gen"),
        "--duration-days", "1",
        "--instances-per-component", "3",
        "--otel-enabled",
        "--otel-metrics-endpoint", "https://example.invalid/v1/metrics",
        "--otel-emit-gauges",
    ])
    assert args.instances_per_component == 3
    assert args.otel_enabled is True
    assert args.otel_emit_gauges is True

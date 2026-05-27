"""Tests for --instances-per-component (Phase 2).

Verifies:
- N=1 (default) produces byte-identical output to omitting the flag.
- N=3 fans each component out to 3 instances with correct row count,
  column header, and pod values.
- Invalid N values (0, 21) are rejected at parse time.
- PREFLIGHT_CELL_CAP is multiplied by N.
"""

import csv
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import run_capture

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "anomaly-metric-creator.py"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(amc, out_dir, *, days, extra_args=None, interval_seconds=1.0):
    """Thin wrapper over conftest.run_capture that throws away the stderr
    SimpleNamespace so existing call sites can keep returning ``out_dir``.

    ``interval_seconds=1.0`` (default) preserves the full-resolution locked
    N3_ONE_DAY_HASHES / N3_SEVEN_DAY_HASHES.

    Routing through the shared helper keeps the suite's single
    session-scoped ``amc`` module load (see conftest._load_amc) shared
    with the rest of the test suite — re-importing via
    ``spec_from_file_location`` from this file would double the
    registry-build cost.
    """
    captured = run_capture(amc, out_dir, days=days, extra_args=extra_args,
                           interval_seconds=interval_seconds)
    return captured.out_dir


def _invoke(args, *, expect_fail=False):
    """Run the script as a subprocess and return the ``CompletedProcess``.

    ``expect_fail=True`` asserts non-zero exit; the default (``False``)
    asserts zero exit. The returned object exposes ``returncode``,
    ``stdout``, and ``stderr`` for assertion in the calling test.
    """
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)] + args,
        capture_output=True, text=True,
    )
    if expect_fail:
        assert result.returncode != 0, f"Expected non-zero exit; got 0. stderr: {result.stderr}"
    else:
        assert result.returncode == 0, f"Expected exit 0; got {result.returncode}. stderr: {result.stderr}"
    return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
#
# The ``amc`` fixture is the session-scoped one from ``tests/conftest.py``;
# we deliberately do not override it here so the whole suite shares a
# single ``exec_module`` build of the registry (see
# ``tests/conftest.py::_load_amc`` for the memoization).


@pytest.fixture(scope="module")
def default_1d(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("inst_default_1d")
    _run(amc, out, days=1)
    return out


@pytest.fixture(scope="module")
def n1_explicit_1d(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("inst_n1_1d")
    _run(amc, out, days=1, extra_args=["--instances-per-component", "1"])
    return out


@pytest.fixture(scope="module")
def n3_1d(n3_one_day_dataset_dir):
    """N=3 1-day dataset for this module's per-component CSV / anomalies
    checks. Delegates to the session-scoped ``n3_one_day_dataset_dir``
    fixture in ``conftest.py`` so the ~25-second / ~1.3 GB
    generation pass runs once for the whole suite instead of once per
    test file. Every consumer in this module only reads per-component
    CSVs or ``anomalies.csv``; the shared fixture's
    ``--emit-selection metrics`` is exactly that set."""
    return n3_one_day_dataset_dir


@pytest.fixture(scope="module")
def n3_7d(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("inst_n3_7d")
    _run(amc, out, days=7, extra_args=["--instances-per-component", "3"])
    return out


# ---------------------------------------------------------------------------
# Byte-identity: N=1 (explicit) == default
# ---------------------------------------------------------------------------

def test_n1_byte_identical_to_default(amc, default_1d, n1_explicit_1d):
    """--instances-per-component 1 must produce byte-for-byte identical CSVs."""
    components = list(amc.COMPONENTS.keys())
    assert len(components) > 0
    for name in components:
        fname = f"{name}.csv"
        default_hash = _sha256(default_1d / fname)
        n1_hash = _sha256(n1_explicit_1d / fname)
        assert default_hash == n1_hash, (
            f"{fname}: N=1 hash {n1_hash!r} != default hash {default_hash!r}"
        )


# ---------------------------------------------------------------------------
# Row count: N=3 produces exactly 3× rows
# ---------------------------------------------------------------------------

def test_n3_row_count(amc, default_1d, n3_1d):
    """Each per-component CSV with N=3 has exactly 3× the default row count."""
    for name in amc.COMPONENTS:
        fname = f"{name}.csv"
        with open(default_1d / fname) as f:
            default_data_rows = sum(1 for _ in f) - 1  # subtract header
        with open(n3_1d / fname) as f:
            n3_data_rows = sum(1 for _ in f) - 1
        assert n3_data_rows == 3 * default_data_rows, (
            f"{fname}: expected {3 * default_data_rows} rows with N=3, "
            f"got {n3_data_rows}"
        )


# ---------------------------------------------------------------------------
# Column header: timestamp,id,host,pod,az,region,tenant,<metrics...>
# ---------------------------------------------------------------------------

DIM_COLUMNS = ["id", "host", "pod", "az", "region", "tenant"]
TIMESTAMP_COL = "timestamp"


def test_n3_header_has_dimension_columns(amc, n3_1d):
    """N=3 CSVs have timestamp,id,host,pod,az,region,tenant,<metrics> header."""
    for name in amc.COMPONENTS:
        fname = f"{name}.csv"
        with open(n3_1d / fname) as f:
            header = f.readline().rstrip("\n").split(",")
        assert header[0] == TIMESTAMP_COL, f"{fname}: first column is not 'timestamp'"
        for i, dim in enumerate(DIM_COLUMNS, start=1):
            assert header[i] == dim, (
                f"{fname}: expected column {i} to be {dim!r}, got {header[i]!r}"
            )
        # Metric columns follow
        expected_metrics = [m.name for m in amc.COMPONENTS[name][
            : amc.DEFAULT_METRICS_PER_COMPONENT[name]
        ]]
        assert len(expected_metrics) > 0
        actual_metrics = header[1 + len(DIM_COLUMNS):]
        assert actual_metrics == expected_metrics, (
            f"{fname}: metric columns after dims don't match spec"
        )


def test_n1_header_has_no_dimension_columns(amc, n1_explicit_1d):
    """N=1 (anonymous instance) CSVs have timestamp,<metrics> with no dim columns."""
    for name in amc.COMPONENTS:
        fname = f"{name}.csv"
        with open(n1_explicit_1d / fname) as f:
            header = f.readline().rstrip("\n").split(",")
        assert header[0] == TIMESTAMP_COL
        for dim in DIM_COLUMNS:
            assert dim not in header, (
                f"{fname}: unexpected dimension column {dim!r} in N=1 output"
            )


# ---------------------------------------------------------------------------
# Pod values: pod-0, pod-1, pod-2 in stable order
# ---------------------------------------------------------------------------

def test_n3_pod_values(amc, n3_1d):
    """N=3 CSVs cycle through pod-0, pod-1, pod-2 (one full block per pod)."""
    for name in amc.COMPONENTS:
        fname = f"{name}.csv"
        with open(n3_1d / fname) as f:
            reader = csv.DictReader(f)
            pod_values = [row["pod"] for row in reader]
        assert len(pod_values) > 0, f"{fname}: no data rows"
        n = len(pod_values) // 3
        assert n > 0
        # All rows in block 0 have pod-0, block 1 have pod-1, block 2 have pod-2
        assert all(v == "pod-0" for v in pod_values[:n]), f"{fname}: block 0 not all pod-0"
        assert all(v == "pod-1" for v in pod_values[n: 2 * n]), f"{fname}: block 1 not all pod-1"
        assert all(v == "pod-2" for v in pod_values[2 * n:]), f"{fname}: block 2 not all pod-2"


def test_n3_id_values(amc, n3_1d):
    """N=3 CSVs cycle through id i0, i1, i2."""
    for name in amc.COMPONENTS:
        fname = f"{name}.csv"
        with open(n3_1d / fname) as f:
            reader = csv.DictReader(f)
            id_values = [row["id"] for row in reader]
        n = len(id_values) // 3
        assert all(v == "i0" for v in id_values[:n])
        assert all(v == "i1" for v in id_values[n: 2 * n])
        assert all(v == "i2" for v in id_values[2 * n:])


# ---------------------------------------------------------------------------
# Dimension fields not set by Phase 2 are empty strings in the CSV
# ---------------------------------------------------------------------------

def test_n3_unset_dims_are_empty(amc, n3_1d):
    """host, az, region, tenant are empty (not 'None') in N=3 Phase-2 output."""
    for name in amc.COMPONENTS:
        fname = f"{name}.csv"
        with open(n3_1d / fname) as f:
            reader = csv.DictReader(f)
            first_row = next(reader)
        for dim in ("host", "az", "region", "tenant"):
            assert first_row[dim] == "", (
                f"{fname}: expected empty string for {dim!r}, got {first_row[dim]!r}"
            )


# ---------------------------------------------------------------------------
# CLI rejection: out-of-range values
# ---------------------------------------------------------------------------

def test_instances_per_component_zero_rejected(tmp_path):
    result = _invoke(
        ["--instances-per-component", "0",
         "--output-dir", str(tmp_path), "--duration-days", "1"],
        expect_fail=True,
    )
    assert "instances-per-component" in result.stderr.lower()


def test_instances_per_component_over_max_rejected(amc, tmp_path):
    over = str(amc.MAX_INSTANCES_PER_COMPONENT + 1)
    result = _invoke(
        ["--instances-per-component", over,
         "--output-dir", str(tmp_path), "--duration-days", "1"],
        expect_fail=True,
    )
    assert "instances-per-component" in result.stderr.lower()


def test_instances_per_component_range_error_precedes_gating(amc, tmp_path):
    """An out-of-range N paired with a still-gated flag surfaces the
    range error, not the incompatibility error.

    Without explicit ordering the user would see "incompatible with
    --validate-output" for ``--instances-per-component 999
    --validate-output ...`` and waste time looking for a Phase 8 fix
    when the real problem is the invalid N. The range check is run
    *before* every N>1 gate in ``parse_args``. After phase 5
    lifted the ``--combine`` / ``--combine-only`` / ``--emit-selection
    gauges`` gates, ``--validate-output`` (Phase 8) is the canonical
    still-gated flag to exercise this precedence invariant against.
    """
    over = str(amc.MAX_INSTANCES_PER_COMPONENT + 1)
    result = _invoke(
        ["--instances-per-component", over,
         "--validate-output", str(tmp_path),
         "--duration-days", "1"],
        expect_fail=True,
    )
    stderr_low = result.stderr.lower()
    assert "must be in [1," in stderr_low
    # The Phase 8 incompatibility message must not fire — the range
    # error takes precedence and exits before the gate is reached.
    assert "incompatible with --validate-output" not in stderr_low


# ---------------------------------------------------------------------------
# Dimension-field validation (extended in Phase 2 to cover host/pod/az/region/tenant)
# ---------------------------------------------------------------------------

def test_validate_instance_list_rejects_non_string_dim_field(amc):
    """A non-string dim field (e.g. an int ``pod``) raises ValueError at
    validation time rather than TypeError-ing inside the CSV writer."""
    bad = [amc.Instance(id="i0", pod=42)]
    with pytest.raises(ValueError, match="dimension fields must be None or a string"):
        amc._validate_instance_list(bad, where="test")


def test_validate_instance_list_rejects_comma_in_dim_field(amc):
    """A comma in a dim field would corrupt the long-form CSV (no quoting);
    validation rejects it up-front."""
    bad = [amc.Instance(id="i0", host="bad,host")]
    with pytest.raises(ValueError, match="CSV-significant characters"):
        amc._validate_instance_list(bad, where="test")


def test_validate_instance_list_rejects_newline_in_dim_field(amc):
    """A newline in a dim field would split one CSV row into two; validation
    rejects it up-front."""
    bad = [amc.Instance(id="i0", tenant="bad\ntenant")]
    with pytest.raises(ValueError, match="CSV-significant characters"):
        amc._validate_instance_list(bad, where="test")


def test_validate_instance_list_rejects_comma_in_id(amc):
    """Same protection applies to ``id``: a comma there would break the
    long-form ``id`` column."""
    bad = [amc.Instance(id="bad,id")]
    with pytest.raises(ValueError, match="CSV-significant characters"):
        amc._validate_instance_list(bad, where="test")


def test_validate_instance_list_accepts_normal_string_dims(amc):
    """Sanity check: a fully-populated, comma-free instance validates."""
    good = [amc.Instance(
        id="i0", host="host-0", pod="pod-0",
        az="us-east-1a", region="us-east-1", tenant="tenant-a",
    )]
    amc._validate_instance_list(good, where="test")  # no raise


# ---------------------------------------------------------------------------
# DST artifact mutual exclusion: N>1 + --inject-dst-artifact-day rejected
# ---------------------------------------------------------------------------

def test_dst_artifact_with_multi_instance_rejected(amc, tmp_path):
    """--instances-per-component > 1 + --inject-dst-artifact-day > 0 is
    rejected at parse time; the multi-instance long-form writer rebuilds
    rows from the pre-splice timestamp arrays and would silently drop
    the DST duplicate hour."""
    result = _invoke(
        ["--instances-per-component", "2",
         "--inject-dst-artifact-day", "1",
         "--output-dir", str(tmp_path), "--duration-days", "1"],
        expect_fail=True,
    )
    stderr_low = result.stderr.lower()
    assert "instances-per-component" in stderr_low
    assert "inject-dst-artifact-day" in stderr_low


# ---------------------------------------------------------------------------
# PREFLIGHT_CELL_CAP: N multiplies the estimate
# ---------------------------------------------------------------------------

def test_preflight_cap_multiplied_by_n(amc, tmp_path):
    """A run that passes with N=1 trips the cap when N pushes it over."""
    # Both invocations use the default --components all selector. With 14
    # components each emitting their default metric counts (4–10 per
    # component), ``total_metrics`` is 85 metrics summed across the run.
    # The cap formula is rows_per_component * total_metrics * N. This test
    # opts into 1s sampling explicitly because the CLI default is now 60s.
    #
    # First leg: 86400 rows * 85 metrics * 1 instance = ~7.3M cells —
    # well under the 200M cap. Pair --allow-huge-output so the assertion
    # only exercises argument parsing (no generation) and stays fast.
    args_n1 = amc.parse_args([
        "--output-dir", str(tmp_path),
        "--duration-days", "1",
        "--interval-seconds", "1.0",
        "--instances-per-component", "1",
        "--allow-huge-output",
    ])
    assert args_n1.instances_per_component == 1

    # Second leg: same default --components all, but 7 days and N=20.
    # 604800 rows * 85 metrics * 20 instances = ~1.03B cells > 200M cap,
    # so parse_args must reject without --allow-huge-output. (At 1 day
    # the same N=20 would land at ~130M, under the cap — the 7-day knob
    # is what trips it deterministically.)
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--output-dir", str(tmp_path),
            "--duration-days", "7",
            "--interval-seconds", "1.0",
            "--instances-per-component", "20",
        ])


# ---------------------------------------------------------------------------
# Locked N=3 golden hashes (1-day and 7-day) at --seed 42.
#
# Captured on the Phase 2 landing. These pin the
# per-component CSV bytes under --instances-per-component 3 so any future
# change to the long-form dimension prefix, the per-instance row ordering, or
# the RNG-sharing-across-instances contract trips the regression here.
#
# The anomalies.csv hash matches DEFAULT_ONE_DAY_HASHES / DEFAULT_SEVEN_DAY_HASHES
# in tests/test_scenarios.py because anomalies are not duplicated per instance
# in v1 — anomaly rows record one event per (timestamp, component, metric)
# regardless of how many instances the component fans out to. Phase 4 (the
# instance_filter feature) will reshape that contract; until then, the
# anomalies hash here is intentionally identical to the default-run hash.
# ---------------------------------------------------------------------------

N3_ONE_DAY_HASHES = {
    "anomalies.csv": "954cc16cf814a78ea26b309ebb8223a44f7603884b7f9cf10ba7bd76de701615",
    "apigateway.csv": "cdabce8bbe7a4fb0df54381c7163cc4c7a3fadb81a93dd9c202982c6b0770bb3",
    "authservice.csv": "ceacd47fa7f3009b589161552a926652e69846723cce104b4b43ec14c32c3939",
    "cacheservice.csv": "2761a8023acc6b833fb36de1751da775de929138d1d13258e423a78e080e3075",
    "database.csv": "e3a135bced1d1812c241e632afe9983a285adabaa86c62a408b5475f62091bb1",
    "gpu_inference.csv": "a29426066a981d8ac7f80652cc12f608326edb4fd2d5e13c9b854fd8016964c7",
    "identityprovider.csv": "9169e5f4afb16012e3883c0a75a538264818e92254126be9319a0826c185e8b5",
    "llm_analytics.csv": "6ed6fab0811dc944879158b2266179caf92c0e2791e3d0de0b8af11d93fe56cb",
    "loadbalancer.csv": "4c43ea90ec40b6a8380329251f2c5bd6ab4c4f62bf38974d3b33fde6a4e3a02a",
    "mqservice.csv": "5f1e819c9a53b4576d6aea3cb2f2130c293d9d8abbf407e9e333cf688f0da627",
    "objectstore.csv": "1c414d37d479812e9c3235ab90a0384634e63db923134d5586f11d7ddcc5d77b",
    "observabilitypipeline.csv": "dec99e71f314132c1479bf47c4a92665bda6e71dd745052dca9215335cee9957",
    "paymentservice.csv": "03cb4e015801229407194a68fcb1416d464c56d53111013abf0a18bf3c51bcad",
    "scheduler.csv": "a4693ff06fc227d8d54b4b81f2eb5c0bbe59a119dc89f2ed757cd5611240ed67",
    "vectorstore.csv": "54d5c49b4c7b8d29fd02a3b5938f2757bd25f30bfc01ac1a67df1bda6709f2b8",
}

N3_SEVEN_DAY_HASHES = {
    "anomalies.csv": "9c31ede26ec85676f7d9f143617485b4f68b9374bfae30aca3a1c4f051537ba7",
    "apigateway.csv": "6c836c777b55971ae7a4305d80331f9a84d62e26f21a2990a153aad50143b949",
    "authservice.csv": "bd88cf284e1b4d9adbc4d4c08920b039b405a545e3423bd3bc8631906508a62c",
    "cacheservice.csv": "27cdcb2e58b9c92780c43f1b828448daef9155297dd71682d16b9c3db7fec84c",
    "database.csv": "1cf587f7ded44c27c3f2bdcb2083d2f373ca29a82f28abf3b7b53e443f1b56d6",
    "gpu_inference.csv": "ca6fd8f1e7a4254ac1a1e23c537dfeaeb74bb208160297e7bd389e0b3ee62719",
    "identityprovider.csv": "f55e490418234429520b7d499b54aabfd23de7258deef77264cd5d538e43d9b9",
    "llm_analytics.csv": "04e929c6cdde9dc4a98bda8e7703f1e9224c995e87b639c7f918fc9be104fc09",
    "loadbalancer.csv": "20fc1c328b4b0807f4fcc30546773fb8ecd9c2265f72e2ed03edd68446c0e531",
    "mqservice.csv": "63993738714b36b2a1d852baac6b15a40e1923aa5d199f476465fa57ef21b59a",
    "objectstore.csv": "0170a990dd7852bbba418638bb1e02cce792bc7455b89874c388549f57e4490d",
    "observabilitypipeline.csv": "86d53c132e2f5a8207105b0f8880aecd862747c2c3cbc70fd71164bf7b30a328",
    "paymentservice.csv": "e7eb1562ea7279f8f98e3e74fe1326af72e7e3f920a067bd006e02263e57289c",
    "scheduler.csv": "fa8610dae0835de17f1b780ebad9d71c26e5715721769409b0a29e369eeca22f",
    "vectorstore.csv": "50ae5e406fe1f39178932287b3ff72bcce4dad0555b11211b6ba62ed00f0d5eb",
}


def test_n3_one_day_csvs_byte_identical(amc, n3_1d):
    """N=3 1-day per-component CSV + anomalies.csv match the locked golden hashes.

    Catches silent drift in the Phase 2 long-form writer (dimension prefix,
    per-instance row ordering, shared-RNG contract) and in the anomaly
    pipeline (which the issue locks at byte-identical to the default run).
    """
    assert N3_ONE_DAY_HASHES, "N3_ONE_DAY_HASHES must be non-empty"
    component_names = set(amc.COMPONENTS)
    assert component_names, "COMPONENTS registry is empty"
    for fname, expected in N3_ONE_DAY_HASHES.items():
        actual = _sha256(n3_1d / fname)
        assert actual == expected, (
            f"{fname}: N=3 1-day hash drifted. expected={expected} actual={actual}"
        )
    # Guard against silent registry drift adding a component the table forgot.
    component_files = {f"{name}.csv" for name in component_names}
    missing = component_files - set(N3_ONE_DAY_HASHES)
    assert not missing, (
        f"N3_ONE_DAY_HASHES is missing entries for components: {sorted(missing)}"
    )


def test_n3_seven_day_csvs_byte_identical(amc, n3_7d):
    """N=3 7-day per-component CSV + anomalies.csv match the locked golden hashes."""
    assert N3_SEVEN_DAY_HASHES, "N3_SEVEN_DAY_HASHES must be non-empty"
    component_names = set(amc.COMPONENTS)
    assert component_names, "COMPONENTS registry is empty"
    for fname, expected in N3_SEVEN_DAY_HASHES.items():
        actual = _sha256(n3_7d / fname)
        assert actual == expected, (
            f"{fname}: N=3 7-day hash drifted. expected={expected} actual={actual}"
        )
    component_files = {f"{name}.csv" for name in component_names}
    missing = component_files - set(N3_SEVEN_DAY_HASHES)
    assert not missing, (
        f"N3_SEVEN_DAY_HASHES is missing entries for components: {sorted(missing)}"
    )


def test_n3_1d_hashes_stable(amc, n3_1d, tmp_path_factory):
    """N=3 1-day output is byte-stable across two identical runs.

    Complements the locked-hash test: catches non-determinism that would
    surface as a re-run hash mismatch even if the locked hashes happen to
    match the first run by accident.

    The baseline ``n3_1d`` is the session-scoped
    ``n3_one_day_dataset_dir``, which uses
    ``--emit-selection metrics``. The second run mirrors that selection
    so the two outputs are comparable on the artifacts the per-component
    CSV hashes cover, and the second run avoids re-emitting the
    ~1.3 GB of logs/traces artifacts neither side compares against.
    Per-component CSV bytes are independent of ``--emit-selection``
    (the metric columns are written under any selection that includes
    ``metrics``), so this trims disk without changing the test's
    invariant.
    """
    out2 = tmp_path_factory.mktemp("inst_n3_1d_v2")
    _run(amc, out2, days=1, extra_args=[
        "--instances-per-component", "3",
        "--emit-selection", "metrics",
    ])
    for name in amc.COMPONENTS:
        fname = f"{name}.csv"
        assert _sha256(n3_1d / fname) == _sha256(out2 / fname), (
            f"{fname}: N=3 1-day output is not byte-stable"
        )


def test_n3_7d_hashes_stable(amc, n3_7d, tmp_path_factory):
    """N=3 7-day output is byte-stable across two identical runs."""
    out2 = tmp_path_factory.mktemp("inst_n3_7d_v2")
    _run(amc, out2, days=7, extra_args=["--instances-per-component", "3"])
    for name in amc.COMPONENTS:
        fname = f"{name}.csv"
        assert _sha256(n3_7d / fname) == _sha256(out2 / fname), (
            f"{fname}: N=3 7-day output is not byte-stable"
        )


# ---------------------------------------------------------------------------
# N=1 + DST: the positive control for the multi-instance DST guard
# (see test_dst_artifact_with_multi_instance_rejected above for the
# N>1 rejection case).
# ---------------------------------------------------------------------------


def test_instances_n1_with_dst_allowed(tmp_path):
    """--instances-per-component 1 with --inject-dst-artifact-day is fine."""
    _invoke(
        ["--instances-per-component", "1",
         "--inject-dst-artifact-day", "1",
         "--output-dir", str(tmp_path), "--duration-days", "1",
         "--components", "loadbalancer", "--interval-seconds", "60"],
        expect_fail=False,
    )


# ---------------------------------------------------------------------------
# Out-of-scope downstream emitters are gated for N > 1
# ---------------------------------------------------------------------------
#
# After phase 5 the file-form long-form writers
# (``combined_metrics_unified.csv`` and ``gauges.csv``) are dimension-
# aware: ``--instances-per-component > 1`` paired with ``--combine`` /
# ``--combine-only`` / ``--emit-selection gauges`` is now permitted and
# dispatches to the long-form layout. Phase 6 then made the
# OTEL streamer dimension-aware, so ``--otel-enabled`` /
# ``--otel-emit-gauges`` are also accepted under multi-instance runs.
# Phase 8 (this branch) closes the loop: ``schema.json``
# declares a per-component ``dimensions`` block and
# ``--validate-output`` walks the long-form headers end-to-end, so
# every downstream-flag combination above is now permitted at parse
# time. The only remaining multi-instance gate is the DST splice
# (``--inject-dst-artifact-day > 0``). After the long-form
# row builder routes through ``_format_csv_row_block`` and would
# apply the splice per-instance correctly, but the parse-time guard
# stays in place because per-instance non-monotonic timestamps inside
# each long-form row block cannot be merged downstream
# (``gauges.csv`` / ``combined_metrics_unified.csv``).


def test_n2_plus_combine_allowed(tmp_path):
    """Phase 5: ``--instances-per-component > 1`` + ``--combine``
    is now permitted. The combine writer dispatches to a long-form
    layout when the per-component CSVs carry the dimension prefix.
    """
    _invoke(
        ["--instances-per-component", "2", "--combine",
         "--components", "apigateway",
         "--output-dir", str(tmp_path), "--duration-days", "1",
         "--interval-seconds", "60"],
        expect_fail=False,
    )
    assert (tmp_path / "combined_metrics_unified.csv").exists()


def test_n2_plus_combine_only_allowed(tmp_path):
    """Phase 5: ``--instances-per-component > 1`` + ``--combine-only``
    is now permitted. A staged multi-instance directory is combined
    into a long-form unified CSV."""
    # Seed a single-component dimensioned directory first.
    _invoke(
        ["--instances-per-component", "2",
         "--components", "apigateway",
         "--output-dir", str(tmp_path), "--duration-days", "1",
         "--interval-seconds", "60"],
        expect_fail=False,
    )
    # combine_only over the staged dimensioned per-component CSV must
    # succeed and write the long-form unified CSV.
    _invoke(
        ["--instances-per-component", "2", "--combine-only",
         "--components", "apigateway",
         "--output-dir", str(tmp_path), "--duration-days", "1",
         "--interval-seconds", "60"],
        expect_fail=False,
    )
    unified = tmp_path / "combined_metrics_unified.csv"
    assert unified.exists()
    with open(unified) as f:
        header = f.readline().rstrip("\n").split(",")
    assert header[:2] == ["timestamp", "component"]
    assert header[2] == "id"


def test_n2_plus_emit_gauges_allowed(tmp_path):
    """Phase 5: ``--instances-per-component > 1`` +
    ``--emit-selection gauges`` is now permitted. The file-form gauge
    writer emits the 10-column long form with the dimension prefix
    instead of the 4-column ``timestamp,component,metric,value`` shape.
    """
    _invoke(
        ["--instances-per-component", "2",
         "--components", "apigateway",
         "--emit-selection", "metrics,gauges",
         "--output-dir", str(tmp_path), "--duration-days", "1",
         "--interval-seconds", "60"],
        expect_fail=False,
    )
    gauges = tmp_path / "gauges.csv"
    assert gauges.exists()
    with open(gauges) as f:
        header = f.readline().rstrip("\n").split(",")
    # 10-column long form when N>1.
    assert header == [
        "timestamp", "component", "id", "host", "pod", "az",
        "region", "tenant", "metric", "value",
    ]


def test_n2_plus_emit_schema_allowed(amc, tmp_path):
    """``--instances-per-component > 1`` + ``--emit-selection schema`` is allowed
    after Phase 8. ``write_schema_json`` declares a per-component
    ``dimensions`` block on every dim-aware component, and
    ``--validate-output`` (when also enabled) honors it via
    ``_validate_component_cells`` / ``_validate_component_row_count`` /
    ``_validate_long_form_dimensions``. Exercises ``parse_args`` directly
    to pin the gate lift, regardless of whether a downstream schema write
    actually runs.
    """
    args = amc.parse_args([
        "--instances-per-component", "2",
        "--emit-selection", "metrics,schema",
        "--output-dir", str(tmp_path), "--duration-days", "1",
    ])
    assert args.instances_per_component == 2
    assert "schema" in args.emit_selection


def test_n2_plus_validate_output_allowed(amc, tmp_path):
    """``--instances-per-component > 1`` + ``--validate-output`` is allowed
    after Phase 8. The validator reads the per-component
    ``dimensions`` block from ``schema.json`` and walks the long-form
    headers end-to-end, so the previous parse-time gate is no longer
    needed.
    """
    args = amc.parse_args([
        "--instances-per-component", "2",
        "--validate-output", str(tmp_path),
        "--duration-days", "1",
    ])
    assert args.instances_per_component == 2
    assert args.validate_output == tmp_path


def test_n2_plus_otel_enabled_allowed(amc, tmp_path):
    """``--instances-per-component > 1`` + ``--otel-enabled`` is allowed
    after Phase 6 wired the OTEL streamer's dimension attributes.

    The parse-time gate that used to reject this combination was lifted
    when ``stream_otel_signals`` / ``stream_otel_gauges`` began surfacing
    each ``_INSTANCE_DIMENSION_COLUMNS`` cell as a string attribute on
    every OTLP data point. Exercises ``parse_args`` directly so the
    test does not spin up an OTEL HTTP client against the dummy endpoint.
    """
    args = amc.parse_args([
        "--instances-per-component", "2",
        "--otel-enabled",
        "--otel-metrics-endpoint", "http://localhost:4318",
        "--output-dir", str(tmp_path), "--duration-days", "1",
    ])
    assert args.instances_per_component == 2
    assert args.otel_enabled is True


def test_n2_plus_otel_emit_gauges_allowed(amc, tmp_path):
    """``--instances-per-component > 1`` + ``--otel-emit-gauges`` is allowed
    after Phase 6. ``stream_otel_gauges`` reads the dimension
    columns off the per-component CSV and surfaces each non-empty
    ``_INSTANCE_DIMENSION_COLUMNS`` cell as a string attribute on every
    OTLP gauge data point.
    """
    args = amc.parse_args([
        "--instances-per-component", "2",
        "--otel-enabled",
        "--otel-emit-gauges",
        "--otel-metrics-endpoint", "http://localhost:4318",
        "--output-dir", str(tmp_path), "--duration-days", "1",
    ])
    assert args.instances_per_component == 2
    assert args.otel_enabled is True
    assert args.otel_emit_gauges is True


def test_n1_with_combine_gauges_schema_allowed(tmp_path):
    """The same set of flags is permitted with the default ``N == 1``.

    Counter-test for the gating: a user who wants combine / gauges /
    schema still gets the historic single-instance behavior at
    ``--instances-per-component 1`` (the default). This ensures the gate
    is targeted at the multi-instance case and does not regress the
    single-instance path the rest of the suite locks via golden hashes.
    """
    _invoke(
        ["--instances-per-component", "1",
         "--components", "apigateway",
         "--combine",
         "--emit-selection", "metrics,gauges,schema",
         "--output-dir", str(tmp_path), "--duration-days", "1"],
        expect_fail=False,
    )


# ---------------------------------------------------------------------------
# Defense-in-depth: generate_component() rejects DST + non-anonymous
# instances even when callers bypass parse_args (e.g. direct programmatic
# use). The parse_args gate above covers the CLI; this covers the helper.
# ---------------------------------------------------------------------------


def test_generate_component_raises_on_dst_plus_non_anonymous_instances(
    amc, tmp_path,
):
    """Direct callers cannot smuggle DST + multi-instance past the writer."""
    component = "apigateway"
    specs = amc.COMPONENTS[component][:1]
    interval = 1.0
    total_seconds = 60
    ts_array, ts_strings = amc._build_timestamp_arrays(total_seconds, interval)
    ctx = amc.RunContext(rng=amc.np.random.RandomState(42))
    instances = [
        amc.Instance(id="i0", pod="pod-0"),
        amc.Instance(id="i1", pod="pod-1"),
    ]
    with pytest.raises(ValueError) as exc:
        amc.generate_component(
            component, specs, [],
            base_dir=tmp_path,
            total_seconds=total_seconds,
            drop_rate=0.0,
            interval=interval,
            ts_array=ts_array,
            ts_strings=ts_strings,
            dst_inject_day=1,
            ctx=ctx,
            instances=instances,
        )
    msg = str(exc.value).lower()
    assert "dst_inject_day" in msg
    assert "instance" in msg


# ---------------------------------------------------------------------------
# --combine-only bypass: a user can generate per-component CSVs with N>1
# (rejected if combined in the same run by the parse_args gate above), then
# re-invoke --combine-only against the same directory. ``combine_logs``
# now inspects the header of each per-component CSV and refuses to combine
# any CSV that carries dimension columns (id/host/pod/az/region/tenant).
# ---------------------------------------------------------------------------


def test_combine_only_long_form_against_multi_instance_per_component_csv(
    amc, tmp_path,
):
    """Phase 5: ``--combine-only`` against an N>1 directory
    succeeds and writes the long-form unified CSV.

    Previously the parse-time + combine-time guards refused this
    combination (Phase 5 was deferred). Now the combine writer
    dispatches to ``_write_combined_long_form`` when header inspection
    detects dimension columns, so the two-pass generate-then-combine
    workflow against a staged multi-instance directory works.
    """
    # Phase 1: generate a multi-instance directory.
    _invoke(
        ["--instances-per-component", "2",
         "--components", "apigateway",
         "--output-dir", str(tmp_path), "--duration-days", "1",
         "--interval-seconds", "60"],
        expect_fail=False,
    )
    with (tmp_path / "apigateway.csv").open() as fh:
        header = fh.readline().rstrip("\n")
    assert header.startswith("timestamp,id,host,pod,az,region,tenant,")

    # Phase 2: --combine-only against the multi-instance dir succeeds
    # and emits the long-form unified CSV (instead of the historic wide
    # layout, which couldn't represent dimensions). The default
    # ``instances_per_component=1`` on the combine-only invocation is
    # fine — the per-component CSV's own header is the source of truth
    # for the layout dispatch.
    _invoke(
        ["--combine-only",
         "--components", "apigateway",
         "--output-dir", str(tmp_path)],
        expect_fail=False,
    )
    unified = tmp_path / "combined_metrics_unified.csv"
    assert unified.exists()
    with unified.open() as fh:
        unified_header = fh.readline().rstrip("\n").split(",")
    assert unified_header == [
        "timestamp", "component", "id", "host", "pod", "az",
        "region", "tenant", "metric", "value",
    ]


def test_combine_logs_accepts_single_instance_csv_with_overlapping_metric_name(
    amc, tmp_path,
):
    """A single-instance CSV whose metric name happens to be ``id`` /
    ``host`` / ... must combine cleanly under the wide layout. The
    Phase-5 header classifier requires the *full* canonical dimension
    prefix in column order, not any-overlap on the dim field set —
    otherwise a hypothetical future metric named one of the six dim
    columns would false-positive into the long-form branch in a single-
    instance CSV.
    """
    # Hand-craft a per-component CSV whose first metric column reuses a
    # dimension column name. This is the false-positive shape Copilot
    # originally flagged: a one-row overlap on the column set, but no
    # canonical six-column prefix in canonical order.
    csv_path = tmp_path / "synthetic_component.csv"
    csv_path.write_text(
        "timestamp,id,metric_b\n"
        "2025-01-01 00:00:00,42,3.14\n"
        "2025-01-01 00:00:01,43,2.71\n"
    )
    # combine_logs is the choke point both --combine and --combine-only
    # share, so calling it directly exercises the dispatcher without
    # spinning up a subprocess.
    amc.combine_logs(tmp_path, components=["synthetic_component"])
    combined = tmp_path / "combined_metrics_unified.csv"
    assert combined.exists()
    # Classifier must route this through the wide-layout branch (no
    # canonical six-column prefix in order), so the unified header is
    # the historic ``timestamp,<component>_<metric>...`` shape rather
    # than the long-form 10-column header.
    with combined.open() as fh:
        unified_header = fh.readline().rstrip("\n").split(",")
    assert unified_header[0] == "timestamp"
    assert "synthetic_component_id" in unified_header
    assert "synthetic_component_metric_b" in unified_header
    # ``component`` column would mean the long-form path fired.
    assert "component" not in unified_header

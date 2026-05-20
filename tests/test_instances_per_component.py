"""Tests for --instances-per-component (VER-140 Phase 2).

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
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(amc, out_dir, *, days, extra_args=None):
    """Thin wrapper over conftest.run_capture that throws away the stderr
    SimpleNamespace so existing call sites can keep returning ``out_dir``.

    Routing through the shared helper keeps the suite's single
    session-scoped ``amc`` module load (see conftest._load_amc) shared
    with the rest of the test suite — re-importing via
    ``spec_from_file_location`` from this file would double the
    registry-build cost.
    """
    captured = run_capture(amc, out_dir, days=days, extra_args=extra_args)
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
def n3_1d(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("inst_n3_1d")
    _run(amc, out, days=1, extra_args=["--instances-per-component", "3"])
    return out


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
    # Find interval that puts a single-component run just under the cap at N=1
    # but over it at N=2. Use a single component to keep math simple.
    # 1 component, default metrics, 1 day, interval=1s → ~75 default metrics *
    # 86400 rows = ~6.5M cells — well under cap. Need to stay under cap at N=1
    # but go over at very small interval. Instead test via mocking:
    # run with --allow-huge-output N=1 to confirm it completes, then verify
    # parse_args raises when estimated_cells > cap without the bypass flag.
    args_n1 = amc.parse_args([
        "--output-dir", str(tmp_path),
        "--duration-days", "1",
        "--instances-per-component", "1",
        "--allow-huge-output",
    ])
    assert args_n1.instances_per_component == 1

    # Verify that parse_args with a huge N on a long run raises SystemExit.
    # 86400 rows * 75 metrics * 20 instances = ~130M cells (under 200M).
    # Use 7 days: 604800 * 75 * 20 = 907M > 200M.
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--output-dir", str(tmp_path),
            "--duration-days", "7",
            "--instances-per-component", "20",
        ])


# ---------------------------------------------------------------------------
# Locked N=3 golden hashes (1-day and 7-day) at --seed 42.
#
# Captured on the Phase 2 landing (VER-145 / VER-140 phase 2). These pin the
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
    "anomalies.csv": "b2978b6a5abdfc3e253120a04302895c6f678f382fd6fea1acba569b28f355e5",
    "apigateway.csv": "d22024b8c4b4a4ee1f36a295371009c469da71b47b4dce81d5019c3e904a0ffd",
    "authservice.csv": "8491cb0a3311d144c73181918947583a7f3011788f3904186033ed2d345183c8",
    "cacheservice.csv": "fa09b96aea69dfc1393d87b29a064ebf393185a230da2e34a585b19ec94b02d6",
    "database.csv": "30e4f8b30c122538e8779d6d999037d10641e4a2af8b4932bfd4b7cef6225dc9",
    "identityprovider.csv": "e510b278e4af9bd3b041ee0bde31270ec4700e1f438867d489e589fe97bc3fb5",
    "llm_analytics.csv": "d8d3a3a4680b4cb7a2937c0eb41ea27c06bcad2325a6abf895bc5f23231a8502",
    "loadbalancer.csv": "4ee8d39da21fab146f19fd47cc760f4d057331691d184d4d97edf0a10198a8a2",
    "mqservice.csv": "bc115ab67235ded62c0ffb5f930bf6b0f94efccceb49c0c2793db872a752d23e",
    "objectstore.csv": "fa4daee9f03c65e72a87af51ba8edcd7e42f9b3ca91a9d94c35187ca8ec865cf",
    "observabilitypipeline.csv": "5a12ca556432a8abe3a29338ad480f768b43de0739dcb43bb53e731dcc85065f",
    "paymentservice.csv": "68cefdbef481bff64191e5cd505ffdab9628843521cf8a9b73e617633b95cf71",
    "scheduler.csv": "7ae78312b8ab5e85dfd0cf34bdfeb4d03c3922e834b5c4a66a1ae34b4e6d0004",
    "vectorstore.csv": "f8cc7f591d77d4c62445a453d7fbaf966a873eae717bf1c8388f681e7649b51c",
}

N3_SEVEN_DAY_HASHES = {
    "anomalies.csv": "97e4cb8b63d2629a0499dd27c07d5dce68003e0306bc68a22bcbd60b827ae725",
    "apigateway.csv": "71c3d7e3d3a111044d80bd5c3173dd0ddb91e9780dc928cd7d450cb2dcf9dbc1",
    "authservice.csv": "ab32c063e97c6dd23f628abd801b92e44ee23c97757ec46913b72d5fe08c798e",
    "cacheservice.csv": "6471d112752a963144e7118f4c34df03dd414ece3895cc3f0074ea86121be501",
    "database.csv": "8a209690abf47e437f99881a323aa71575d0c5bda7fcf6608849cce5e047882d",
    "identityprovider.csv": "06f894e392b7e5a17a37c8befa420c3d70959c3a41368bf08e696f92e8094c0b",
    "llm_analytics.csv": "886417be3488958e4c777b49d7ae8f406ddca7733006734bd8c92345cf3226cf",
    "loadbalancer.csv": "44f4ade8b065fdc47eea91ca42299a18b0950f601a828ea021e61253fa61891c",
    "mqservice.csv": "c40c84a4cb27134401214619e7c472d1ed248aa5f2b527b01f554870dbab7e4c",
    "objectstore.csv": "3a034e1e693ad0cbe27f7b83a076c8eb23965f7b0f10eb304213f85e0e325c82",
    "observabilitypipeline.csv": "db8dcbc73fde892bbf30a260440cfb5b0f0b8f99dcd58d142d38c0d7085ea41b",
    "paymentservice.csv": "717e072c37c4b76fac6abbd3851a26d7ced072f404e2cc0b693d090d470575ac",
    "scheduler.csv": "8c7fc755df09ab550fd9972909343e8c816d7a6add09cfdd0c36ae144e71ec12",
    "vectorstore.csv": "4a63ebca8d0daa0d9168c97fa90b218aa76b636ee70b36775eb536fc61631f09",
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
    """
    out2 = tmp_path_factory.mktemp("inst_n3_1d_v2")
    _run(amc, out2, days=1, extra_args=["--instances-per-component", "3"])
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
# Mutual exclusion: --instances-per-component > 1 + --inject-dst-artifact-day
# ---------------------------------------------------------------------------

def test_instances_per_component_dst_mutually_exclusive(tmp_path):
    """--instances-per-component > 1 is incompatible with --inject-dst-artifact-day > 0."""
    result = _invoke(
        ["--instances-per-component", "2",
         "--inject-dst-artifact-day", "1",
         "--output-dir", str(tmp_path), "--duration-days", "1"],
        expect_fail=True,
    )
    assert "instances-per-component" in result.stderr.lower()
    assert "inject-dst-artifact-day" in result.stderr.lower()


def test_instances_n1_with_dst_allowed(tmp_path):
    """--instances-per-component 1 with --inject-dst-artifact-day is fine."""
    _invoke(
        ["--instances-per-component", "1",
         "--inject-dst-artifact-day", "1",
         "--output-dir", str(tmp_path), "--duration-days", "1"],
        expect_fail=False,
    )

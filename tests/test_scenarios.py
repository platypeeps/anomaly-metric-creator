"""Phase 1 SCENARIOS registry coverage.

This phase migrates the 3 multi-day cascading scenarios (cache_leak_restart,
jwks_rotation_chaos, db_disk_exhaustion) out of the legacy ``anoms_*`` lists
and ``register_default_cascades()`` body into a registry-driven walk in
``main()``. The legacy imperative path remains in place for every other
anomaly until VER-104 completes the migration. These tests cover:

* Registry structural validation — slug uniqueness, severity vocabulary,
  ``days_required`` vocabulary, component coverage.
* CLI flag parsing for ``--scenarios`` and ``--exclude-scenarios`` (the
  smaller surface here; ``tests/test_args.py`` carries the case-insensitive
  and whitespace-tolerant variants).
* End-to-end smoke flags — allowlist, exclusion, out-of-duration warn-and-skip,
  unknown-slug hard error.
* Default-output byte-for-byte regression — locked SHA-256 hashes for every
  per-component CSV and ``anomalies.csv`` from a default 1-day run at seed
  42 and a 7-day run at seed 42, captured immediately before this refactor.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from conftest import read_manifest, run_capture


THREE_MULTI_DAY_SCENARIOS = {"cache_leak_restart", "jwks_rotation_chaos", "db_disk_exhaustion"}

# Per-component descriptions for each multi-day scenario primary spec.
# Mirrors tests/test_multiday_cascades.py but indexed by scenario slug.
SCENARIO_PRIMARIES_BY_SLUG = {
    "cache_leak_restart": {
        "Cache memory leak — slow growth 50%→95% over 51h",
        "Cache eviction cascade — hit ratio decline 88%→60% over 12h",
        "Cache forced restart — memory reset to 55%",
        "Cache cold start after restart — hit ratio 5%",
        "Cache warm-up errors during restart",
    },
    "jwks_rotation_chaos": {
        "TLS cert validation flapping at POPs — errors ramp 2→25/s",
        "JWKS fetch latency sustained at 800 ms — pre-rotation slowdown",
        "Login success rate decline 98%→85% as cert chain degrades",
        "Hard cert expiration — TLS errors spike to 200/s",
        "Cert expiry — OIDC flow failures spike to 800",
        "Emergency key rotation — 50 events during expiry window",
    },
    "db_disk_exhaustion": {
        "Database disk slow exhaustion 65%→92% over 96h",
        "Database write latency drift 12→90 ms as I/O saturates",
        "Emergency log truncation — write errors spike to 12%",
        "Database log truncation — disk drops to 78%",
        "Database write latency partial relief — 30 ms post-truncation",
    },
}

SCENARIO_CASCADES_BY_SLUG = {
    "cache_leak_restart": {
        "Cascading: Rising cache miss volume — DB queries climb to ~32k",
        "Cascading: Cache hit-ratio decline — DB queries climb to ~42k",
        "Cascading: Cache hit-ratio decline pushes DB read latency to ~55 ms",
        "Cascading: Cache cold-start stampede — DB queries ~60k",
        "Cascading: Cache restart causes brief gateway errors (~8%)",
        "Cascading: Cache restart backs up MQ — ~180,000 pending",
    },
    "jwks_rotation_chaos": {
        "Cascading: Sporadic TLS failures propagate to gateway (~5%)",
        "Cascading: Slow JWKS fetch raises auth latency to ~350 ms",
        "Cascading: Broken auth chain — payment 5xx ~8%",
        "Cascading: Mass TLS failure floods gateway (~28%)",
        "Cascading: Unverifiable tokens drive declines to ~45%",
        "Cascading: Mass session re-auth — cache misses ~3,500",
    },
    "db_disk_exhaustion": {
        "Cascading: Slow disk fails background-job writes (~8/min)",
        "Cascading: DB write latency drift lags observability ingest to ~180s",
        "Cascading: Consumers blocked on DB writes — ~320k pending",
        "Cascading: DB truncation event raises backend latency to ~720 ms",
        "Cascading: DB error spike propagates to gateway (~15%)",
    },
}


# ------------------------------------------------------------------
# SHA-256 hashes of per-component CSVs from the immediately-pre-refactor
# main branch (seed 42, default flags). Locking these protects every
# component output from accidental RNG-order or spec-order drift during
# the scenario-registry migration.
# ------------------------------------------------------------------
DEFAULT_ONE_DAY_HASHES = {
    "anomalies.csv": "f68a07e3597b63f6aa69ea83d4a65fb4cbd70522ecb68d67a181d463519c40c5",
    "apigateway.csv": "791955b9345479ff26a2045978df7edaefb4a30f03276d1c1937dfa9b5ba01ef",
    "authservice.csv": "7ba9f17c30c579fd81a5a929b6cf23a075493e34e4bf301fcf78e75d3fbbd195",
    "cacheservice.csv": "b62c4e58578aff922ca9e65684d246c1be54166ec961d23699dd9cef6148daf8",
    "database.csv": "7a605a7c7b838b4ff1bcfaed674cc9f8bbd0bc3f2caf0789df277ec6b9c92dc5",
    "identityprovider.csv": "c884970f063d58a8cd2289be8500b810a022727c407601c503d841844cdf1577",
    "llm_analytics.csv": "84dbc8c47045a870d01b567f7794e3281f7a0290fb78b2bfc7e3d4ef3beccb6b",
    "loadbalancer.csv": "a1de03bfba5aabbeaf86c2346e603218fd23e38bfa3cb31f51453e15077656b1",
    "mqservice.csv": "9eba5bbebbba3fd66b84eb2117a42cd2dd342c40e284657fb9df385767605ea7",
    "objectstore.csv": "fc4ea917e6591cd6839eb315775bf20371bd4569c53df05a7dd7f9323c2e899d",
    "observabilitypipeline.csv": "e26bac024a6b192519792e056d5e7a60378d438df5c635a4c168420823b56f63",
    "paymentservice.csv": "fd768a451f4dd9e35436659eff6bb6f121252395b0302eea44cff21600cedec9",
    "scheduler.csv": "09f2fd6953dcf4ca9e47332f332e8fa206c4d392637eccff0e4f5840fd7a9aa7",
    "vectorstore.csv": "45f40482e8fffbe0d0e0bd6b871cdbb984ccf1e3d79e65600e8da2e34853fa88",
}

DEFAULT_SEVEN_DAY_HASHES = {
    "anomalies.csv": "8fdfdc418e298ba3f27cfe4f20fd41d587e0c49e951f33bd2e1963e3d60c01f1",
    "apigateway.csv": "c9d2d0040154d6fd18a30b87e037798a79f219f5a53f5f5d21884408029d5bdc",
    "authservice.csv": "3f3f41f55f4e31bf71c79d8af971ae5d3f003646ab280c15c2d849b091d1f26e",
    "cacheservice.csv": "04f32aa9462c7e8fd52f5b50b4857570df90f9196f7513496209537728b5e704",
    "database.csv": "75ac2f92d8de573ac0a164153c7d9cb73aea30abf918fcb22ecf3ccc794f378a",
    "identityprovider.csv": "f4ba4d1a34b45c2e155913af030fb1b44b7001e2a4145f4fb34b5d17f38bc5ba",
    "llm_analytics.csv": "a3161f50f7bf862e57da090585a2969ac97d57624f87c021cff53fc1b4f6f698",
    "loadbalancer.csv": "28429668c0880a6b2cac9299e2eb5eabe4594efbe1eaecb5107c0e3c032c5f9a",
    "mqservice.csv": "30d45a1f410204696a9c1c7fac4d73ace1edc6661e9ab257fd824f9c2c467fec",
    "objectstore.csv": "f7959a62b01ca59e98ae84edc7f77d1ef97bd47cfae929ef3c569c50acb52c57",
    "observabilitypipeline.csv": "60e5b94ce8fea80de4115986d079046c191d15731579e8b8ac131b9247dab020",
    "paymentservice.csv": "bd477a89fcc4279799b479db685cef4efedf88db588d385eaafcab4717bdecbf",
    "scheduler.csv": "de482da5f5552b463b666d2e1e124c853125e9fc18af8167e65f812bd7c73cd1",
    "vectorstore.csv": "00bda8d310a34db9e08c3dd5e26c01378f58f9a2669ee776ac01e0c985d4d5ea",
}

# SHA-256 hashes captured from the pre-VER-103 main branch for
# ``--signal-level high --duration-days 7 --anomaly-count 100`` at seed 42.
# Locking these protects the deterministic --anomaly-count sampling pool
# from drift in the positional order of legacy / scenario / high-pressure
# specs: _apply_signal_level_and_count() seeds an SeedSequence with
# ``spawn_key=(_ANOMALY_COUNT_CAP_SALT,)`` and picks ``anomaly_count``
# positions out of the in-range pool, so any reshuffle changes which
# anomalies land in the manifest.
HIGH_SEVEN_DAY_CAPPED_HASHES = {
    "anomalies.csv": "cc2b39f13df6c3b44d700f1c4856dc98ed7af7654ba8a6469ecc807e84d5399f",
    "apigateway.csv": "019af0c94f2c803f51c8f948b09ef9ed89c4faa2a1335a7da22aa5d7e4775a54",
    "authservice.csv": "8a934bd6b9069948d0fed195056f64e42ad9bf784db05a9ab2251eb0cf6a352d",
    "cacheservice.csv": "693c886a53d61f5038b88884005c46865f89faae10452a9ce3e469db2ee5a2d9",
    "database.csv": "3ae83b0e75fcd27d64c87d3ce53cc997abef0b42dfc4c7128fca7fdfef7194ef",
    "identityprovider.csv": "523e8929bc18e09559c7fa6a06def508124d33a9cfd68248e2de3bc7dbb156a6",
    "llm_analytics.csv": "3adcf752bfc6f67d233750b1902a0cec498f5a37837e1bacc9a1d13338f65a42",
    "loadbalancer.csv": "a4b3434be2c8407a96c04d2bcf90c79708797f08a04391519aa377d030d155f4",
    "mqservice.csv": "ab74c73a43902cbe050f12b3d6fe7af97c811f28457107330a9b60c4f13b518d",
    "objectstore.csv": "42598e0db3d27aecc85bf74c04412bfd3c21b4aedaf4be2c88a229432645bcae",
    "observabilitypipeline.csv": "5211c83ad338ac49866b2f7d366d227485ffd6d716fa1504648760286e10e9f3",
    "paymentservice.csv": "3178647b2d2ea7f8a28cd7c0371bfed3847e0d01c44540a2f2c49b40306f2758",
    "scheduler.csv": "9dd9f850a6733c544c9094c225ce7daa7c0cc952be2059ef00aee8d1bc7ecc43",
    "vectorstore.csv": "b5e2e05135491d3f5e4f63ba4ece370fbad056a4f0ffa4ffeb517a38fe20c3be",
}


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------
# Registry structural validation
# ------------------------------------------------------------------
def test_scenarios_registry_exists(amc):
    assert hasattr(amc, "SCENARIOS"), "amc must expose a SCENARIOS registry"
    assert isinstance(amc.SCENARIOS, dict)


def test_scenarios_registry_contains_three_multi_day(amc):
    missing = THREE_MULTI_DAY_SCENARIOS - set(amc.SCENARIOS.keys())
    assert not missing, f"SCENARIOS must contain the 3 multi-day cascading slugs; missing: {missing}"


def test_scenario_slugs_are_unique(amc):
    slugs = list(amc.SCENARIOS.keys())
    assert len(slugs) == len(set(slugs)), "Scenario slugs must be unique"


def test_scenario_id_matches_dict_key(amc):
    for slug, scenario in amc.SCENARIOS.items():
        assert scenario.id == slug, (
            f"SCENARIOS[{slug!r}].id is {scenario.id!r}; "
            f"id must equal the registry key"
        )


@pytest.mark.parametrize("slug", sorted(THREE_MULTI_DAY_SCENARIOS))
def test_scenario_severity_in_vocabulary(amc, slug):
    severity = amc.SCENARIOS[slug].severity
    assert severity in {"low", "medium", "high"}, (
        f"SCENARIOS[{slug!r}].severity {severity!r} not in {{low, medium, high}}"
    )


@pytest.mark.parametrize("slug", sorted(THREE_MULTI_DAY_SCENARIOS))
def test_scenario_days_required_vocabulary(amc, slug):
    days_required = amc.SCENARIOS[slug].days_required
    assert days_required in {1, 7}, (
        f"SCENARIOS[{slug!r}].days_required {days_required!r} not in {{1, 7}}"
    )


@pytest.mark.parametrize("slug", sorted(THREE_MULTI_DAY_SCENARIOS))
def test_scenario_components_touched_exist(amc, slug):
    components = amc.SCENARIOS[slug].components_touched
    assert components, f"SCENARIOS[{slug!r}].components_touched must be non-empty"
    unknown = set(components) - set(amc.COMPONENTS.keys())
    assert not unknown, (
        f"SCENARIOS[{slug!r}].components_touched contains unknown component(s): {unknown}"
    )


def test_three_multi_day_scenarios_require_seven_days(amc):
    """The 3 migrated multi-day cascading scenarios must declare
    ``days_required >= 7`` so a default 1-day run drops them with a stderr
    warning (matches the acceptance criterion for VER-103)."""
    for slug in THREE_MULTI_DAY_SCENARIOS:
        scenario = amc.SCENARIOS[slug]
        assert scenario.days_required == 7, (
            f"SCENARIOS[{slug!r}].days_required must be 7 (this is a multi-day scenario)"
        )


# ------------------------------------------------------------------
# CLI flag parsing — case-insensitive variants live in test_args.py
# ------------------------------------------------------------------
def test_parse_args_scenarios_default_is_all(amc):
    args = amc.parse_args(["--output-dir", "test_out"])
    assert args.scenarios == set(amc.SCENARIOS.keys())


def test_parse_args_exclude_scenarios_default_empty(amc):
    args = amc.parse_args(["--output-dir", "test_out"])
    assert args.exclude_scenarios == set()


def test_parse_args_scenarios_unknown_slug_fails(amc):
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--scenarios", "cache_leak_restart,not_a_scenario",
            "--output-dir", "test_out",
        ])


def test_parse_args_exclude_scenarios_unknown_slug_fails(amc):
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--exclude-scenarios", "not_a_scenario",
            "--output-dir", "test_out",
        ])


# ------------------------------------------------------------------
# End-to-end smoke flags
# ------------------------------------------------------------------
def test_scenarios_cache_leak_restart_isolates_to_a_only(amc, tmp_path):
    """``--scenarios cache_leak_restart --duration-days 7`` includes only
    Scenario A primaries/cascades (in addition to all legacy specs that
    aren't scenario-tagged)."""
    result = run_capture(
        amc, tmp_path, days=7,
        extra_args=["--scenarios", "cache_leak_restart"],
    )
    descriptions = {row["description"] for row in read_manifest(result.out_dir)}

    missing_a = SCENARIO_PRIMARIES_BY_SLUG["cache_leak_restart"] - descriptions
    assert not missing_a, (
        f"Scenario A primaries missing under --scenarios cache_leak_restart: {missing_a}"
    )
    missing_a_cas = SCENARIO_CASCADES_BY_SLUG["cache_leak_restart"] - descriptions
    assert not missing_a_cas, (
        f"Scenario A cascades missing under --scenarios cache_leak_restart: {missing_a_cas}"
    )

    leaked_b = SCENARIO_PRIMARIES_BY_SLUG["jwks_rotation_chaos"] & descriptions
    leaked_c = SCENARIO_PRIMARIES_BY_SLUG["db_disk_exhaustion"] & descriptions
    assert not leaked_b, f"Scenario B primaries leaked when filtered to A-only: {leaked_b}"
    assert not leaked_c, f"Scenario C primaries leaked when filtered to A-only: {leaked_c}"

    leaked_b_cas = SCENARIO_CASCADES_BY_SLUG["jwks_rotation_chaos"] & descriptions
    leaked_c_cas = SCENARIO_CASCADES_BY_SLUG["db_disk_exhaustion"] & descriptions
    assert not leaked_b_cas, (
        f"Scenario B cascades leaked when filtered to A-only: {leaked_b_cas}"
    )
    assert not leaked_c_cas, (
        f"Scenario C cascades leaked when filtered to A-only: {leaked_c_cas}"
    )


def test_scenarios_db_disk_exhaustion_one_day_warns_and_drops(amc, tmp_path):
    """``--scenarios db_disk_exhaustion --duration-days 1`` emits a stderr
    WARNING naming the scenario and produces zero anomalies from it."""
    result = run_capture(
        amc, tmp_path, days=1,
        extra_args=["--scenarios", "db_disk_exhaustion"],
    )
    descriptions = {row["description"] for row in read_manifest(result.out_dir)}
    leaked = SCENARIO_PRIMARIES_BY_SLUG["db_disk_exhaustion"] & descriptions
    assert not leaked, (
        f"db_disk_exhaustion primaries leaked at duration_days=1: {leaked}"
    )
    leaked_cas = SCENARIO_CASCADES_BY_SLUG["db_disk_exhaustion"] & descriptions
    assert not leaked_cas, (
        f"db_disk_exhaustion cascades leaked at duration_days=1: {leaked_cas}"
    )
    assert "db_disk_exhaustion" in result.stderr, (
        f"Expected stderr WARNING naming db_disk_exhaustion; stderr was: {result.stderr!r}"
    )


def test_scenarios_unknown_slug_exits_nonzero(amc, tmp_path):
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--scenarios", "unknown_slug",
            "--output-dir", str(tmp_path),
        ])


def test_resolve_scenarios_warning_order_is_deterministic(amc, tmp_path):
    """When multiple scenarios are dropped by ``--duration-days`` /
    ``--signal-level``, the stderr ``WARNING`` lines appear in sorted-slug
    order so diagnostics are reproducible across runs. Set iteration
    order would otherwise vary by interpreter hash randomization."""
    result = run_capture(
        amc, tmp_path, days=1,
        extra_args=["--scenarios", "all"],
    )
    warning_slugs = [
        line.split()[2]
        for line in result.stderr.splitlines()
        if line.startswith("WARNING: scenario ")
    ]
    expected = sorted(THREE_MULTI_DAY_SCENARIOS)
    assert warning_slugs == expected, (
        "Expected scenario WARNING lines in sorted-slug order "
        f"{expected}; got {warning_slugs}"
    )


def test_exclude_scenarios_jwks_drops_only_b(amc, tmp_path):
    """``--exclude-scenarios jwks_rotation_chaos --duration-days 7`` keeps
    Scenario A + C primaries and drops Scenario B."""
    result = run_capture(
        amc, tmp_path, days=7,
        extra_args=["--exclude-scenarios", "jwks_rotation_chaos"],
    )
    descriptions = {row["description"] for row in read_manifest(result.out_dir)}

    leaked_b = SCENARIO_PRIMARIES_BY_SLUG["jwks_rotation_chaos"] & descriptions
    assert not leaked_b, (
        f"Scenario B primaries leaked under --exclude-scenarios jwks_rotation_chaos: {leaked_b}"
    )
    leaked_b_cas = SCENARIO_CASCADES_BY_SLUG["jwks_rotation_chaos"] & descriptions
    assert not leaked_b_cas, (
        f"Scenario B cascades leaked under --exclude-scenarios jwks_rotation_chaos: {leaked_b_cas}"
    )

    missing_a = SCENARIO_PRIMARIES_BY_SLUG["cache_leak_restart"] - descriptions
    missing_c = SCENARIO_PRIMARIES_BY_SLUG["db_disk_exhaustion"] - descriptions
    assert not missing_a, (
        f"Scenario A primaries missing when only B is excluded: {missing_a}"
    )
    assert not missing_c, (
        f"Scenario C primaries missing when only B is excluded: {missing_c}"
    )
    missing_a_cas = SCENARIO_CASCADES_BY_SLUG["cache_leak_restart"] - descriptions
    missing_c_cas = SCENARIO_CASCADES_BY_SLUG["db_disk_exhaustion"] - descriptions
    assert not missing_a_cas, (
        f"Scenario A cascades missing when only B is excluded: {missing_a_cas}"
    )
    assert not missing_c_cas, (
        f"Scenario C cascades missing when only B is excluded: {missing_c_cas}"
    )


# ------------------------------------------------------------------
# Byte-for-byte default-output regression
# ------------------------------------------------------------------
@pytest.mark.parametrize("filename, expected_hash", sorted(DEFAULT_ONE_DAY_HASHES.items()))
def test_default_one_day_csvs_byte_identical(one_day_run_a, filename, expected_hash):
    path = one_day_run_a.out_dir / filename
    assert path.exists(), f"{filename} missing from default 1-day run"
    actual = _sha256_path(path)
    assert actual == expected_hash, (
        f"{filename} drifted from pre-refactor hash. "
        f"expected={expected_hash} actual={actual}"
    )


@pytest.mark.parametrize("filename, expected_hash", sorted(DEFAULT_SEVEN_DAY_HASHES.items()))
def test_default_seven_day_csvs_byte_identical(seven_day_run, filename, expected_hash):
    path = seven_day_run.out_dir / filename
    assert path.exists(), f"{filename} missing from default 7-day run"
    actual = _sha256_path(path)
    assert actual == expected_hash, (
        f"{filename} drifted from pre-refactor hash. "
        f"expected={expected_hash} actual={actual}"
    )


# ------------------------------------------------------------------
# Byte-for-byte high-pressure + anomaly-cap regression
# ------------------------------------------------------------------
# This guards against the ordering bug where applying scenarios *after*
# the high-pressure extensions reshuffles the deterministic sampling pool
# used by --anomaly-count, even though the default (medium-signal) hashes
# stay stable.
@pytest.fixture(scope="session")
def high_seven_day_capped_run(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("high_seven_day_capped")
    return run_capture(
        amc, out, days=7,
        extra_args=["--signal-level", "high", "--anomaly-count", "100"],
    )


@pytest.mark.parametrize(
    "filename, expected_hash", sorted(HIGH_SEVEN_DAY_CAPPED_HASHES.items())
)
def test_high_seven_day_capped_csvs_byte_identical(
    high_seven_day_capped_run, filename, expected_hash
):
    path = high_seven_day_capped_run.out_dir / filename
    assert path.exists(), (
        f"{filename} missing from --signal-level high --duration-days 7 "
        f"--anomaly-count 100 run"
    )
    actual = _sha256_path(path)
    assert actual == expected_hash, (
        f"{filename} drifted from pre-refactor hash under "
        f"--signal-level high --anomaly-count 100. The scenario / "
        f"high-pressure spec ordering inside _apply_signal_level_and_count's "
        f"sampling pool has shifted. "
        f"expected={expected_hash} actual={actual}"
    )

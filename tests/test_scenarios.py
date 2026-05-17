"""SCENARIOS registry coverage (VER-103 phase 1 + VER-104 phase 2 full migration).

All anomaly and cascade specs live in the SCENARIOS registry; the legacy
``anoms_*`` lists and ``register_default_cascades()`` /
``register_high_pressure_cascades()`` functions have been deleted entirely.
These tests cover:

* Registry structural validation — slug uniqueness, severity vocabulary,
  ``days_required`` vocabulary, component coverage.
* CLI flag parsing for ``--scenarios`` and ``--exclude-scenarios`` (the
  smaller surface here; ``tests/test_args.py`` carries the case-insensitive
  and whitespace-tolerant variants).
* End-to-end smoke flags — allowlist, exclusion, out-of-duration warn-and-skip,
  unknown-slug hard error.
* Default-output byte-for-byte regression — locked SHA-256 hashes for every
  per-component CSV and ``anomalies.csv`` from a default 1-day run at seed
  42 and a 7-day run at seed 42 (the VER-104 baseline; the high-signal +
  ``--anomaly-count`` capped 7-day hashes below were captured after the
  full migration and lock the post-VER-104 sampling pool).
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
        "Cache eviction cascade — misses ramp 682→3,333 (hit ratio 88%→60%) over 12h",
        "Cache forced restart — memory reset to 55%",
        "Cache cold start after restart — misses ~95,000 (hit ratio ~5%)",
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
#
# VER-132 (2026-05-17) re-locked the anomalies.csv hashes after adding the
# 8 enriched columns (scenario_id, severity, is_cascade, event_id,
# parent_event_id, span_start, span_end, shape) and chronological sort.
# Per-component CSV hashes were NOT changed by VER-132 — the scenario
# provenance is stamped into shallow-copied spec dicts so RNG draw order
# and CSV bytes remain byte-identical to the pre-VER-132 main.
# ------------------------------------------------------------------
DEFAULT_ONE_DAY_HASHES = {
    "anomalies.csv": "458703b3da32183889d7a2ca68840f1da05fd00a78cc95aff4e530c2cd5cbb06",
    "apigateway.csv": "23d0e6e3c0ebe47976480a656f393e2c623ea233532679c741c35a8fc5927c22",
    "authservice.csv": "06ab97884f65eb53db6eff0c61147f576809517b841e21b98b2861cb99dd5617",
    "cacheservice.csv": "7ace2f8b8dd6c6ed43ed90058eae8a0f1b1f077a37bbedd88ea9f7523246dde3",
    "database.csv": "d9f6249464da8fef4e9456df653923b8a4eceac0ad9c403d2c66783106c1a750",
    "identityprovider.csv": "c884970f063d58a8cd2289be8500b810a022727c407601c503d841844cdf1577",
    "llm_analytics.csv": "84dbc8c47045a870d01b567f7794e3281f7a0290fb78b2bfc7e3d4ef3beccb6b",
    "loadbalancer.csv": "a1de03bfba5aabbeaf86c2346e603218fd23e38bfa3cb31f51453e15077656b1",
    "mqservice.csv": "2aab1b3bc389c4c5b80e13347c1da37f8848c07bed927e8bad80ba3fdd686d07",
    "objectstore.csv": "fc4ea917e6591cd6839eb315775bf20371bd4569c53df05a7dd7f9323c2e899d",
    "observabilitypipeline.csv": "e26bac024a6b192519792e056d5e7a60378d438df5c635a4c168420823b56f63",
    "paymentservice.csv": "fd768a451f4dd9e35436659eff6bb6f121252395b0302eea44cff21600cedec9",
    "scheduler.csv": "09f2fd6953dcf4ca9e47332f332e8fa206c4d392637eccff0e4f5840fd7a9aa7",
    "vectorstore.csv": "45f40482e8fffbe0d0e0bd6b871cdbb984ccf1e3d79e65600e8da2e34853fa88",
}

DEFAULT_SEVEN_DAY_HASHES = {
    "anomalies.csv": "5ab3f0d8d5397b8715c691e82e06e462c6db76b37b985c83d1386a9b63dc2ec0",
    "apigateway.csv": "bc7d1a450ed06b4bd4555b467abbea31f363ec4758533a44cf2cb77758d096ad",
    "authservice.csv": "a5aab875ee8f14aa2070b7647885bbca274305ab7cc69d80c5136e755a0eaabb",
    "cacheservice.csv": "3524a441d5b9e2388d4f62799cc5da1aabcfa912c08bf1192ed84bda6a86d0a6",
    "database.csv": "8815d53fcb1abbea704c3af519635743d12e1b05dfda47224a343bb52c01c9c2",
    "identityprovider.csv": "f4ba4d1a34b45c2e155913af030fb1b44b7001e2a4145f4fb34b5d17f38bc5ba",
    "llm_analytics.csv": "a3161f50f7bf862e57da090585a2969ac97d57624f87c021cff53fc1b4f6f698",
    "loadbalancer.csv": "28429668c0880a6b2cac9299e2eb5eabe4594efbe1eaecb5107c0e3c032c5f9a",
    "mqservice.csv": "33aa01c12460f405e38cc50c33fcbbd0d561015fd2cd59cd2e2d19b44308ec9c",
    "objectstore.csv": "f7959a62b01ca59e98ae84edc7f77d1ef97bd47cfae929ef3c569c50acb52c57",
    "observabilitypipeline.csv": "60e5b94ce8fea80de4115986d079046c191d15731579e8b8ac131b9247dab020",
    "paymentservice.csv": "bd477a89fcc4279799b479db685cef4efedf88db588d385eaafcab4717bdecbf",
    "scheduler.csv": "de482da5f5552b463b666d2e1e124c853125e9fc18af8167e65f812bd7c73cd1",
    "vectorstore.csv": "00bda8d310a34db9e08c3dd5e26c01378f58f9a2669ee776ac01e0c985d4d5ea",
}

# SHA-256 hashes for ``--signal-level high --duration-days 7 --anomaly-count 100``
# at seed 42, captured against the post-VER-104 registry-only spec ordering
# (commit f6bd453). Locking these protects the deterministic --anomaly-count
# sampling pool from drift in the positional order of registry specs:
# _apply_signal_level_and_count() seeds a SeedSequence with
# ``spawn_key=(_ANOMALY_COUNT_CAP_SALT,)`` and picks ``anomaly_count`` positions
# out of the in-range pool, so any reshuffle of SCENARIOS insertion order or
# of per-component append ordering changes which anomalies land in the manifest.
HIGH_SEVEN_DAY_CAPPED_HASHES = {
    "anomalies.csv": "424438f430832ccfe843e1fb1b603cb607e938f7ccfa53ebe89dbdb265a22acd",
    "apigateway.csv": "749ed6244fcade5e2719b767e49d1a50150afe67b1e31bcb38d5381a8fdfc06c",
    "authservice.csv": "19fc066c4304e0712504819f9f2beda96081eb58ead79c4c2501a845ef12fcb0",
    "cacheservice.csv": "8cacedb1b97abdedbd4b06c0094ea81f6b248c2e7521a1daade81d0078322067",
    "database.csv": "3c40f26dfa68dad837c7b86facc44d6d50c1037152ea35c20d6e68e4e21c3815",
    "identityprovider.csv": "7f9549edc1f597f2b25b2e20b1b14625ede70f22859ee5cc5582bc17859044e4",
    "llm_analytics.csv": "56e975cc0e08eb315ed772c0d338ce0203b4ae88d32e20644dd5fe10c7dcb79c",
    "loadbalancer.csv": "aa593986a8c026e828b426ef3896341b3d0a6dd7b4cb7327e363419d2cd438f1",
    "mqservice.csv": "064107f4214bdb8715de9ffee6658873ae408747faf98762eacaf9fe536538b1",
    "objectstore.csv": "176d8a59c6e302d0ccdb4438c3c2e90c19afab7234ed163e5dc1b988c8df7f04",
    "observabilitypipeline.csv": "d5f94960af80e52366a1ca26725cc38f8933b59c558ddf13faa0101c9dce9cc4",
    "paymentservice.csv": "ab847d6ca94990dc254bdecd2430cec826192e85248dfd190f6d41f2023e49c3",
    "scheduler.csv": "597e72aef32163ade0c676f64eb482376e5e48c3761e9dc44a71bb569c24933e",
    "vectorstore.csv": "bcdb9c00e505815fa0c4ded0b8f508ae94104b8d0de06b0c309c6c1d3e27faf6",
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
    """``days_required`` is the minimum --duration-days at which any of the
    scenario's specs becomes in range. VER-104 relaxed the validator from
    ``{1, 7}`` to any positive int so each scenario can gate at the day
    index of its earliest offset; the equality check (days_required must
    equal that earliest in-range day) lives in
    ``test_registry.test_scenarios_days_required_valid``.
    """
    days_required = amc.SCENARIOS[slug].days_required
    assert isinstance(days_required, int) and days_required >= 1, (
        f"SCENARIOS[{slug!r}].days_required {days_required!r} must be a positive int"
    )


@pytest.mark.parametrize("slug", sorted(THREE_MULTI_DAY_SCENARIOS))
def test_scenario_components_touched_exist(amc, slug):
    components = amc.SCENARIOS[slug].components_touched
    assert components, f"SCENARIOS[{slug!r}].components_touched must be non-empty"
    unknown = set(components) - set(amc.COMPONENTS.keys())
    assert not unknown, (
        f"SCENARIOS[{slug!r}].components_touched contains unknown component(s): {unknown}"
    )


def test_three_multi_day_scenarios_require_multi_day_runs(amc):
    """The 3 migrated multi-day cascading scenarios all have specs that span past
    Day 1, so they must declare ``days_required >= 2`` and a default 1-day run
    must drop them with a stderr warning (the VER-103 acceptance criterion).

    VER-104 narrowed each scenario's ``days_required`` to its actual minimum
    in-range day (e.g. ``cache_leak_restart`` and ``db_disk_exhaustion`` start
    on Day 2, ``jwks_rotation_chaos`` starts on Day 3) so shorter multi-day
    runs emit the in-range portion the legacy path used to emit. The test
    therefore checks ``>= 2`` rather than the original ``== 7``.
    """
    for slug in THREE_MULTI_DAY_SCENARIOS:
        scenario = amc.SCENARIOS[slug]
        assert scenario.days_required >= 2, (
            f"SCENARIOS[{slug!r}].days_required must be >= 2 (multi-day scenario)"
        )


# ------------------------------------------------------------------
# Spec-schema validator (_validate_scenario_spec, VER-130)
# ------------------------------------------------------------------
def _good_primary_spec():
    """Well-formed primary spec used as a baseline by validator tests."""
    return {
        "time_offset": 60,
        "metric": "error_rate",
        "description": "Synthetic baseline spec for validator tests",
        "generator": lambda ts, idx: 0.5,
    }


def _good_cascade_spec():
    """Well-formed cascade spec used as a baseline by validator tests."""
    return {
        "time_offset": 60,
        "metric": "error_rate",
        "description": "Synthetic cascade spec for validator tests",
        "generator": lambda ts, idx: 0.5,
    }


def test_validate_scenario_spec_happy_path_primary(amc):
    spec = _good_primary_spec()
    spec["duration_seconds"] = 30
    spec["shape"] = "ramp_linear"
    spec["shape_params"] = {"start": 0.1, "end": 0.9}
    assert amc._validate_scenario_spec(
        "test_slug", "apigateway", spec, is_cascade=False
    ) is None


def test_validate_scenario_spec_happy_path_cascade(amc):
    assert amc._validate_scenario_spec(
        "test_slug", "apigateway", _good_cascade_spec(), is_cascade=True
    ) is None


def test_validate_scenario_spec_valid_anomaly_shapes_constant(amc):
    assert amc._VALID_ANOMALY_SHAPES == frozenset({
        "step", "sustained", "ramp_linear", "ramp_exp", "sawtooth", "sine",
    })


@pytest.mark.parametrize(
    "missing_key", ["time_offset", "metric", "description", "generator"]
)
def test_validate_scenario_spec_missing_required_key(amc, missing_key):
    spec = _good_primary_spec()
    del spec[missing_key]
    with pytest.raises(ValueError, match=missing_key) as excinfo:
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)
    assert "test_slug" in str(excinfo.value)


def test_validate_scenario_spec_unknown_metric(amc):
    spec = _good_primary_spec()
    spec["metric"] = "this_metric_does_not_exist"
    with pytest.raises(ValueError, match="this_metric_does_not_exist") as excinfo:
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)
    msg = str(excinfo.value)
    assert "test_slug" in msg
    assert "apigateway" in msg


def test_validate_scenario_spec_unknown_metric_on_cascade(amc):
    spec = _good_cascade_spec()
    spec["metric"] = "ghost_metric"
    with pytest.raises(ValueError, match="ghost_metric"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=True)


def test_validate_scenario_spec_non_callable_generator(amc):
    spec = _good_primary_spec()
    spec["generator"] = 42
    with pytest.raises(ValueError, match="generator") as excinfo:
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)
    assert "test_slug" in str(excinfo.value)


def test_validate_scenario_spec_negative_time_offset(amc):
    spec = _good_primary_spec()
    spec["time_offset"] = -1
    with pytest.raises(ValueError, match="time_offset"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_non_numeric_time_offset(amc):
    spec = _good_primary_spec()
    spec["time_offset"] = "5"
    with pytest.raises(ValueError, match="time_offset"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_boolean_time_offset_rejected(amc):
    """True/False are int subclasses; the validator must reject them so a
    stray boolean doesn't silently round to row 1."""
    spec = _good_primary_spec()
    spec["time_offset"] = True
    with pytest.raises(ValueError, match="time_offset"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


@pytest.mark.parametrize("bad_offset", [float("nan"), float("inf"), float("-inf")])
def test_validate_scenario_spec_non_finite_time_offset_rejected(amc, bad_offset):
    """NaN and infinities must be rejected; they'd crash row-index conversion at runtime."""
    spec = _good_primary_spec()
    spec["time_offset"] = bad_offset
    with pytest.raises(ValueError, match="time_offset"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_empty_description(amc):
    spec = _good_primary_spec()
    spec["description"] = ""
    with pytest.raises(ValueError, match="description"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_non_string_description(amc):
    spec = _good_primary_spec()
    spec["description"] = 12345
    with pytest.raises(ValueError, match="description"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_unknown_shape(amc):
    spec = _good_primary_spec()
    spec["shape"] = "explode"
    with pytest.raises(ValueError, match="explode") as excinfo:
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)
    msg = str(excinfo.value)
    for valid_shape in ("step", "sustained", "ramp_linear"):
        assert valid_shape in msg


def test_validate_scenario_spec_non_numeric_duration(amc):
    spec = _good_primary_spec()
    spec["duration_seconds"] = "60"
    with pytest.raises(ValueError, match="duration_seconds"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_boolean_duration_rejected(amc):
    spec = _good_primary_spec()
    spec["duration_seconds"] = True
    with pytest.raises(ValueError, match="duration_seconds"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


@pytest.mark.parametrize("bad_dur", [float("nan"), float("inf"), float("-inf")])
def test_validate_scenario_spec_non_finite_duration_rejected(amc, bad_dur):
    spec = _good_primary_spec()
    spec["duration_seconds"] = bad_dur
    with pytest.raises(ValueError, match="duration_seconds"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_negative_duration_rejected(amc):
    spec = _good_primary_spec()
    spec["duration_seconds"] = -1
    with pytest.raises(ValueError, match="duration_seconds"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_non_string_shape_rejected(amc):
    """Non-string shape must raise ValueError, not TypeError on unhashable lookup."""
    spec = _good_primary_spec()
    spec["shape"] = ["sustained"]
    with pytest.raises(ValueError, match="shape"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_three_arg_generator_with_shape_rejected(amc):
    """3-arg (ts, col, rng) generators are step-only; using one with a shape
    spec would silently pass t_within as rng. Validator must reject."""
    spec = _good_primary_spec()
    spec["shape"] = "sustained"
    spec["duration_seconds"] = 30
    spec["generator"] = lambda ts, idx, rng: 1.0
    with pytest.raises(ValueError, match="3-arg"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_three_arg_generator_with_duration_rejected(amc):
    """A 3-arg generator with duration_seconds > 0 hits the span path; reject."""
    spec = _good_primary_spec()
    spec["duration_seconds"] = 30
    spec["generator"] = lambda ts, idx, rng: 1.0
    with pytest.raises(ValueError, match="3-arg"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_three_arg_generator_step_only_allowed(amc):
    """3-arg (ts, col, rng) generators are allowed on plain step specs."""
    spec = _good_primary_spec()
    spec["generator"] = lambda ts, idx, rng: 1.0
    assert amc._validate_scenario_spec(
        "test_slug", "apigateway", spec, is_cascade=False
    ) is None


def test_validate_scenario_spec_five_arg_generator_with_shape_allowed(amc):
    """5-arg form is the canonical signature for shape/duration specs."""
    spec = _good_primary_spec()
    spec["shape"] = "sustained"
    spec["duration_seconds"] = 30
    spec["generator"] = lambda ts, idx, t_within, span_idx, rng: 1.0
    assert amc._validate_scenario_spec(
        "test_slug", "apigateway", spec, is_cascade=False
    ) is None


def test_validate_scenario_spec_non_dict_rejected(amc):
    """A None or non-dict spec must produce a ValueError, not a raw TypeError."""
    with pytest.raises(ValueError, match="not a dict"):
        amc._validate_scenario_spec("test_slug", "apigateway", None, is_cascade=False)


def test_validate_scenario_spec_non_string_metric_rejected(amc):
    """Unhashable metric values would raise TypeError on catalog lookup; must ValueError first."""
    spec = _good_primary_spec()
    spec["metric"] = ["error_rate"]
    with pytest.raises(ValueError, match="non-string metric"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


@pytest.mark.parametrize("bad_arity", [1, 4])
def test_validate_scenario_spec_step_path_rejects_wrong_arity(amc, bad_arity):
    """Plain step specs use the step path which calls (ts, col, rng) or
    (ts, col); 1-arg, 4-arg, and 5-arg generators must be rejected."""
    spec = _good_primary_spec()
    if bad_arity == 1:
        spec["generator"] = lambda ts: 1.0
    else:  # 4-arg
        spec["generator"] = lambda ts, idx, t, s: 1.0
    with pytest.raises(ValueError, match=f"{bad_arity}-arg"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_step_path_rejects_five_arg(amc):
    """5-arg generators belong on shape/duration specs, not the step path."""
    spec = _good_primary_spec()
    spec["generator"] = lambda ts, idx, t, s, rng: 1.0
    with pytest.raises(ValueError, match="5-arg"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_cascade_rejects_wrong_arity(amc):
    """Cascades use the step path; only 2-arg or 3-arg generators are valid."""
    spec = _good_cascade_spec()
    spec["generator"] = lambda ts, idx, t, s, rng: 1.0
    with pytest.raises(ValueError, match="5-arg"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=True)


def test_validate_scenario_spec_kwargs_does_not_bypass_arity(amc):
    """**kwargs does not add positional capacity; a (ts, col, rng, **kw)
    generator on a shape spec must still be rejected as 3-arg."""
    spec = _good_primary_spec()
    spec["shape"] = "sustained"
    spec["duration_seconds"] = 30
    spec["generator"] = lambda ts, idx, rng, **kw: 1.0
    with pytest.raises(ValueError, match="3-arg"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_var_args_skips_arity_check(amc):
    """*args makes positional arity unbounded; the validator must skip the check."""
    spec = _good_primary_spec()
    spec["shape"] = "sustained"
    spec["duration_seconds"] = 30
    spec["generator"] = lambda ts, idx, *args: 1.0
    assert amc._validate_scenario_spec(
        "test_slug", "apigateway", spec, is_cascade=False
    ) is None


def test_validate_scenario_spec_required_kwarg_only_rejected(amc):
    """Generators with required keyword-only params cannot be called by
    positional dispatch; the validator must reject them."""
    def gen(ts, col, *, rng):
        return 1.0
    spec = _good_primary_spec()
    spec["generator"] = gen
    with pytest.raises(ValueError, match="keyword-only"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_keyword_only_with_default_allowed(amc):
    """Keyword-only params with defaults don't block positional dispatch."""
    def gen(ts, col, *, scale=1.0):
        return 1.0 * scale
    spec = _good_primary_spec()
    spec["generator"] = gen
    assert amc._validate_scenario_spec(
        "test_slug", "apigateway", spec, is_cascade=False
    ) is None


def test_call_generator_within_span_dispatch_by_arity(amc):
    """_call_generator_within_span dispatches by inspected arity, not by
    catching TypeError. A 3-arg generator that raises TypeError internally
    must not be retried as 2-arg (which would hide the error and
    duplicate any side effects)."""
    calls = []
    def buggy_three_arg(ts, col, t_within):
        calls.append((ts, col, t_within))
        raise TypeError("internal bug — must not be swallowed")
    import datetime as _dt
    with pytest.raises(TypeError, match="internal bug"):
        amc._call_generator_within_span(buggy_three_arg, _dt.datetime(2026,1,1), 0, 1.0, 0)
    assert len(calls) == 1, "Generator must be called exactly once, not retried with 2-arg form"


def test_call_generator_within_span_var_args_picks_five_arg(amc):
    """*args generators get called with the 5-arg form (highest info)."""
    recorded = []
    def gen(*args):
        recorded.append(len(args))
        return 1.0
    import datetime as _dt
    amc._call_generator_within_span(gen, _dt.datetime(2026,1,1), 0, 2.0, 1, "rng-marker")
    assert recorded == [5]


def test_call_generator_within_span_unhashable_callable(amc):
    """Unhashable callables (e.g., mutable callable instances) must not crash
    the cache lookup; introspection falls back to uncached path."""
    class UnhashableCallable:
        __hash__ = None
        def __call__(self, ts, col, rng):
            return 42.0
    gen = UnhashableCallable()
    import datetime as _dt
    result = amc._call_generator_within_span(gen, _dt.datetime(2026,1,1), 0, 0.0, 0, None)
    assert result == 42.0


def test_resolve_anomaly_value_step_path_does_not_retry_on_internal_typeerror(amc):
    """The step path in _resolve_anomaly_value must dispatch by arity, not
    catch a 3-arg generator's internal TypeError and retry as 2-arg."""
    calls = []
    def buggy_three_arg(ts, col, rng):
        calls.append((ts, col, rng))
        raise TypeError("internal bug in step generator")
    import datetime as _dt
    spec = {"generator": buggy_three_arg}
    with pytest.raises(TypeError, match="internal bug"):
        amc._resolve_anomaly_value(spec, _dt.datetime(2026,1,1), 0, 0.0, 0, None)
    assert len(calls) == 1


def test_validate_scenario_spec_non_dict_shape_params(amc):
    spec = _good_primary_spec()
    spec["shape_params"] = [1, 2, 3]
    with pytest.raises(ValueError, match="shape_params"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


@pytest.mark.parametrize(
    "forbidden_key,forbidden_value",
    [
        ("shape", "step"),
        ("duration_seconds", 60),
        ("shape_params", {"start": 0.1, "end": 0.9}),
    ],
)
def test_validate_scenario_spec_cascade_rejects_shape_keys(
    amc, forbidden_key, forbidden_value
):
    spec = _good_cascade_spec()
    spec[forbidden_key] = forbidden_value
    with pytest.raises(ValueError, match=forbidden_key) as excinfo:
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=True)
    msg = str(excinfo.value)
    assert "test_slug" in msg
    assert "cascade" in msg.lower()


def test_validate_scenarios_registry_walks_every_spec(amc):
    """Live registry must satisfy the new schema checks today. If this
    breaks, the offending spec needs fixing in SCENARIOS, not the validator.
    """
    amc._validate_scenarios_registry()


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
    order would otherwise vary by interpreter hash randomization.

    Computes the expected set dynamically (any registry slug whose
    ``severity`` is outside the active hierarchy or whose
    ``days_required`` exceeds the run's ``--duration-days``) so the test
    stays correct as new scenarios are added.
    """
    days = 1
    signal_level = "medium"
    allowed_severities = amc.SIGNAL_LEVELS[signal_level]
    result = run_capture(
        amc, tmp_path, days=days,
        extra_args=["--scenarios", "all", "--signal-level", signal_level],
    )
    warning_slugs = [
        line.split()[2]
        for line in result.stderr.splitlines()
        if line.startswith("WARNING: scenario ")
    ]
    expected = sorted(
        slug for slug, scenario in amc.SCENARIOS.items()
        if scenario.severity not in allowed_severities
        or scenario.days_required > days
    )
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


# ------------------------------------------------------------------
# Per-slug isolation test: --scenarios <slug> emits only that scenario
# ------------------------------------------------------------------

def _extra_args_for_slug(amc, slug: str) -> list[str]:
    """Return extra_args (beyond seed/days/output-dir) to activate a single slug."""
    scenario = amc.SCENARIOS[slug]
    extra = ["--scenarios", slug, "--drop-rate", "0", "--interval-seconds", "60"]
    if scenario.severity == "high":
        extra += ["--signal-level", "high"]
    return extra


def _expected_events_for_slug(amc, slug: str) -> set[tuple[str, str, str]]:
    """Build the expected ``(component, metric, description)`` set for a slug.

    Primary specs land on their declared component; cascade specs land on the
    cascade's target component (the first element of each ``cascade_specs``
    tuple). This matches what ``_apply_scenarios`` writes into
    ``component_anomalies`` / ``cascading_anomalies``, which in turn is what
    ``anomalies.csv`` records row-by-row.
    """
    scenario = amc.SCENARIOS[slug]
    expected: set[tuple[str, str, str]] = set()
    for component, spec in scenario.primary_specs:
        expected.add((component, spec["metric"], spec["description"]))
    for target, cascade in scenario.cascade_specs:
        expected.add((target, cascade["metric"], cascade["description"]))
    return expected


def test_per_slug_isolation(amc, tmp_path):
    """For every slug in SCENARIOS, running with ``--scenarios <slug>`` produces
    a non-empty ``anomalies.csv`` whose every row matches one of that scenario's
    own primary or cascade specs by ``(component, metric, description)``.

    Checking only ``components_touched`` membership is too loose: e.g.
    ``auth_brute_force`` and ``monday_baseline`` both touch authservice +
    apigateway, so a leak between them would pass a components-only assertion.
    Comparing against the slug's own ``primary_specs`` + ``cascade_specs``
    (whose descriptions flow verbatim into the manifest) catches that.
    """
    for slug, scenario in amc.SCENARIOS.items():
        out = tmp_path / f"slug_{slug}"
        out.mkdir()
        days = max(scenario.days_required, 1)
        extra = _extra_args_for_slug(amc, slug)
        run = run_capture(amc, out, days=days, extra_args=extra)

        manifest = read_manifest(out)
        assert manifest, (
            f"--scenarios {slug} produced empty anomalies.csv "
            f"(severity={scenario.severity}, days_required={scenario.days_required})"
        )
        expected_events = _expected_events_for_slug(amc, slug)
        for row in manifest:
            key = (row["component"], row["metric"], row["description"])
            assert key in expected_events, (
                f"--scenarios {slug}: manifest row {key!r} is not declared by "
                f"this scenario's primary_specs or cascade_specs — looks like a "
                f"leak from another scenario."
            )


# ==================================================================
# VER-105: Composition matrix + validation + WARNING hardening
# ==================================================================
# Composition order locked by VER-102 plan:
#   allowlist (--scenarios) → exclude (--exclude-scenarios)
#   → severity (--signal-level) → duration (--duration-days)
#   → components (--components)
#
# The tests below lock in the intersection semantics of every selector
# pair, the validation surface (clear errors for unknown slugs, clear
# error for the 'all'+explicit-slug mix), and the stderr WARNING
# contract (exactly one line per dropped slug for severity/duration
# drops; silent drop for component-disjoint scenarios).

# Lightweight slug picks for the composition matrix. Locking these to
# named scenarios (rather than discovering them dynamically) keeps each
# test's intent obvious; the slug-vocabulary tests above already catch
# rename/removal of any of these slugs.
_LOW_SLUG = "monday_baseline"            # severity=low, days=1
_MEDIUM_1D_SLUG = "auth_brute_force"     # severity=medium, days=1, touches authservice/apigateway
_MEDIUM_MULTI_SLUG = "cache_leak_restart"  # severity=medium, days=2
_HIGH_1D_SLUG = "gateway_ddos"           # severity=high, days=1


def _stderr_warnings_for(slug: str, stderr: str) -> list[str]:
    """Return every ``WARNING: scenario <slug> …`` line in ``stderr``.

    The convention is one WARNING per dropped slug; this helper exists so
    the matching is one place and is robust to any extra warnings the
    generator may emit for unrelated reasons.
    """
    return [
        line for line in stderr.splitlines()
        if line.startswith(f"WARNING: scenario {slug} ")
    ]


def _all_scenario_descriptions(amc, slug: str) -> set[str]:
    """Return every primary + cascade description a scenario can emit.

    Dropping a scenario must remove both its primaries and its cascades
    from the manifest. Gate tests use this so a cascade-only leak can't
    silently slip through a primary-only assertion.
    """
    scenario = amc.SCENARIOS[slug]
    descs: set[str] = set()
    for _component, spec in scenario.primary_specs:
        descs.add(spec["description"])
    for _target, cascade in scenario.cascade_specs:
        descs.add(cascade["description"])
    return descs


# ------------------------------------------------------------------
# Composition matrix
# ------------------------------------------------------------------
def test_compose_scenarios_x_signal_level_low_drops_medium_with_warning(amc, tmp_path):
    """Medium-severity slug requested at ``--signal-level low`` → dropped from
    the manifest (both primaries **and** cascades) and emits exactly one
    WARNING naming the slug.
    """
    result = run_capture(
        amc, tmp_path, days=1,
        extra_args=["--scenarios", _MEDIUM_1D_SLUG, "--signal-level", "low"],
    )
    descriptions = {row["description"] for row in read_manifest(result.out_dir)}
    scenario_descs = _all_scenario_descriptions(amc, _MEDIUM_1D_SLUG)
    leaked = scenario_descs & descriptions
    assert not leaked, (
        f"{_MEDIUM_1D_SLUG} primaries/cascades leaked at --signal-level low: {leaked}"
    )
    warnings = _stderr_warnings_for(_MEDIUM_1D_SLUG, result.stderr)
    assert len(warnings) == 1, (
        f"Expected exactly one WARNING for {_MEDIUM_1D_SLUG} at --signal-level low; "
        f"got {len(warnings)}: {warnings!r}"
    )
    assert "--signal-level" in warnings[0]


def test_compose_scenarios_x_signal_level_medium_drops_high_with_warning(amc, tmp_path):
    """High-severity slug requested at the default ``--signal-level medium`` →
    dropped from the manifest (both primaries **and** cascades) and emits
    exactly one WARNING naming the slug.
    """
    result = run_capture(
        amc, tmp_path, days=1,
        extra_args=["--scenarios", _HIGH_1D_SLUG],
    )
    descriptions = {row["description"] for row in read_manifest(result.out_dir)}
    scenario_descs = _all_scenario_descriptions(amc, _HIGH_1D_SLUG)
    leaked = scenario_descs & descriptions
    assert not leaked, (
        f"{_HIGH_1D_SLUG} primaries/cascades leaked at --signal-level medium: {leaked}"
    )
    warnings = _stderr_warnings_for(_HIGH_1D_SLUG, result.stderr)
    assert len(warnings) == 1, (
        f"Expected exactly one WARNING for {_HIGH_1D_SLUG} at --signal-level medium; "
        f"got {len(warnings)}: {warnings!r}"
    )
    assert "--signal-level" in warnings[0]


def test_compose_scenarios_x_duration_days_short_run_drops_multi_day(amc, tmp_path):
    """Multi-day slug on a 1-day run → dropped from the manifest (both
    primaries **and** cascades) and emits exactly one WARNING naming
    the slug.
    """
    result = run_capture(
        amc, tmp_path, days=1,
        extra_args=["--scenarios", _MEDIUM_MULTI_SLUG],
    )
    descriptions = {row["description"] for row in read_manifest(result.out_dir)}
    scenario_descs = _all_scenario_descriptions(amc, _MEDIUM_MULTI_SLUG)
    leaked = scenario_descs & descriptions
    assert not leaked, (
        f"{_MEDIUM_MULTI_SLUG} primaries/cascades leaked at --duration-days 1: {leaked}"
    )
    warnings = _stderr_warnings_for(_MEDIUM_MULTI_SLUG, result.stderr)
    assert len(warnings) == 1, (
        f"Expected exactly one WARNING for {_MEDIUM_MULTI_SLUG} at --duration-days 1; "
        f"got {len(warnings)}: {warnings!r}"
    )
    assert "--duration-days" in warnings[0]


def test_compose_scenarios_x_components_disjoint_drops_silently(amc, tmp_path):
    """Scenario whose ``components_touched`` is disjoint from ``--components``
    is dropped silently — no WARNING is emitted because the user restricted
    components on purpose and the scenario could not have produced output
    under that allowlist anyway.

    ``monday_baseline`` touches ``authservice`` + ``apigateway``; restricting
    components to a disjoint set (``database``) drops it. Both primaries and
    cascades are checked.
    """
    result = run_capture(
        amc, tmp_path, days=1,
        extra_args=[
            "--scenarios", _LOW_SLUG,
            "--signal-level", "low",
            "--components", "database",
        ],
    )
    descriptions = {row["description"] for row in read_manifest(result.out_dir)}
    scenario_descs = _all_scenario_descriptions(amc, _LOW_SLUG)
    leaked = scenario_descs & descriptions
    assert not leaked, (
        f"{_LOW_SLUG} primaries/cascades leaked under --components database: {leaked}"
    )
    warnings = _stderr_warnings_for(_LOW_SLUG, result.stderr)
    assert warnings == [], (
        f"Component-disjoint drops must be silent; got: {warnings!r}"
    )


def test_compose_scenarios_x_components_overlap_survives(amc, tmp_path):
    """When ``components_touched`` overlaps the ``--components`` allowlist,
    the scenario survives the filter — its primaries that target the kept
    components appear in the manifest.
    """
    result = run_capture(
        amc, tmp_path, days=1,
        extra_args=[
            "--scenarios", _LOW_SLUG,
            "--signal-level", "low",
            "--components", "authservice",
        ],
    )
    descriptions = {row["description"] for row in read_manifest(result.out_dir)}
    expected_descs = {
        spec["description"]
        for component, spec in amc.SCENARIOS[_LOW_SLUG].primary_specs
        if component == "authservice"
    }
    assert expected_descs, (
        f"{_LOW_SLUG} has no authservice primaries to assert against; pick "
        f"a different overlap test slug"
    )
    missing = expected_descs - descriptions
    assert not missing, (
        f"{_LOW_SLUG} authservice primaries missing under --components authservice: {missing}"
    )


def test_compose_scenarios_x_exclude_scenarios_overlap_excludes_wins(amc, tmp_path):
    """When ``--exclude-scenarios`` overlaps ``--scenarios``, the exclusion
    wins: every slug named in both lists is dropped from the resolved set
    (both primaries and cascades) and emits no WARNING (it was excluded
    by the user, not by a gate).
    """
    result = run_capture(
        amc, tmp_path, days=7,
        extra_args=[
            "--scenarios", f"{_MEDIUM_MULTI_SLUG},jwks_rotation_chaos",
            "--exclude-scenarios", _MEDIUM_MULTI_SLUG,
        ],
    )
    descriptions = {row["description"] for row in read_manifest(result.out_dir)}

    excluded_descs = _all_scenario_descriptions(amc, _MEDIUM_MULTI_SLUG)
    leaked = excluded_descs & descriptions
    assert not leaked, (
        f"{_MEDIUM_MULTI_SLUG} primaries/cascades survived --exclude-scenarios overlap: {leaked}"
    )
    # The non-excluded scenario in the allowlist still fires.
    kept = SCENARIO_PRIMARIES_BY_SLUG["jwks_rotation_chaos"] & descriptions
    assert kept, (
        "jwks_rotation_chaos primaries missing even though it was in --scenarios "
        "and not in --exclude-scenarios"
    )
    # Exclusion is silent — no WARNING emitted for the excluded slug.
    assert _stderr_warnings_for(_MEDIUM_MULTI_SLUG, result.stderr) == []


# ------------------------------------------------------------------
# Validation errors
# ------------------------------------------------------------------
def test_validation_scenarios_unknown_slug_error_message(amc, capsys):
    """``--scenarios <unknown>`` exits non-zero and the error names the bad
    slug along with the catalog so the user can fix the typo.
    """
    with pytest.raises(SystemExit) as excinfo:
        amc.parse_args([
            "--scenarios", "not_a_scenario",
            "--output-dir", "test_out",
        ])
    assert excinfo.value.code != 0
    err = capsys.readouterr().err
    assert "not_a_scenario" in err, f"Error must name the bad slug; got: {err!r}"
    # Catalog should be advertised so the user can pick a valid one.
    for slug in sorted(amc.SCENARIOS.keys()):
        assert slug in err, (
            f"Error message must list the full catalog (missing {slug!r}); got: {err!r}"
        )


def test_validation_exclude_scenarios_unknown_slug_error_message(amc, capsys):
    """``--exclude-scenarios <unknown>`` exits non-zero and the error names
    the bad slug along with the catalog.
    """
    with pytest.raises(SystemExit) as excinfo:
        amc.parse_args([
            "--exclude-scenarios", "not_a_scenario",
            "--output-dir", "test_out",
        ])
    assert excinfo.value.code != 0
    err = capsys.readouterr().err
    assert "not_a_scenario" in err, f"Error must name the bad slug; got: {err!r}"
    for slug in sorted(amc.SCENARIOS.keys()):
        assert slug in err, (
            f"Error message must list the full catalog (missing {slug!r}); got: {err!r}"
        )


def test_validation_scenarios_all_plus_explicit_slug_mutually_exclusive(amc, capsys):
    """``--scenarios all,<slug>`` is rejected: 'all' is a sentinel meaning
    every scenario, so mixing it with explicit slugs is ambiguous. Catches
    both ``all,unknown`` (covered by ``..._unknown_slug_...`` above) and
    ``all,<valid>`` (this test).
    """
    with pytest.raises(SystemExit) as excinfo:
        amc.parse_args([
            "--scenarios", f"all,{_MEDIUM_MULTI_SLUG}",
            "--output-dir", "test_out",
        ])
    assert excinfo.value.code != 0
    err = capsys.readouterr().err
    assert "all" in err and "mutually exclusive" in err, (
        f"Error must call out the 'all'+explicit mutual exclusion; got: {err!r}"
    )


def test_validation_scenarios_all_plus_unknown_slug_fails(amc):
    """``--scenarios all,foo`` exits non-zero. Today this is caught by the
    mutual-exclusion guard (``all`` plus anything else); previously it was
    caught by the unknown-slug check. Either path is acceptable — the
    contract is that the combination is not accepted.
    """
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--scenarios", "all,not_a_scenario",
            "--output-dir", "test_out",
        ])


# ------------------------------------------------------------------
# Out-of-range WARNING tests (exactly one line per dropped slug)
# ------------------------------------------------------------------
def test_warning_multi_day_slug_on_one_day_run_emits_exactly_one_line(amc, tmp_path):
    """Multi-day slug on a 1-day run → exactly one stderr WARNING line for
    that slug. Convention matches ``_resolve_scenarios``:
    ``WARNING: scenario <slug> requires --duration-days >= N (current: 1); skipped.``
    """
    result = run_capture(
        amc, tmp_path, days=1,
        extra_args=["--scenarios", _MEDIUM_MULTI_SLUG],
    )
    warnings = _stderr_warnings_for(_MEDIUM_MULTI_SLUG, result.stderr)
    assert len(warnings) == 1, (
        f"Expected exactly one WARNING for {_MEDIUM_MULTI_SLUG}; "
        f"got {len(warnings)}: {warnings!r}"
    )
    expected_days = amc.SCENARIOS[_MEDIUM_MULTI_SLUG].days_required
    line = warnings[0]
    assert "--duration-days" in line
    assert f">= {expected_days}" in line
    assert "skipped" in line


def test_warning_high_pressure_slug_at_medium_signal_emits_exactly_one_line(amc, tmp_path):
    """High-severity slug at ``--signal-level medium`` → exactly one stderr
    WARNING line for that slug, and zero rows from it in the manifest.
    """
    result = run_capture(
        amc, tmp_path, days=1,
        extra_args=["--scenarios", _HIGH_1D_SLUG],
    )
    warnings = _stderr_warnings_for(_HIGH_1D_SLUG, result.stderr)
    assert len(warnings) == 1, (
        f"Expected exactly one WARNING for {_HIGH_1D_SLUG} at --signal-level medium; "
        f"got {len(warnings)}: {warnings!r}"
    )
    assert "--signal-level high" in warnings[0]
    assert "skipped" in warnings[0]

    descriptions = {row["description"] for row in read_manifest(result.out_dir)}
    primary_descs = {
        spec["description"]
        for component, spec in amc.SCENARIOS[_HIGH_1D_SLUG].primary_specs
    }
    assert not (primary_descs & descriptions)


# ------------------------------------------------------------------
# --anomaly-count + --scenarios interaction
# ------------------------------------------------------------------
def test_anomaly_count_with_scenarios_restricts_sampling_pool(amc, tmp_path):
    """With ``--scenarios <slug> --anomaly-count N``, every manifest row
    belongs to the selected scenario (the sampling pool is restricted to
    that scenario's primaries + cascades). N is honoured exactly when the
    pool is at least that large.
    """
    slug = _MEDIUM_MULTI_SLUG
    out = tmp_path / "anomaly_count_scenarios"
    out.mkdir()
    extra = [
        "--scenarios", slug,
        "--anomaly-count", "5",
        "--drop-rate", "0",
        "--interval-seconds", "60",
    ]
    run_capture(amc, out, days=7, extra_args=extra)
    manifest = read_manifest(out)
    assert len(manifest) == 5, (
        f"--anomaly-count 5 should produce exactly 5 manifest rows; got {len(manifest)}"
    )
    expected = _expected_events_for_slug(amc, slug)
    for row in manifest:
        key = (row["component"], row["metric"], row["description"])
        assert key in expected, (
            f"--anomaly-count + --scenarios {slug}: manifest row {key!r} is "
            f"not declared by this scenario's primary_specs or cascade_specs"
        )


def test_anomaly_count_with_scenarios_is_deterministic_for_seed(amc, tmp_path):
    """Two ``--scenarios <slug> --anomaly-count N`` runs at the same
    ``--seed`` produce byte-identical ``anomalies.csv``. The cap RNG is
    seeded off the same SeedSequence as ``--seed``, so the sampled
    positions are stable.
    """
    slug = _MEDIUM_MULTI_SLUG
    out_a = tmp_path / "anomaly_count_det_a"
    out_b = tmp_path / "anomaly_count_det_b"
    out_a.mkdir()
    out_b.mkdir()
    extra = [
        "--scenarios", slug,
        "--anomaly-count", "5",
        "--drop-rate", "0",
        "--interval-seconds", "60",
    ]
    run_capture(amc, out_a, days=7, extra_args=extra)
    run_capture(amc, out_b, days=7, extra_args=extra)
    assert _sha256_path(out_a / "anomalies.csv") == _sha256_path(out_b / "anomalies.csv"), (
        "Two runs of --scenarios + --anomaly-count at the same --seed must "
        "produce byte-identical anomalies.csv"
    )

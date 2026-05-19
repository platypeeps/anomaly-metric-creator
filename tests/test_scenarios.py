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
* Default-output byte-for-byte regression — locked SHA-256 hashes for
  every per-component CSV and ``anomalies.csv`` from a default 1-day
  run at seed 42 and a 7-day run at seed 42. After VER-156 phase 6
  the default is ``--topology-mode realistic`` with the integer-cast
  bundle on, so the constants below
  (``DEFAULT_ONE_DAY_HASHES`` / ``DEFAULT_SEVEN_DAY_HASHES`` /
  ``HIGH_SEVEN_DAY_CAPPED_HASHES``) capture realistic-mode bytes;
  the high-signal + ``--anomaly-count`` capped 7-day hashes were
  captured against the post-VER-104 sampling-pool ordering, which
  this PR did not change. The pre-flag-day independent baseline is
  preserved verbatim in ``LEGACY_INDEPENDENT_ONE_DAY_HASHES`` and is
  pinned by
  ``tests/test_topology_loadbalancer_gateway.py::test_topology_mode_independent_matches_legacy_baseline_byte_for_byte``.
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
# SHA-256 hashes of per-component CSVs under the current default flags
# (seed 42, ``--topology-mode realistic``, integer-cast bundle on).
# Locking these protects every component output from accidental
# RNG-order, spec-order, or topology-coupling drift.
#
# VER-156 (2026-05-19) re-baselined every entry here as the phase-6
# flag-day landing: ``--topology-mode realistic`` is now the default
# and ``dtype="int"`` columns are rounded via ``np.rint`` before
# derivations. The pre-flag-day independent baseline (the lineage
# locked by VER-132 / VER-104 historical commits) lives in
# ``LEGACY_INDEPENDENT_ONE_DAY_HASHES`` below and is pinned by
# ``test_topology_mode_independent_matches_legacy_baseline_byte_for_byte``;
# the legacy table is the byte-for-byte parity reference for the
# deprecation alias and is scheduled for removal after VER-141 phase 9.
# When updating these hashes again, regenerate against the realistic
# default rather than the legacy alias.
# ------------------------------------------------------------------
DEFAULT_ONE_DAY_HASHES = {
    "anomalies.csv": "458703b3da32183889d7a2ca68840f1da05fd00a78cc95aff4e530c2cd5cbb06",
    "apigateway.csv": "fd2de17267e6c1e0969d7f657f6c9658c197441df3b32bcdf513b222c9fe67cd",
    "authservice.csv": "b199dc119b6780725729f557208d6437b60f059d21d98dcd975771c5c34b3594",
    "cacheservice.csv": "aaea7333b9cd47bf2806129945f0dddeef861cfdd8c7bd0c665a29c4d62b3158",
    "database.csv": "419790bd3780b836c6018d56d830003f68ea6b36b48017007642ef3fa6054ec3",
    "identityprovider.csv": "6ccf54d998faadb8cf2bee8a7e35b4b6f6ec406b6cff920121b2066220aeb4e1",
    "llm_analytics.csv": "6a464b9b4c2be8d919061a5136bc3bde9c2682ccb6c1d0f3cbee220072653ae9",
    "loadbalancer.csv": "c1e1ea63928870c6905b863f4f14ed0e990012ddab7919ab00707a82c4ab00b2",
    "mqservice.csv": "17af32099252ec55c98b6090565848932668d02ef22edd448d40e581b9e8827a",
    "objectstore.csv": "a6993057d62c0565cc9ca495db85d08e4a6186660b3c220ff7389bb4be21bc69",
    "observabilitypipeline.csv": "dfd89b922312ee53c8afc5ebcb26d310f6466ca4fc8753f53ea6e69e901748fc",
    "paymentservice.csv": "f60145f9f360c2a0c785c869cd046eda5895672d40b0ec2ec0caef5af1f27ba1",
    "scheduler.csv": "1ae06a98848fe404da0af873826ed7de8e653eed78deb8f67ef49c973e7752a1",
    "vectorstore.csv": "e3a0b6e511ca879eebbe08cffff59fb02df7d36bb2f30ae0b8075dcda84e0955",
}

DEFAULT_SEVEN_DAY_HASHES = {
    "anomalies.csv": "8af60c0064dee35f53e0a77d635f36d23eb36df5cbaa858c77c99e30bfbf5923",
    "apigateway.csv": "4d24ad92fd63dc917ab8fadec37f3d27d31de4e10c41aebeb1a8b275a6be7cc7",
    "authservice.csv": "8fe3ade4c6b1a7e93f6d8918d9b7ef98acc4bd4b786e196e6dfc6907f756fca8",
    "cacheservice.csv": "a92c39968368f9ecc468b36e55edbae6461bc9e4b84631f4084236d4ad7f0d19",
    "database.csv": "0a22feee822a880d2487a13e3355b20e93db2eefea4da31d0a55218d47ef33ad",
    "identityprovider.csv": "926b28780af3efc4815ed964dd03c3ac8d686dcb3f8236e5cce71dea7530ae67",
    "llm_analytics.csv": "fb4230f32deafa22a78ff22431493a807ab7202db2d5e32e0e652e049df30ab9",
    "loadbalancer.csv": "fcb55773a22331cbf249aadc0b4e7f5eb5fcfe9d4c1dede58270366fa8ac7c6f",
    "mqservice.csv": "1c04a5b8295b2d2da45a1dc033002b84e1e9a741135be4314e7d5ed296d57f3d",
    "objectstore.csv": "a21108e432b068a15bde2c8790b48b8961f792a87bd00227e3d18f965a75b88f",
    "observabilitypipeline.csv": "5e6cae855793d2e14b258b6b0801f7a7958775281baa43809d99a86e28daf6b4",
    "paymentservice.csv": "923dc4369f426c66146fabbce6b3306d81213fcb731bc43a15642913cd743425",
    "scheduler.csv": "27a47467d91902604ac182b661bada4ac92daafa4b980b6071d8d5e803d1bd7b",
    "vectorstore.csv": "5fafee33e0e394a20d11b0984b8856ff8d708ef109bdc72f83d84ae476cd4e93",
}

# Pre-flag-day baseline hashes captured under the original
# ``--topology-mode independent`` mode (no topology coupling, no
# integer-cast bundle). VER-156 phase 6 retained ``--topology-mode
# independent`` as a deprecation alias whose CSV bytes must remain
# byte-for-byte identical to this baseline, so test_topology_loadbalancer_gateway
# pins them against the alias output to catch any silent drift in the
# deprecated path before it is removed (scheduled for after VER-141 phase 9).
LEGACY_INDEPENDENT_ONE_DAY_HASHES = {
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

# SHA-256 hashes for ``--signal-level high --duration-days 7 --anomaly-count 100``
# at seed 42 under the current defaults (``--topology-mode realistic``,
# integer-cast bundle on). VER-156 (2026-05-19) re-baselined this block
# alongside ``DEFAULT_ONE_DAY_HASHES`` / ``DEFAULT_SEVEN_DAY_HASHES`` as
# part of the phase-6 flag-day landing; the lineage of this golden set
# (the post-VER-104 registry-only spec ordering, commit f6bd453, that
# stabilizes the ``--anomaly-count`` sampling pool) is unchanged, only
# the resulting bytes shifted under realistic-mode coupling.
# Locking these protects the deterministic ``--anomaly-count`` sampling
# pool from drift in the positional order of registry specs:
# ``_apply_signal_level_and_count()`` seeds a ``SeedSequence`` with
# ``spawn_key=(_ANOMALY_COUNT_CAP_SALT,)`` and picks ``anomaly_count``
# positions out of the in-range pool, so any reshuffle of SCENARIOS
# insertion order or of per-component append ordering changes which
# anomalies land in the manifest. Regenerate against the realistic
# default when re-baselining.
HIGH_SEVEN_DAY_CAPPED_HASHES = {
    "anomalies.csv": "ecb240779f48662e028c86541b8069feab257ef19cfb7aa3fc0c4045dca0478d",
    "apigateway.csv": "b8182e5688ad165dbf2cbbeea67a4a026c91bd1cbd9f8b886bd8aed61d775b65",
    "authservice.csv": "88c87df55ff786943f5523061284111f50b5acf93bceed6f54fa6f7134c07d80",
    "cacheservice.csv": "79bf073239f0dcfddfb4cc0b60b05ad36b923840b00655f2a69e3a5cc350fee8",
    "database.csv": "b082c02d538d00b87a0b62bac9e823ee523a9bb1a94cf4cefe7f25c3449075b6",
    "identityprovider.csv": "176a04f1f66fe0cafc515af0d57dad175625c20d7c10721fd4a65541b48b22f0",
    "llm_analytics.csv": "d8ac67e68997f7c576aa21a553d9136bf01ea4bb612307089fdd2b89978bb117",
    "loadbalancer.csv": "534153413847720ba23bbbf96eeb0f2143e4e183626c847ebf5b0e777718d705",
    "mqservice.csv": "b9e47b75f2da12a24f07c53a2984083b73261da90371d2f81058feb4ed1baf23",
    "objectstore.csv": "2581e0799ed8906fa3c10a2785c4645a34be11dd1952126c4786ada9ce5ae888",
    "observabilitypipeline.csv": "c9cf7d9f2a7ab4a8fff351adae2fd94da66da5d1e0e7be78361bfffabcc03947",
    "paymentservice.csv": "9958a7913fc6a79b72990f261e7cdba4afc0ab76d933ec3c6e1a9b25d1f0ba19",
    "scheduler.csv": "a85429392998b5bf207e83ca543d2cf90473d69d21fd60fe70a77758667fdb00",
    "vectorstore.csv": "b2fb7639cde8d2944e39adbeb91ae6d42cce5d7fe0210e8a4d0357e6e2b24ccc",
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


_RESOLVER_SHAPES = frozenset({
    "step", "sustained", "ramp_linear", "ramp_exp", "sawtooth", "sine",
})


def _load_vocab_shapes_for_parametrize():
    """Load _VALID_ANOMALY_SHAPES at pytest collection time so the test
    below is parametrized over the LIVE vocab rather than a hard-coded
    list. Adding a shape to _VALID_ANOMALY_SHAPES automatically extends
    test coverage."""
    import importlib.util as _u
    import os as _os
    script = _os.environ.get(
        "SCRIPT_PATH",
        _os.path.join(_os.path.dirname(__file__), "..",
                      "anomaly-metric-creator.py"),
    )
    spec = _u.spec_from_file_location("amc_for_parametrize", script)
    m = _u.module_from_spec(spec)
    spec.loader.exec_module(m)
    return sorted(m._VALID_ANOMALY_SHAPES)


def test_validate_scenario_spec_valid_anomaly_shapes_constant(amc):
    """Forward consistency: every shape branch the resolver dispatches on
    must be present in ``_VALID_ANOMALY_SHAPES``. ``_RESOLVER_SHAPES`` is
    the test-side ground truth for what branches exist in
    ``_resolve_anomaly_value``; if a branch is added there, add it here."""
    assert isinstance(amc._VALID_ANOMALY_SHAPES, frozenset)
    missing = _RESOLVER_SHAPES - amc._VALID_ANOMALY_SHAPES
    assert not missing, (
        f"Resolver shapes {sorted(missing)} are dispatched by "
        f"_resolve_anomaly_value but missing from _VALID_ANOMALY_SHAPES"
    )


@pytest.mark.parametrize("shape", _load_vocab_shapes_for_parametrize())
def test_every_valid_shape_is_dispatched_by_resolver(amc, shape):
    """Reverse consistency: every shape in ``_VALID_ANOMALY_SHAPES`` must be
    handled by ``_resolve_anomaly_value``. If a future change adds a shape
    to the vocabulary without wiring it into the resolver, validator would
    accept specs that fail at runtime with 'Unsupported anomaly shape'.
    This test fixes that drift by exercising every vocab entry."""
    assert shape in amc._VALID_ANOMALY_SHAPES
    import datetime as _dt
    spec = {
        "generator": lambda ts, col: 100.0,
        "shape": shape,
        "duration_seconds": 10,
        "shape_params": {"start": 0.0, "end": 1.0, "amplitude": 1.0,
                         "midline": 0.0, "period_s": 5.0},
    }
    # If resolver rejects this shape at runtime, the validator and resolver
    # have drifted. Any return value is fine; we just must not get the
    # "Unsupported anomaly shape" ValueError.
    try:
        amc._resolve_anomaly_value(spec, _dt.datetime(2026, 1, 1), 0, 1.0, 0, None)
    except ValueError as e:
        if "Unsupported anomaly shape" in str(e):
            pytest.fail(
                f"Shape {shape!r} is in _VALID_ANOMALY_SHAPES but the resolver "
                f"raises 'Unsupported anomaly shape' — vocabulary and resolver "
                f"have drifted. Wire {shape!r} into _resolve_anomaly_value or "
                f"remove it from the vocab."
            )
        raise


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
    with pytest.raises(ValueError, match="required_positional=3"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_three_arg_generator_with_duration_rejected(amc):
    """A 3-arg generator with duration_seconds > 0 hits the span path; reject."""
    spec = _good_primary_spec()
    spec["duration_seconds"] = 30
    spec["generator"] = lambda ts, idx, rng: 1.0
    with pytest.raises(ValueError, match="required_positional=3"):
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


def test_validate_scenario_spec_step_path_rejects_one_arg(amc):
    """Plain step specs need at least 2 positional params; (ts) fails."""
    spec = _good_primary_spec()
    spec["generator"] = lambda ts: 1.0
    with pytest.raises(ValueError, match="fixed_positional_count=1"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_step_path_rejects_four_arg(amc):
    """4 required positional > step target 3."""
    spec = _good_primary_spec()
    spec["generator"] = lambda ts, idx, t, s: 1.0
    with pytest.raises(ValueError, match="required_positional=4"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_step_path_rejects_five_arg(amc):
    """5-arg generators belong on shape/duration specs, not the step path."""
    spec = _good_primary_spec()
    spec["generator"] = lambda ts, idx, t, s, rng: 1.0
    with pytest.raises(ValueError, match="required_positional=5"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_cascade_rejects_wrong_arity(amc):
    """Cascades use the step path; only 2-arg or 3-arg generators are valid."""
    spec = _good_cascade_spec()
    spec["generator"] = lambda ts, idx, t, s, rng: 1.0
    with pytest.raises(ValueError, match="required_positional=5"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=True)


def test_validate_scenario_spec_kwargs_does_not_bypass_arity(amc):
    """**kwargs does not add positional capacity; a (ts, col, rng, **kw)
    generator on a shape spec must still be rejected as 3-positional."""
    spec = _good_primary_spec()
    spec["shape"] = "sustained"
    spec["duration_seconds"] = 30
    spec["generator"] = lambda ts, idx, rng, **kw: 1.0
    with pytest.raises(ValueError, match="required_positional=3"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_var_args_safe_prefix_accepted(amc):
    """*args with a safe 2-arg fixed prefix (ts, idx, *args) on a span spec
    is valid: the dispatcher calls 5-arg, ts/idx bind to the first 2 fixed
    positionals, and *args absorbs t_within/span_idx/rng."""
    spec = _good_primary_spec()
    spec["shape"] = "sustained"
    spec["duration_seconds"] = 30
    spec["generator"] = lambda ts, idx, *args: 1.0
    assert amc._validate_scenario_spec(
        "test_slug", "apigateway", spec, is_cascade=False
    ) is None


def test_validate_scenario_spec_var_args_unsafe_prefix_rejected_span(amc):
    """*args with 3-arg fixed prefix (ts, idx, rng, *args) on a span spec
    is REJECTED — the 5-arg dispatcher would bind t_within to the 3rd
    fixed positional (named rng), the exact misbind the validator is
    meant to catch."""
    spec = _good_primary_spec()
    spec["shape"] = "sustained"
    spec["duration_seconds"] = 30
    spec["generator"] = lambda ts, idx, rng, *args: 1.0
    with pytest.raises(ValueError, match="required_positional=3"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_var_args_four_arg_prefix_rejected_step(amc):
    """*args with a 4-arg fixed prefix (a, b, c, d, *args) on a step spec
    must be rejected — the 3-arg dispatcher call cannot satisfy
    required=4."""
    spec = _good_primary_spec()
    spec["generator"] = lambda a, b, c, d, *args: 1.0
    with pytest.raises(ValueError, match="required_positional=4"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_var_args_default_prefix_rejected_step(amc):
    """(ts, col, scale=1.0, *args) on step path: required=2, fixed=3, has_var.
    Dispatcher would call 3-arg and overwrite the scale default with rng.
    Misbind — reject."""
    spec = _good_primary_spec()
    spec["generator"] = lambda ts, col, scale=1.0, *args: 1.0
    with pytest.raises(ValueError, match="fixed_positional_count=3"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_var_args_default_prefix_rejected_span(amc):
    """(ts, col, scale=1.0, *args) on span path: required=2, fixed=3, has_var.
    Dispatcher would call 5-arg and overwrite the scale default with t_within.
    Misbind — reject."""
    spec = _good_primary_spec()
    spec["shape"] = "sustained"
    spec["duration_seconds"] = 30
    spec["generator"] = lambda ts, col, scale=1.0, *args: 1.0
    with pytest.raises(ValueError, match="fixed_positional_count=3"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_huge_int_time_offset_rejected(amc):
    """A Python int that overflows float representation is now rejected at
    import time — generate_component does `time_offset / interval` (float
    divide) at runtime, which would raise OverflowError there. Reject up
    front with the validator's clear ValueError."""
    spec = _good_primary_spec()
    spec["time_offset"] = 10 ** 400
    with pytest.raises(ValueError, match="overflows float"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_validate_scenario_spec_huge_int_duration_rejected(amc):
    """Same float-overflow rejection for duration_seconds."""
    spec = _good_primary_spec()
    spec["duration_seconds"] = 10 ** 400
    with pytest.raises(ValueError, match="overflows float"):
        amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)


def test_step_dispatcher_rejects_var_args_with_default_prefix(amc):
    """Direct callers of generate_component bypass the validator. The
    dispatcher itself must refuse the *args+default-prefix misbind case
    (e.g., (ts, col, scale=1.0, *args)) so unvalidated callers can't
    silently bind the RNG to the scale parameter."""
    def bad(ts, col, scale=1.0, *args):
        return 1.0
    import datetime as _dt
    spec = {"generator": bad}
    with pytest.raises(TypeError, match="fixed_positional_count"):
        amc._resolve_anomaly_value(spec, _dt.datetime(2026, 1, 1), 0, 0.0, 0, None)


def test_span_dispatcher_rejects_var_args_with_default_prefix(amc):
    """Same defensive check on the span path."""
    def bad(ts, col, scale=1.0, *args):
        return 1.0
    import datetime as _dt
    with pytest.raises(TypeError, match="fixed_positional_count"):
        amc._call_generator_within_span(bad, _dt.datetime(2026, 1, 1), 0, 1.0, 0, None)


def test_span_dispatcher_rejects_var_args_with_required_misbind(amc):
    """Span path required==3 with *args is a required-misbind, not a
    default-overwrite. (ts, col, rng, *args) would have t_within bound to
    the required rng slot. The dispatcher must defensively refuse, even
    though the validator should have caught this at import time."""
    def bad(ts, col, rng, *args):
        return 1.0
    import datetime as _dt
    with pytest.raises(TypeError, match="required_positional=3"):
        amc._call_generator_within_span(bad, _dt.datetime(2026, 1, 1), 0, 1.0, 0, None)


def test_span_dispatcher_accepts_var_args_with_required_target(amc):
    """The canonical required-target+*args form is safe: positions 1-5
    fill required, *args stays empty. Don't reject."""
    seen = []
    def gen(ts, col, t_within, span_idx, rng, *args):
        seen.append((t_within, span_idx, rng, args))
        return 1.0
    import datetime as _dt
    amc._call_generator_within_span(gen, _dt.datetime(2026, 1, 1), 0, 7.5, 3, "rng-marker")
    assert seen == [(7.5, 3, "rng-marker", ())]




def test_validate_scenario_spec_canonical_required_with_trailing_optional_step(amc):
    """Step spec with (ts, col, rng, extra=None): required=3, fixed=4.
    Dispatcher calls 3-arg (required==target), all required positions
    bind correctly, ``extra`` keeps its default. No misbind — accept."""
    spec = _good_primary_spec()
    spec["generator"] = lambda ts, col, rng, extra=None: 1.0
    assert amc._validate_scenario_spec(
        "test_slug", "apigateway", spec, is_cascade=False
    ) is None


def test_validate_scenario_spec_canonical_required_with_trailing_optional_span(amc):
    """Span spec with (ts, col, t_within, span_idx, rng, extra=None):
    required=5, fixed=6. Dispatcher calls 5-arg, all required bind,
    ``extra`` keeps default. No misbind — accept."""
    spec = _good_primary_spec()
    spec["shape"] = "sustained"
    spec["duration_seconds"] = 30
    spec["generator"] = lambda ts, col, t_within, span_idx, rng, extra=None: 1.0
    assert amc._validate_scenario_spec(
        "test_slug", "apigateway", spec, is_cascade=False
    ) is None


def test_validate_scenario_spec_default_positional_accepted_step(amc):
    """Step spec with (ts, col, rng=None, extra=None): required=2, max=4.
    Validator accepts. At runtime the required-based step dispatcher will
    call this generator with just (ts, col) — both rng and extra keep
    their declared defaults — because required_positional is 2."""
    spec = _good_primary_spec()
    spec["generator"] = lambda ts, col, rng=None, extra=None: 1.0
    assert amc._validate_scenario_spec(
        "test_slug", "apigateway", spec, is_cascade=False
    ) is None


def test_validate_scenario_spec_default_positional_accepted_span(amc):
    """Span spec with (ts, col, t=0, s=0, rng=None): required=2, max=5.
    Validator accepts. At runtime the required-based span dispatcher will
    call this generator with just (ts, col) — t/s/rng keep their declared
    defaults — because required_positional is 2."""
    spec = _good_primary_spec()
    spec["shape"] = "sustained"
    spec["duration_seconds"] = 30
    spec["generator"] = lambda ts, col, t=0.0, s=0, rng=None: 1.0
    assert amc._validate_scenario_spec(
        "test_slug", "apigateway", spec, is_cascade=False
    ) is None


def test_validate_scenario_spec_partial_default_positional_accepted_span(amc):
    """Span spec with (ts, col, rng=None): required=2, fixed=3, no *args.
    Under the required-based dispatch contract introduced in pass 6, the
    span dispatcher calls this generator with 2 args (required=2), so the
    rng=None default is preserved — no misbind risk. The validator accepts
    it. Authors who actually need the RNG must declare required=5 or
    use (ts, col, *args)."""
    spec = _good_primary_spec()
    spec["shape"] = "sustained"
    spec["duration_seconds"] = 30
    spec["generator"] = lambda ts, col, rng=None: 1.0
    assert amc._validate_scenario_spec(
        "test_slug", "apigateway", spec, is_cascade=False
    ) is None


def test_step_dispatcher_does_not_bind_rng_to_optional_third_arg(amc):
    """Required-based dispatch: a (ts, col, scale=1.0) step generator
    has required=2, so the dispatcher calls it 2-arg and ``scale`` keeps
    its default. The previous callability-based dispatch would have
    called 3-arg and silently bound the RNG object to ``scale``."""
    seen = []
    def gen(ts, col, scale=1.0):
        seen.append(scale)
        return 1.0
    import datetime as _dt
    spec = {"generator": gen}
    amc._resolve_anomaly_value(
        spec, _dt.datetime(2026, 1, 1), 0, 0.0, 0, "rng-marker"
    )
    assert seen == [1.0], (
        "scale must keep its default 1.0; the dispatcher must not bind the "
        "RNG object to an optional 3rd positional named scale"
    )


def test_span_dispatcher_does_not_bind_runtime_internals_to_optional_positions(amc):
    """Required-based dispatch on span path: a generator with
    (ts, col, scale=1.0, factor=2.0, baseline=0.0) has required=2, so the
    dispatcher calls it 2-arg. Defaults for scale/factor/baseline are
    preserved instead of being overwritten by t_within/span_idx/rng."""
    seen = []
    def gen(ts, col, scale=1.0, factor=2.0, baseline=0.0):
        seen.append((scale, factor, baseline))
        return 1.0
    import datetime as _dt
    amc._call_generator_within_span(
        gen, _dt.datetime(2026, 1, 1), 0, 7.5, 3, "rng-marker"
    )
    assert seen == [(1.0, 2.0, 0.0)], (
        "scale/factor/baseline must keep their defaults; dispatcher must "
        "not bind t_within/span_idx/rng to optional positions"
    )


def test_step_dispatcher_calls_three_arg_when_required(amc):
    """Generator with required=3 (canonical step rng form) gets 3-arg call."""
    seen = []
    def gen(ts, col, rng):
        seen.append(rng)
        return 1.0
    import datetime as _dt
    spec = {"generator": gen}
    amc._resolve_anomaly_value(spec, _dt.datetime(2026, 1, 1), 0, 0.0, 0, "rng-marker")
    assert seen == ["rng-marker"]


def test_span_dispatcher_calls_five_arg_when_required(amc):
    """Generator with required=5 (canonical span form) gets 5-arg call."""
    seen = []
    def gen(ts, col, t_within, span_idx, rng):
        seen.append((t_within, span_idx, rng))
        return 1.0
    import datetime as _dt
    amc._call_generator_within_span(gen, _dt.datetime(2026, 1, 1), 0, 7.5, 3, "rng-marker")
    assert seen == [(7.5, 3, "rng-marker")]


def test_validate_scenarios_registry_rejects_unhashable_scenario_severity(amc):
    """An unhashable Scenario.severity (e.g., []) must raise the validator's
    ValueError, not a raw TypeError from set-membership lookup."""
    scenario = amc.Scenario(
        id="__t__", name="t", severity=[],  # unhashable
        days_required=1, category="t", components_touched=("apigateway",),
        primary_specs=(("apigateway", {
            "time_offset": 60, "metric": "error_rate",
            "description": "x", "generator": lambda ts, idx: 0.0,
        }),),
        cascade_specs=(),
    )
    original = amc.SCENARIOS.copy()
    amc.SCENARIOS["__t__"] = scenario
    try:
        with pytest.raises(ValueError, match="severity"):
            amc._validate_scenarios_registry()
    finally:
        amc.SCENARIOS.clear()
        amc.SCENARIOS.update(original)


def test_generate_component_requires_ctx(amc, tmp_path):
    """generate_component() must require ctx= so a caller can never
    silently lose ctx.anomalies / ctx.cascading_anomalies into a private
    discarded RunContext."""
    specs = [amc.MetricSpec(name="m0", base=10.0, std=0.0)]
    ts_array, ts_strings = amc._build_timestamp_arrays(5, 1.0)
    out = tmp_path / "ctx_required"
    out.mkdir()
    with pytest.raises(TypeError, match="ctx"):
        amc.generate_component(
            "x", specs, [], base_dir=out, total_seconds=5,
            drop_rate=0.0, interval=1.0,
            ts_array=ts_array, ts_strings=ts_strings,
        )


def test_validate_scenarios_registry_rejects_unhashable_severity_primary(amc):
    """A primary spec with unhashable severity (e.g., []) must raise
    ValueError, not a raw TypeError from set membership lookup."""
    spec = {
        "time_offset": 60, "metric": "error_rate",
        "description": "x", "generator": lambda ts, idx: 0.0,
        "severity": [],  # unhashable
    }
    scenario = amc.Scenario(
        id="__t__", name="t", severity="low", days_required=1,
        category="t", components_touched=("apigateway",),
        primary_specs=(("apigateway", spec),),
        cascade_specs=(),
    )
    original = amc.SCENARIOS.copy()
    amc.SCENARIOS["__t__"] = scenario
    try:
        with pytest.raises(ValueError, match="severity"):
            amc._validate_scenarios_registry()
    finally:
        amc.SCENARIOS.clear()
        amc.SCENARIOS.update(original)


def test_validate_scenarios_registry_rejects_unhashable_severity_cascade(amc):
    """Same protection on cascade severity."""
    cascade = {
        "time_offset": 60, "metric": "error_rate",
        "description": "x", "generator": lambda ts, idx: 0.0,
        "severity": [],  # unhashable
    }
    scenario = amc.Scenario(
        id="__t__", name="t", severity="low", days_required=1,
        category="t", components_touched=("apigateway",),
        primary_specs=(),
        cascade_specs=(("apigateway", cascade),),
    )
    original = amc.SCENARIOS.copy()
    amc.SCENARIOS["__t__"] = scenario
    try:
        with pytest.raises(ValueError, match="severity"):
            amc._validate_scenarios_registry()
    finally:
        amc.SCENARIOS.clear()
        amc.SCENARIOS.update(original)


def test_uninspectable_callable_span_dispatch_skips_intermediate_arities(amc):
    """When inspect.signature() fails, the span dispatcher must attempt only
    the two canonical shapes (5-arg then 2-arg) — never an intermediate
    4- or 3-arg call that could silently misbind t_within/span_idx."""
    import datetime as _dt
    arities_tried = []
    class HiddenSig:
        """Callable whose signature cannot be introspected."""
        def __call__(self, *args):
            arities_tried.append(len(args))
            if len(args) == 5:
                raise TypeError("simulate 5-arg refusal")
            if len(args) == 2:
                return 1.0
            raise TypeError(f"unexpected arity {len(args)}")
        # Hide signature from inspect.signature.
        __signature__ = property(lambda self: (_ for _ in ()).throw(ValueError("hidden")))

    gen = HiddenSig()
    amc._call_generator_within_span(gen, _dt.datetime(2026, 1, 1), 0, 1.0, 0, None)
    # Must have attempted 5 (failed), then 2 (succeeded); never 3 or 4.
    assert arities_tried == [5, 2], (
        f"Uninspectable span dispatch must try only [5, 2]; got {arities_tried}"
    )


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
    """_call_generator_within_span dispatches by inspected callability, not
    by catching TypeError. A 5-arg generator that raises TypeError
    internally must not be retried with fewer args (which would hide the
    error and duplicate any side effects)."""
    calls = []
    def buggy_five_arg(ts, col, t_within, span_idx, rng):
        calls.append((ts, col, t_within, span_idx, rng))
        raise TypeError("internal bug — must not be swallowed")
    import datetime as _dt
    with pytest.raises(TypeError, match="internal bug"):
        amc._call_generator_within_span(buggy_five_arg, _dt.datetime(2026,1,1), 0, 1.0, 0, None)
    assert len(calls) == 1, "Generator must be called exactly once, not retried with shorter forms"


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
        def __call__(self, ts, col, t_within, span_idx, rng):
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

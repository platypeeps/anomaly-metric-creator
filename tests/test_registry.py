import gc
import weakref

import pytest
from conftest import COMPONENT_FIELDS


def test_conftest_registry_matches_amc(amc):
    """Ensure every component in tests/conftest.py exists in the main script."""
    for component in COMPONENT_FIELDS:
        assert component in amc.COMPONENTS, f"Component {component} missing from amc.COMPONENTS"


def test_amc_registry_matches_conftest(amc):
    """Ensure every component in the main script is covered by tests/conftest.py."""
    for component in amc.COMPONENTS:
        assert component in COMPONENT_FIELDS, f"Component {component} missing from COMPONENT_FIELDS in conftest.py"


def test_metric_counts_match(amc):
    """Every component in conftest must have the expected number of metrics."""
    for component, expected_count in COMPONENT_FIELDS.items():
        actual_count = len(amc.COMPONENTS[component])
        assert actual_count == expected_count, (
            f"Component {component} has {actual_count} metrics, expected {expected_count}"
        )


def test_default_metrics_per_component_keys_match_components(amc):
    """``DEFAULT_METRICS_PER_COMPONENT`` keys must mirror ``COMPONENTS`` exactly."""
    assert set(amc.DEFAULT_METRICS_PER_COMPONENT.keys()) == set(amc.COMPONENTS.keys()), (
        "DEFAULT_METRICS_PER_COMPONENT must include exactly the components in COMPONENTS"
    )
    for component, default in amc.DEFAULT_METRICS_PER_COMPONENT.items():
        catalog_size = len(amc.COMPONENTS[component])
        assert 1 <= default <= catalog_size, (
            f"DEFAULT_METRICS_PER_COMPONENT[{component!r}] = {default} is "
            f"outside [1, {catalog_size}]"
        )


def test_extracted_runtime_registries_release_module_callbacks():
    """Runtime seams must not retain isolated legacy module copies."""
    from anomaly_metric_creator import catalog, models_impl

    model_key = "test-model-runtime-release"
    catalog_key = "test-catalog-runtime-release"

    def build_runtime():
        components = {"authservice": []}
        instances = {"authservice": [models_impl.Instance()]}
        defaults = {"authservice": 1}
        keepalive = {}

        def get_components():
            return components

        def get_instances():
            return instances

        def get_defaults():
            return defaults

        def get_max_instances():
            return 1

        keepalive["components"] = get_components
        keepalive["instances"] = get_instances
        keepalive["defaults"] = get_defaults
        keepalive["max_instances"] = get_max_instances
        models_impl._configure_models_runtime(
            get_components=keepalive["components"],
            get_max_instances_per_component=keepalive["max_instances"],
            runtime_key=model_key,
        )
        catalog._configure_catalog_runtime(
            get_components=keepalive["components"],
            get_instances=keepalive["instances"],
            get_default_metrics_per_component=keepalive["defaults"],
            runtime_key=catalog_key,
        )
        return keepalive, [weakref.ref(fn) for fn in keepalive.values()]

    keepalive, callback_refs = build_runtime()
    assert all(ref() is not None for ref in callback_refs)
    assert model_key in models_impl._models_runtimes
    assert catalog_key in catalog._catalog_runtimes

    del keepalive
    for _ in range(3):
        gc.collect()

    assert all(ref() is None for ref in callback_refs)
    assert model_key not in models_impl._models_runtimes
    assert catalog_key not in catalog._catalog_runtimes


def test_scenarios_registry_completeness(amc):
    """Every component must appear in at least one scenario's primary or cascade specs."""
    covered = set()
    for scenario in amc.SCENARIOS.values():
        covered.update(c for c, _ in scenario.primary_specs)
        covered.update(c for c, _ in scenario.cascade_specs)
    missing = set(amc.COMPONENTS.keys()) - covered
    assert not missing, f"Components not covered by any scenario: {missing}"


def test_scenarios_id_matches_key(amc):
    """Every Scenario.id must equal its registry key."""
    for slug, scenario in amc.SCENARIOS.items():
        assert scenario.id == slug, (
            f"SCENARIOS[{slug!r}].id is {scenario.id!r}; must equal the key"
        )


def test_scenarios_severity_valid(amc):
    """Every scenario severity must be low, medium, or high."""
    for slug, scenario in amc.SCENARIOS.items():
        assert scenario.severity in {"low", "medium", "high"}, (
            f"SCENARIOS[{slug!r}].severity {scenario.severity!r} is not valid"
        )


def test_scenarios_days_required_valid(amc):
    """Every scenario days_required must equal the day index (1-based) of the
    smallest time_offset across its primary and cascade specs.

    days_required is the minimum --duration-days at which any of the scenario's
    specs become in range. Setting it too high silently drops in-range specs the
    legacy path would have emitted (with stderr warnings for the out-of-range
    tail). Setting it too low activates the scenario for durations where no spec
    is yet in range, which would produce empty scenario output with no warning.
    Equality is therefore the correct invariant.
    """
    for slug, scenario in amc.SCENARIOS.items():
        assert isinstance(scenario.days_required, int) and scenario.days_required >= 1, (
            f"SCENARIOS[{slug!r}].days_required {scenario.days_required!r} "
            "must be a positive int"
        )
        offsets = [p["time_offset"] for _, p in scenario.primary_specs]
        offsets += [c["time_offset"] for _, c in scenario.cascade_specs]
        if not offsets:
            continue
        min_day_required = min(offsets) // amc.SECONDS_PER_DAY + 1
        assert scenario.days_required == min_day_required, (
            f"SCENARIOS[{slug!r}].days_required={scenario.days_required} does not "
            f"match the day index of its earliest offset ({min_day_required}). "
            "Setting too high silently drops in-range specs; too low activates "
            "the scenario before any spec is in range."
        )


def test_scenarios_components_touched_matches_specs(amc):
    """``components_touched`` must equal the set of components referenced by the
    scenario's primary and cascade specs.

    ``_resolve_scenarios()`` filters scenarios against ``--components`` using
    ``components_touched``. If a referenced component is missing from that tuple,
    users who select only that component will silently lose this scenario.
    Conversely, listing components the scenario does not touch dilutes the
    filter and causes the scenario to fire under irrelevant component
    allowlists.
    """
    for slug, scenario in amc.SCENARIOS.items():
        referenced = {c for c, _ in scenario.primary_specs}
        referenced.update(c for c, _ in scenario.cascade_specs)
        declared = set(scenario.components_touched)
        assert referenced == declared, (
            f"SCENARIOS[{slug!r}].components_touched={sorted(declared)} does not "
            f"equal components referenced by specs={sorted(referenced)}; "
            f"missing={sorted(referenced - declared)} extras={sorted(declared - referenced)}"
        )


def test_scenarios_spec_level_severity_in_vocabulary(amc):
    """Every explicit ``severity`` on a primary or cascade spec dict must be
    ``low`` / ``medium`` / ``high``.

    ``_apply_signal_level_and_count`` reads ``spec.get("severity",
    DEFAULT_SEVERITY)`` per spec, so an unknown value (e.g. ``"meidum"`` typo)
    would not raise — the spec would be silently filtered out at every
    ``--signal-level``. The import-time validator
    ``_validate_scenarios_registry`` rejects unknown spec-level severities for
    the same reason; this test mirrors that invariant for explicit coverage.
    """
    valid = {"low", "medium", "high"}
    explicit_severity_count = 0
    for slug, scenario in amc.SCENARIOS.items():
        for component, spec in scenario.primary_specs:
            if "severity" in spec:
                explicit_severity_count += 1
                assert spec["severity"] in valid, (
                    f"SCENARIOS[{slug!r}].primary_specs entry for "
                    f"{component!r} has severity {spec['severity']!r}; "
                    f"must be one of {sorted(valid)}"
                )
        for target, cascade in scenario.cascade_specs:
            if "severity" in cascade:
                explicit_severity_count += 1
                assert cascade["severity"] in valid, (
                    f"SCENARIOS[{slug!r}].cascade_specs entry targeting "
                    f"{target!r} has severity {cascade['severity']!r}; "
                    f"must be one of {sorted(valid)}"
                )
    # Non-empty guard (pre-PR checklist "Test path determinism"): if the
    # catalog migrated to scenario-level-only severity, the loops above
    # would assert zero times while keeping their green checkmark.
    assert explicit_severity_count > 0, (
        "no primary or cascade spec carries an explicit severity; the "
        "vocabulary assertions above ran zero times"
    )


def test_validate_scenarios_registry_rejects_bad_spec_severity(amc, monkeypatch):
    """The import-time validator must reject typos in spec-level severity.

    Without this guard a misspelling like ``"meidum"`` would import cleanly
    and then silently drop the spec at every ``--signal-level`` because
    ``_apply_signal_level_and_count`` filters with ``spec.get("severity",
    DEFAULT_SEVERITY) in allowed_severities``.
    """
    # Build a minimal valid scenario, then swap in a bad severity on its
    # primary spec and verify the validator raises.
    good_component = next(iter(amc.COMPONENTS.keys()))
    bad_primary_spec = {
        "time_offset": 0,
        "metric": amc.COMPONENTS[good_component][0].name,
        "description": "test-only synthetic spec",
        "generator": lambda ts, idx: 1.0,
        "severity": "meidum",  # intentional typo
    }
    bad_scenario = amc.Scenario(
        id="synthetic_bad_severity",
        name="Synthetic bad-severity scenario",
        severity="medium",
        days_required=1,
        category="same_day",
        components_touched=(good_component,),
        primary_specs=((good_component, bad_primary_spec),),
        cascade_specs=(),
    )
    patched = dict(amc.SCENARIOS)
    patched["synthetic_bad_severity"] = bad_scenario
    monkeypatch.setattr(amc, "SCENARIOS", patched)
    with pytest.raises(ValueError, match="meidum"):
        amc._validate_scenarios_registry()


def test_validate_scenarios_registry_rejects_days_required_too_high(amc, monkeypatch):
    """The import-time validator must reject ``days_required`` set above the
    day index of the earliest spec offset.

    Setting it too high silently drops in-range specs because
    ``_resolve_scenarios`` filters the scenario out at the requested
    ``--duration-days`` before any spec is even considered, with no per-spec
    stderr warning.
    """
    good_component = next(iter(amc.COMPONENTS.keys()))
    primary_spec = {
        "time_offset": 0,  # day index 1
        "metric": amc.COMPONENTS[good_component][0].name,
        "description": "test-only synthetic spec",
        "generator": lambda ts, idx: 1.0,
    }
    bad_scenario = amc.Scenario(
        id="synthetic_days_too_high",
        name="Synthetic days_required-too-high scenario",
        severity="medium",
        days_required=2,  # too high — earliest offset lands on day 1
        category="same_day",
        components_touched=(good_component,),
        primary_specs=((good_component, primary_spec),),
        cascade_specs=(),
    )
    patched = dict(amc.SCENARIOS)
    patched["synthetic_days_too_high"] = bad_scenario
    monkeypatch.setattr(amc, "SCENARIOS", patched)
    with pytest.raises(ValueError, match=r"synthetic_days_too_high.*days_required"):
        amc._validate_scenarios_registry()


def test_validate_scenarios_registry_rejects_days_required_too_low(amc, monkeypatch):
    """The import-time validator must reject ``days_required`` set below the
    day index of the earliest spec offset.

    Setting it too low activates the scenario at durations where no spec is
    yet in range, producing empty scenario output with no warning.
    """
    good_component = next(iter(amc.COMPONENTS.keys()))
    primary_spec = {
        "time_offset": 3 * amc.SECONDS_PER_DAY,  # day index 4
        "metric": amc.COMPONENTS[good_component][0].name,
        "description": "test-only synthetic spec",
        "generator": lambda ts, idx: 1.0,
    }
    bad_scenario = amc.Scenario(
        id="synthetic_days_too_low",
        name="Synthetic days_required-too-low scenario",
        severity="medium",
        days_required=1,  # too low — earliest offset lands on day 4
        category="multi_day",
        components_touched=(good_component,),
        primary_specs=((good_component, primary_spec),),
        cascade_specs=(),
    )
    patched = dict(amc.SCENARIOS)
    patched["synthetic_days_too_low"] = bad_scenario
    monkeypatch.setattr(amc, "SCENARIOS", patched)
    with pytest.raises(ValueError, match=r"synthetic_days_too_low.*days_required"):
        amc._validate_scenarios_registry()


def test_validate_scenarios_registry_rejects_components_touched_missing(amc, monkeypatch):
    """The import-time validator must reject ``components_touched`` that
    under-claims relative to the scenario's primary/cascade specs.

    A missing component silently drops the scenario under a narrow
    ``--components`` allowlist because ``_resolve_scenarios`` short-circuits
    when the touched set is disjoint from the allowlist.
    """
    keys = list(amc.COMPONENTS.keys())
    primary_component = keys[0]
    cascade_target = keys[1]
    primary_spec = {
        "time_offset": 0,
        "metric": amc.COMPONENTS[primary_component][0].name,
        "description": "test-only synthetic spec",
        "generator": lambda ts, idx: 1.0,
    }
    cascade_spec = {
        "time_offset": 60,
        "metric": amc.COMPONENTS[cascade_target][0].name,
        "description": "test-only synthetic cascade",
        "generator": lambda ts, idx: 1.0,
    }
    bad_scenario = amc.Scenario(
        id="synthetic_components_missing",
        name="Synthetic components_touched-missing scenario",
        severity="medium",
        days_required=1,
        category="same_day",
        components_touched=(primary_component,),  # missing cascade_target
        primary_specs=((primary_component, primary_spec),),
        cascade_specs=((cascade_target, cascade_spec),),
    )
    patched = dict(amc.SCENARIOS)
    patched["synthetic_components_missing"] = bad_scenario
    monkeypatch.setattr(amc, "SCENARIOS", patched)
    with pytest.raises(ValueError, match=r"synthetic_components_missing.*components_touched"):
        amc._validate_scenarios_registry()


def test_validate_scenarios_registry_rejects_components_touched_extra(amc, monkeypatch):
    """The import-time validator must reject ``components_touched`` that
    over-claims relative to the scenario's primary/cascade specs.

    An extra component dilutes the ``--components`` filter and causes the
    scenario to fire under allowlists that contain none of its actual
    components, producing no anomalies but counting toward the active pool.
    """
    keys = list(amc.COMPONENTS.keys())
    primary_component = keys[0]
    unused_extra = keys[1]
    primary_spec = {
        "time_offset": 0,
        "metric": amc.COMPONENTS[primary_component][0].name,
        "description": "test-only synthetic spec",
        "generator": lambda ts, idx: 1.0,
    }
    bad_scenario = amc.Scenario(
        id="synthetic_components_extra",
        name="Synthetic components_touched-extra scenario",
        severity="medium",
        days_required=1,
        category="same_day",
        components_touched=(primary_component, unused_extra),  # unused_extra is not referenced
        primary_specs=((primary_component, primary_spec),),
        cascade_specs=(),
    )
    patched = dict(amc.SCENARIOS)
    patched["synthetic_components_extra"] = bad_scenario
    monkeypatch.setattr(amc, "SCENARIOS", patched)
    with pytest.raises(ValueError, match=r"synthetic_components_extra.*components_touched"):
        amc._validate_scenarios_registry()


def test_scenarios_all_components_touched_exist(amc):
    """Every component in components_touched must exist in COMPONENTS."""
    known = set(amc.COMPONENTS.keys())
    for slug, scenario in amc.SCENARIOS.items():
        unknown = set(scenario.components_touched) - known
        assert not unknown, (
            f"SCENARIOS[{slug!r}].components_touched has unknown components: {unknown}"
        )


def test_scenarios_primary_specs_reference_known_components(amc):
    """Every primary_spec component must exist in COMPONENTS."""
    known = set(amc.COMPONENTS.keys())
    for slug, scenario in amc.SCENARIOS.items():
        for component, _ in scenario.primary_specs:
            assert component in known, (
                f"SCENARIOS[{slug!r}].primary_specs references unknown component {component!r}"
            )


def test_scenarios_cascade_specs_reference_known_components(amc):
    """Every cascade_spec target must exist in COMPONENTS."""
    known = set(amc.COMPONENTS.keys())
    for slug, scenario in amc.SCENARIOS.items():
        for target, _ in scenario.cascade_specs:
            assert target in known, (
                f"SCENARIOS[{slug!r}].cascade_specs targets unknown component {target!r}"
            )


def test_scenarios_have_specs(amc):
    """Every scenario must have at least one primary or cascade spec."""
    for slug, scenario in amc.SCENARIOS.items():
        assert scenario.primary_specs or scenario.cascade_specs, (
            f"SCENARIOS[{slug!r}] has neither primary_specs nor cascade_specs"
        )


def test_expected_scenario_slugs_present(amc):
    """All 32 expected scenario slugs are present in the registry."""
    expected = {
        # Same-day medium
        "auth_brute_force", "cache_collapse", "api_cpu_saturation", "db_stall",
        "mq_jam", "lb_flapping", "object_store_5xx", "vectorstore_pressure",
        "scheduler_overflow", "payment_5xx", "idp_jwks_storm", "observability_lag",
        # Low baseline
        "monday_baseline",
        # Multi-day LLM
        "llm_viral_surge_day2", "llm_enterprise_onboarding", "llm_rate_limit_fallout",
        "llm_weekend_batch", "llm_second_viral",
        # High-pressure
        "regional_failover_storm", "cache_db_meltdown", "llm_provider_outage",
        "gateway_ddos", "storage_layer_pressure",
        # High-pressure: sharp-start/end incidents
        "deploy_bad_canary_rollback", "dns_provider_outage",
        "network_partition_az_split",
        # Multi-day cascading
        "cache_leak_restart", "jwks_rotation_chaos", "db_disk_exhaustion",
        # Partial-outage scenarios (Phase 7)
        "auth_pod_failure", "cache_az_isolation",
        # GPU inference serving layer
        "gpu_inference_fragmentation",
    }
    actual = set(amc.SCENARIOS.keys())
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"Expected slugs missing from SCENARIOS: {missing}"
    assert not extra, f"Unexpected slugs in SCENARIOS: {extra}"


# ---------------------------------------------------------------------------
# DERIVATIONS <-> MetricSpec.derivation two-way consistency
# ---------------------------------------------------------------------------


def test_derivations_registry_accepts_current_catalogs(amc):
    """The shipped COMPONENTS/DERIVATIONS pair must pass the two-way
    validation (every declared `derivation` string registered, every
    registered metric declared)."""
    amc._validate_derivations_registry()  # must not raise


def test_derivation_string_without_registry_entry_rejected(amc, monkeypatch):
    """A MetricSpec declaring a `derivation` string with no DERIVATIONS
    entry must fail at import-time validation — previously it surfaced
    only as a runtime KeyError from the strict _RECOMPUTERS lookup at
    ``validate`` subcommand time."""
    import dataclasses
    patched = dict(amc.COMPONENTS)
    specs = list(patched["apigateway"])
    specs[0] = dataclasses.replace(
        specs[0], derivation="synthetic_formula(x)"
    )
    patched["apigateway"] = specs
    monkeypatch.setattr(amc, "COMPONENTS", patched)
    with pytest.raises(ValueError, match="no DERIVATIONS entry"):
        amc._validate_derivations_registry()


def test_registry_entry_without_derivation_string_rejected(amc, monkeypatch):
    """The mirror drift: a DERIVATIONS metric whose MetricSpec declares
    no `derivation` string would be recomputed by the generator but
    never checked by the validate subcommand (schema.json omits the
    derivation)."""
    patched = dict(amc.DERIVATIONS)
    recompute_fn, _metrics = patched["cacheservice"]
    patched["apigateway"] = (recompute_fn, ("requests_per_sec",))
    monkeypatch.setattr(amc, "DERIVATIONS", patched)
    with pytest.raises(ValueError, match="declares no"):
        amc._validate_derivations_registry()

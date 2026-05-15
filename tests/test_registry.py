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
    for slug, scenario in amc.SCENARIOS.items():
        for component, spec in scenario.primary_specs:
            if "severity" in spec:
                assert spec["severity"] in valid, (
                    f"SCENARIOS[{slug!r}].primary_specs entry for "
                    f"{component!r} has severity {spec['severity']!r}; "
                    f"must be one of {sorted(valid)}"
                )
        for target, cascade in scenario.cascade_specs:
            if "severity" in cascade:
                assert cascade["severity"] in valid, (
                    f"SCENARIOS[{slug!r}].cascade_specs entry targeting "
                    f"{target!r} has severity {cascade['severity']!r}; "
                    f"must be one of {sorted(valid)}"
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
    """All 26 expected scenario slugs are present in the registry."""
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
        # Multi-day cascading
        "cache_leak_restart", "jwks_rotation_chaos", "db_disk_exhaustion",
    }
    actual = set(amc.SCENARIOS.keys())
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"Expected slugs missing from SCENARIOS: {missing}"
    assert not extra, f"Unexpected slugs in SCENARIOS: {extra}"

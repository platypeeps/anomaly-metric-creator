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
    """Every scenario days_required must be 1 or 7."""
    for slug, scenario in amc.SCENARIOS.items():
        assert scenario.days_required in {1, 7}, (
            f"SCENARIOS[{slug!r}].days_required {scenario.days_required!r} must be 1 or 7"
        )


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

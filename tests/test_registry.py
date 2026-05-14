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

def test_anom_attributes_exist(amc):
    """Every component in conftest must have a corresponding anoms_* list in amc."""
    for component, (attr, _) in COMPONENT_FIELDS.items():
        assert hasattr(amc, attr), f"amc missing anomaly list attribute {attr} for component {component}"
        assert isinstance(getattr(amc, attr), list), f"amc.{attr} is not a list"

def test_metric_counts_match(amc):
    """Every component in conftest must have the expected number of metrics."""
    for component, (_, expected_count) in COMPONENT_FIELDS.items():
        actual_count = len(amc.COMPONENTS[component])
        assert actual_count == expected_count, (
            f"Component {component} has {actual_count} metrics, expected {expected_count}"
        )

def test_default_metrics_per_component_keys_match_components(amc):
    """``DEFAULT_METRICS_PER_COMPONENT`` keys must mirror ``COMPONENTS`` exactly.

    The script enforces this at import time, but the regression test makes
    drift explicit: anyone adding or removing a component is reminded that
    the default-count registry must be updated in lockstep.
    """
    assert set(amc.DEFAULT_METRICS_PER_COMPONENT.keys()) == set(amc.COMPONENTS.keys()), (
        "DEFAULT_METRICS_PER_COMPONENT must include exactly the components in COMPONENTS"
    )
    for component, default in amc.DEFAULT_METRICS_PER_COMPONENT.items():
        catalog_size = len(amc.COMPONENTS[component])
        assert 1 <= default <= catalog_size, (
            f"DEFAULT_METRICS_PER_COMPONENT[{component!r}] = {default} is "
            f"outside [1, {catalog_size}]"
        )


def test_component_primary_anomalies_keys_match_components(amc):
    """``COMPONENT_PRIMARY_ANOMALIES`` is what ``main()`` uses to build the
    per-component anomaly dict. Its keys must mirror ``COMPONENTS`` exactly,
    and each value must be the actual ``anoms_*`` list module attribute (not
    a copy) so cascade registrations and tests that mutate the source list
    stay coherent."""
    assert set(amc.COMPONENT_PRIMARY_ANOMALIES.keys()) == set(amc.COMPONENTS.keys()), (
        "COMPONENT_PRIMARY_ANOMALIES must include exactly the components in COMPONENTS"
    )
    for component, anoms in amc.COMPONENT_PRIMARY_ANOMALIES.items():
        assert isinstance(anoms, list), (
            f"COMPONENT_PRIMARY_ANOMALIES[{component!r}] must be a list, "
            f"got {type(anoms).__name__}"
        )

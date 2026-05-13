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

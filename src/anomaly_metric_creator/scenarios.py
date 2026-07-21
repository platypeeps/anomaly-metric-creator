"""Scenario registry helpers exposed as a package-level module."""

from __future__ import annotations

from .scenario_builders import Scenario, register_cascade
from .scenario_catalog import SCENARIOS

__all__ = ["SCENARIOS", "Scenario", "register_cascade"]

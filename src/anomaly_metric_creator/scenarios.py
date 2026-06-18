"""Scenario registry helpers exposed as a package-level module."""

from __future__ import annotations

from .legacy import SCENARIOS, Scenario, register_cascade

__all__ = ["SCENARIOS", "Scenario", "register_cascade"]

"""Core data models exposed as a package-level module."""

from __future__ import annotations

from .legacy import Edge, Instance, MetricSpec, RunContext, SaturationParams

__all__ = ["Edge", "Instance", "MetricSpec", "RunContext", "SaturationParams"]

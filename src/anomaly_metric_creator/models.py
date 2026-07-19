"""Core data models exposed as a package-level module."""

from __future__ import annotations

from .legacy import Edge, RunContext, SaturationParams
from .models_impl import Instance, MetricSpec

__all__ = ["Edge", "Instance", "MetricSpec", "RunContext", "SaturationParams"]

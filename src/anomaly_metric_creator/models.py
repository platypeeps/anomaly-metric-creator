"""Core data models exposed as a package-level module."""

from __future__ import annotations

from .legacy import Edge, SaturationParams
from .models_impl import Instance, MetricSpec, RunContext

__all__ = ["Edge", "Instance", "MetricSpec", "RunContext", "SaturationParams"]

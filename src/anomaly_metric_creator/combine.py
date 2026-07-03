"""Combine helpers exposed as a package-level module.

Re-points to ``combine_impl`` (the real home after decomposition step 5)
rather than ``legacy``; ``legacy`` also re-imports these names for its own
surface, so both import paths resolve to the same objects.
"""

from __future__ import annotations

from .combine_impl import combine_logs, combine_logs_unified, discover_components

__all__ = ["combine_logs", "combine_logs_unified", "discover_components"]

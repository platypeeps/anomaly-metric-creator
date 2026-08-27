"""Combine helpers exposed as a package-level module.

Re-points to ``combine_impl`` (the real home after decomposition step 5)
rather than ``legacy``; ``legacy`` also re-imports these names for its own
surface, so both import paths resolve to the same objects.

These exports are a CLI-internal surface, not a supported programmatic
API: a missing per-component CSV raises ``SystemExit`` rather than an
exception the caller can catch, and progress is printed to stdout
unconditionally. See ``.trellis/spec/amc/backend/api-cli-server.md``
§ Library-API Error Posture.
"""

from __future__ import annotations

from .combine_impl import combine_logs, combine_logs_unified, discover_components

__all__ = ["combine_logs", "combine_logs_unified", "discover_components"]

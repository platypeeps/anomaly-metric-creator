"""OTEL streaming helpers exposed as a package-level module.

These exports are a CLI-internal surface, not a supported programmatic
API: they may raise ``SystemExit``, print to stdout, or skip missing
inputs silently. See ``.trellis/spec/amc/backend/api-cli-server.md``
§ Library-API Error Posture.
"""

from __future__ import annotations

from .otel_stream import stream_otel_gauges, stream_otel_signals

__all__ = ["stream_otel_gauges", "stream_otel_signals"]

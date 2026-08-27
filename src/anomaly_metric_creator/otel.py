"""OTEL streaming helpers exposed as a package-level module.

These exports are a CLI-internal surface, not a supported programmatic
API: protobuf mode raises ``SystemExit`` when ``opentelemetry.proto`` is
absent, warnings are printed to stderr rather than raised, and
``stream_otel_gauges`` skips a per-component CSV that is not on disk
without reporting it. See ``.trellis/spec/amc/backend/api-cli-server.md``
§ Library-API Error Posture.
"""

from __future__ import annotations

from .otel_stream import stream_otel_gauges, stream_otel_signals

__all__ = ["stream_otel_gauges", "stream_otel_signals"]

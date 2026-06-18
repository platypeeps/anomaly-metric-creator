"""OTEL streaming helpers exposed as a package-level module."""

from __future__ import annotations

from .legacy import stream_otel_gauges, stream_otel_signals

__all__ = ["stream_otel_gauges", "stream_otel_signals"]

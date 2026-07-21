"""Defaults shared by the generation command and its runtime facade."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .runtime_defaults import SECONDS_PER_DAY
from .scenario_builders import DEFAULT_INTERVAL_SECONDS, DEFAULT_ROW_COUNT

DEFAULT_SEED = 42
DEFAULT_OUTPUT_DIR = Path("iot_logs")
DEFAULT_DROP_RATE = 0.0
DEFAULT_DURATION_DAYS = (
    DEFAULT_ROW_COUNT * DEFAULT_INTERVAL_SECONDS / SECONDS_PER_DAY
)
DEFAULT_OTEL_STREAM_AUTH_SCHEME = "Bearer"
DEFAULT_SIGNAL_LEVEL = "medium"

# Ceiling on emitted metric cells before --allow-huge-output is required.
PREFLIGHT_CELL_CAP = 200_000_000

# Each signal level includes its own severity tier plus every weaker tier.
SIGNAL_LEVELS: dict[str, set[str]] = {
    "low": {"low"},
    "medium": {"low", "medium"},
    "high": {"low", "medium", "high"},
}

# Stable sub-seed keeps anomaly-count sampling independent of generation RNG.
_ANOMALY_COUNT_CAP_SALT = int.from_bytes(
    hashlib.sha256(b"anomaly_count_cap").digest()[:4], "big"
)

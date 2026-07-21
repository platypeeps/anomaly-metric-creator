"""Installed distribution version shared by package and protocol surfaces."""

from __future__ import annotations

from importlib import metadata


_DISTRIBUTION_NAME = "anomaly-metric-creator"


def package_version(*, fallback: str = "0+unknown") -> str:
    """Return the installed AMC version or a caller-owned source-tree fallback."""
    try:
        return metadata.version(_DISTRIBUTION_NAME)
    except metadata.PackageNotFoundError:
        return fallback

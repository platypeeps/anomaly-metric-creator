"""Installable entrypoints for anomaly-metric-creator."""

from __future__ import annotations

from .cli import main
from .version import package_version

__version__ = package_version()

__all__ = ["__version__", "main"]

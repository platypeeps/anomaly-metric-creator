#!/usr/bin/env python3
"""Compatibility shim for the packaged anomaly metric creator CLI."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))

from anomaly_metric_creator import legacy as _legacy

for _name, _value in vars(_legacy).items():
    if not (_name.startswith("__") and _name.endswith("__")):
        globals()[_name] = _value


if __name__ == "__main__":
    _legacy.main()

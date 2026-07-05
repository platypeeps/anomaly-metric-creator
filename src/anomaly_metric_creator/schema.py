"""Schema writer and validator helpers exposed as a package-level module."""

from __future__ import annotations

# Import legacy for its runtime wiring of schema_impl/validate_impl live
# registry callbacks. The public symbols below still come from the focused
# implementation modules, not from the legacy compatibility surface.
from . import legacy as _legacy  # noqa: F401
from .schema_impl import SCHEMA_DOCUMENT_VERSION, write_schema_json
from .validate_impl import validate_output

__all__ = ["SCHEMA_DOCUMENT_VERSION", "validate_output", "write_schema_json"]

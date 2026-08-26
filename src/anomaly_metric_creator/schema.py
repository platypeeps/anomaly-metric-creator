"""Schema writer and validator helpers exposed as a package-level module.

These exports are a CLI-internal surface, not a supported programmatic
API: they may raise ``SystemExit``, print to stdout, or skip missing
inputs silently. See ``.trellis/spec/amc/backend/api-cli-server.md``
§ Library-API Error Posture.
"""

from __future__ import annotations

from importlib import import_module

# Import legacy for its runtime wiring of schema_impl/validate_impl live
# registry callbacks. The public symbols below still come from the focused
# implementation modules, not from the legacy compatibility surface.
import_module(".legacy", __package__)
from .schema_impl import SCHEMA_DOCUMENT_VERSION, write_schema_json
from .validate_impl import validate_output

__all__ = ["SCHEMA_DOCUMENT_VERSION", "validate_output", "write_schema_json"]

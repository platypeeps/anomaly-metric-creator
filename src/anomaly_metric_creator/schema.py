"""Schema writer and validator helpers exposed as a package-level module.

These exports are a CLI-internal surface, not a supported programmatic
API. This facade happens to raise ordinary exceptions rather than exiting
-- ``validate_output`` raises ``ValueError`` for a missing or unreadable
schema -- so it enumerates no process-oriented behavior; that is a fact
about these two functions, not a stronger guarantee than the other
facades give. See ``docs/spec/amc/backend/api-cli-server.md``
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

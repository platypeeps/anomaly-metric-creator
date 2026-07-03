"""Sensitive HTTP-header redaction for the OTEL transport diagnostics.

Extracted verbatim from ``legacy.py`` (decomposition step 1; see
``.trellis/tasks/07-02-legacy-monolith-decomposition/design.md``).
``legacy.py`` re-imports every name so the historic ``legacy.<name>``
surface is unchanged; new code should import from here.

``_masked_headers`` (request-side, dict input) and
``_redact_sensitive_headers`` (response-side, list-of-pairs input) read
from one allowlist so the two paths cannot drift. The redaction runs
*before* any header value reaches the on-disk ``otel-activity.log`` —
an intermediary that echoes ``Set-Cookie`` / ``Authorization`` /
``X-Api-Key`` on a 4xx/5xx must never leak credential material into
transport diagnostics.
"""

from __future__ import annotations

# Canonical lowercased allowlist of HTTP header names whose value must
# never appear in plaintext in the OTEL activity log. The lookup is
# case-insensitive (the matcher lowercases the inbound header name
# before the set lookup), so every wire-format casing —
# ``Authorization``, ``authorization``, ``AUTHORIZATION``,
# ``SeT-cOoKiE`` — collapses to the same entry. Both
# ``_masked_headers`` (request-side, dict input) and
# ``_redact_sensitive_headers`` (response-side, list-of-pairs input)
# read from this set so the two paths cannot drift.
_SENSITIVE_HEADER_NAMES: frozenset[str] = frozenset({
    "authorization",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-api-key",
})

# Subset of ``_SENSITIVE_HEADER_NAMES`` whose values follow
# ``<scheme> <token>``. The scheme prefix is preserved so an operator
# can still distinguish a Bearer challenge from a Basic challenge in
# the log; only the token portion is replaced with ``***``.
_SCHEMED_SENSITIVE_HEADERS: frozenset[str] = frozenset({
    "authorization",
    "proxy-authorization",
})


def _mask_sensitive_value(name: str, value: str) -> str:
    """Replace ``value`` with the redacted form for header ``name``.

    Callers are expected to have already verified ``name.lower()`` is
    in ``_SENSITIVE_HEADER_NAMES``; the schemed-header branch preserves
    the leading scheme token (``Bearer``/``Basic``/etc.) and replaces
    only the credential token.
    """
    if name.lower() in _SCHEMED_SENSITIVE_HEADERS:
        parts = value.split(" ", 1)
        if len(parts) == 2:
            return f"{parts[0]} ***"
        return "***"
    return "***"


def _masked_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of ``headers`` with sensitive request-side values masked.

    The full sensitive set lives in ``_SENSITIVE_HEADER_NAMES``;
    schemed entries (``Authorization``, ``Proxy-Authorization``) keep
    their scheme prefix and replace the credential with ``***``,
    matching the wire-format an operator expects to see in a verbose
    log. The OTEL request path only ever sets Authorization today,
    but covering the broader allowlist keeps the helper symmetric
    with ``_redact_sensitive_headers`` so a future request-side
    cookie or api-key never leaks.
    """
    masked = {}
    for key, value in headers.items():
        if key.lower() in _SENSITIVE_HEADER_NAMES:
            masked[key] = _mask_sensitive_value(key, value)
        else:
            masked[key] = value
    return masked


def _redact_sensitive_headers(
    header_pairs: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Return ``header_pairs`` with sensitive response-side values masked.

    ``_http_error_activity_fields`` calls this on
    ``list(exc.headers.items())`` before serializing the result to
    JSON, so the redaction runs before any string ever reaches the
    activity log. Input casing is preserved on the output header
    name (so a downstream consumer still sees the wire-format name);
    the lookup is case-insensitive via ``name.lower()``. Duplicate
    names (``Set-Cookie`` commonly repeats) are independently
    redacted in order.
    """
    redacted: list[tuple[str, str]] = []
    for name, value in header_pairs:
        if name.lower() in _SENSITIVE_HEADER_NAMES:
            redacted.append((name, _mask_sensitive_value(name, value)))
        else:
            redacted.append((name, value))
    return redacted

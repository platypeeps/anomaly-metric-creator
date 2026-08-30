"""Sensitive HTTP-header redaction for the OTEL transport diagnostics.

Extracted verbatim from ``legacy.py`` (decomposition step 1; see
``docs/work/archive/2026-07/2026-07-02-legacy-monolith-decomposition/design.md``).
``legacy.py`` re-imports every name so the historic ``legacy.<name>``
surface is unchanged; new code should import from here.

The two redaction paths have **deliberately different postures** because
they face different trust origins (task
``07-02-redaction-allowlist-hardening``):

* ``_masked_headers`` (request-side, dict input) redacts headers *this
  process builds* for its own outbound OTEL request. We control that set —
  the only credential we ever attach is ``Authorization`` — so an
  **allowlist-of-sensitive** (``_SENSITIVE_HEADER_NAMES``) is sufficient and
  keeps operational headers (``Content-Type``) legible in a verbose log.
* ``_redact_sensitive_headers`` (response-side, list-of-pairs input) redacts
  headers an **untrusted upstream** echoed back on a 4xx/5xx. Any header
  name could carry a credential (``X-Amz-Security-Token``, ``X-Vault-Token``,
  ``X-Subject-Token``, ``Authentication-Info``, …), so it uses the inverse
  **mask-unless-known-safe** posture: every value is masked except a short
  allowlist of known-safe operational headers (``_SAFE_RESPONSE_HEADER_NAMES``).
  A never-before-seen header defaults to masked. A false-mask only costs a
  diagnostic; a false-pass costs a credential.

Both paths share ``_mask_sensitive_value`` (so the schemed
``Bearer``/``Basic`` prefix is preserved identically) and run *before* any
header value reaches the on-disk ``otel-activity.log``.
"""

from __future__ import annotations

# Canonical lowercased allowlist of request-side header names whose value
# must never appear in plaintext in the OTEL activity log. Used only by the
# request-side ``_masked_headers`` (headers this process builds). The lookup
# is case-insensitive (the matcher lowercases the inbound header name before
# the set lookup), so every wire-format casing — ``Authorization``,
# ``authorization``, ``AUTHORIZATION`` — collapses to the same entry.
_SENSITIVE_HEADER_NAMES: frozenset[str] = frozenset({
    "authorization",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-api-key",
})

# Mask-unless-known-safe allowlist for the RESPONSE-side header dump. An
# untrusted upstream can echo a credential under any header name, so the
# response redactor masks every value whose lowercased name is NOT in this
# set. Kept deliberately short — when in doubt, mask. These are standard,
# non-credential operational headers whose value is useful in a transport
# diagnostic and cannot carry a secret. Note the ``x-*`` namespace is the
# riskiest (``x-amz-security-token``, ``x-vault-token`` live there), so only
# ``x-request-id`` — an unambiguous diagnostic id — is allowlisted from it.
_SAFE_RESPONSE_HEADER_NAMES: frozenset[str] = frozenset({
    "content-type",
    "content-length",
    "content-encoding",
    "content-language",
    "cache-control",
    "date",
    "server",
    "vary",
    "age",
    "retry-after",
    "cf-ray",
    "x-request-id",
})

# Header names whose values follow ``<scheme> <token>``. When such a header
# is masked (by either path), the scheme prefix is preserved so an operator
# can still distinguish a Bearer challenge from a Basic challenge in the log;
# only the token portion is replaced with ``***``.
_SCHEMED_SENSITIVE_HEADERS: frozenset[str] = frozenset({
    "authorization",
    "proxy-authorization",
})


def _mask_sensitive_value(name: str, value: str) -> str:
    """Return the redacted form of header ``value`` for header ``name``.

    Called on any header a caller has decided to mask (a request-side
    sensitive-allowlist match, or a response-side non-safe header). For a
    schemed header (``Authorization``/``Proxy-Authorization``) the leading
    scheme token (``Bearer``/``Basic``/…) is preserved and only the
    credential token is replaced; every other header's value becomes ``***``.
    """
    if name.lower() in _SCHEMED_SENSITIVE_HEADERS:
        parts = value.split(" ", 1)
        if len(parts) == 2:
            return f"{parts[0]} ***"
        return "***"
    return "***"


def _masked_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of ``headers`` with sensitive request-side values masked.

    Request-side posture is **allowlist-of-sensitive**: only names in
    ``_SENSITIVE_HEADER_NAMES`` are masked, because this process builds the
    outbound request and the only credential it attaches is
    ``Authorization`` (schemed → ``Bearer ***``). Operational headers like
    ``Content-Type`` stay legible. This is intentionally the inverse of the
    response-side ``_redact_sensitive_headers`` posture — see the module
    docstring for why the two trust origins warrant different defaults.
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
    """Return ``header_pairs`` with untrusted response-side values masked.

    Response-side posture is **mask-unless-known-safe**: every value is
    masked unless its lowercased name is in ``_SAFE_RESPONSE_HEADER_NAMES``.
    ``_http_error_activity_fields`` calls this on ``list(exc.headers.items())``
    before serializing the result to JSON, so the redaction runs before any
    string reaches the activity log, and a credential an upstream echoes
    under *any* header name — standard or novel — never lands on disk in
    plaintext. Schemed headers keep their ``Bearer``/``Basic`` prefix when
    masked. Input casing is preserved on the output header name; the lookup
    is case-insensitive via ``name.lower()``. Duplicate names (``Set-Cookie``
    commonly repeats) are independently redacted in order.
    """
    redacted: list[tuple[str, str]] = []
    for name, value in header_pairs:
        if name.lower() in _SAFE_RESPONSE_HEADER_NAMES:
            redacted.append((name, value))
        else:
            redacted.append((name, _mask_sensitive_value(name, value)))
    return redacted

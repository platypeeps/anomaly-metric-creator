"""Allowlist tests for ``_redact_sensitive_headers`` and the
``_http_error_activity_fields`` integration introduced by PR #83.

Background: PR #83 widened OTLP HTTP-error diagnostics in
``_http_error_activity_fields`` to dump every response header into the
activity log under the ``response_headers`` field. Cloudflare and other
intermediaries can echo back ``Set-Cookie`` and ``Authorization`` (or
report back which auth header the client sent), so the dump can leak
session cookies or bearer/api-key material into the on-disk log.
``_redact_sensitive_headers`` is the redaction shim that runs before
``json.dumps(header_pairs, ...)`` and masks values for a fixed allowlist
of sensitive header names (case-insensitive).

The canonical sensitive set is::

    Authorization
    Cookie
    Set-Cookie
    Proxy-Authorization
    X-Api-Key

These tests cover every case variant the matcher needs to recognize plus
the round-trip through ``_http_error_activity_fields`` so a future
refactor that breaks the redaction call site fails this suite, not a
production log review.
"""
import http.client
import io
import json
import urllib.error


# ---------------------------------------------------------------------------
# _SENSITIVE_HEADER_NAMES — single source of truth
# ---------------------------------------------------------------------------


def test_sensitive_header_names_is_canonical_lowercase_set(amc):
    """The matcher compares ``name.lower() in _SENSITIVE_HEADER_NAMES``,
    so the set itself must hold lowercased entries — a mixed-case entry
    would silently miss real-world headers."""
    expected = frozenset({
        "authorization",
        "cookie",
        "set-cookie",
        "proxy-authorization",
        "x-api-key",
    })
    assert amc._SENSITIVE_HEADER_NAMES == expected


# ---------------------------------------------------------------------------
# _redact_sensitive_headers — direct unit tests
# ---------------------------------------------------------------------------


def test_redact_sensitive_headers_masks_authorization_preserves_scheme(amc):
    """Authorization carries ``<scheme> <token>``; the scheme is kept for
    log readability (so an operator can see whether a Bearer or Basic
    challenge was sent) but the token is fully replaced. Matches the
    pre-existing ``_masked_headers`` request-header behavior so the two
    paths cannot drift on Authorization rendering."""
    pairs = [("Authorization", "Bearer eyJhbGciOi-secret-jwt")]
    redacted = amc._redact_sensitive_headers(pairs)
    assert redacted == [("Authorization", "Bearer ***")]


def test_redact_sensitive_headers_masks_authorization_no_scheme(amc):
    """A bare Authorization value (no scheme prefix) is masked
    entirely — there is no meaningful prefix to preserve."""
    pairs = [("Authorization", "raw-token-value")]
    redacted = amc._redact_sensitive_headers(pairs)
    assert redacted == [("Authorization", "***")]


def test_redact_sensitive_headers_masks_proxy_authorization_preserves_scheme(amc):
    """Proxy-Authorization parallels Authorization — same scheme rule."""
    pairs = [("Proxy-Authorization", "Basic dXNlcjpwYXNzd29yZA==")]
    redacted = amc._redact_sensitive_headers(pairs)
    assert redacted == [("Proxy-Authorization", "Basic ***")]


def test_redact_sensitive_headers_masks_cookie_entirely(amc):
    """Cookie values carry no scheme — replace the whole value."""
    pairs = [("Cookie", "sessionid=abc123; csrftoken=xyz789")]
    redacted = amc._redact_sensitive_headers(pairs)
    assert redacted == [("Cookie", "***")]


def test_redact_sensitive_headers_masks_set_cookie_entirely(amc):
    """Set-Cookie can echo back from CDNs or auth proxies."""
    pairs = [("Set-Cookie", "AWSALB=secret-id; Path=/; Secure; HttpOnly")]
    redacted = amc._redact_sensitive_headers(pairs)
    assert redacted == [("Set-Cookie", "***")]


def test_redact_sensitive_headers_masks_x_api_key_entirely(amc):
    """X-Api-Key carries a raw API key with no scheme prefix."""
    pairs = [("X-Api-Key", "sk_live_super_secret")]
    redacted = amc._redact_sensitive_headers(pairs)
    assert redacted == [("X-Api-Key", "***")]


def test_redact_sensitive_headers_case_insensitive_matching(amc):
    """Real-world headers arrive in arbitrary case (``Authorization``,
    ``authorization``, ``AUTHORIZATION``, ``SeT-cOoKiE``). The matcher
    lowercases the incoming name before set lookup, so every variant
    is redacted. The header name in the *output* preserves the input
    casing so a downstream consumer still sees the wire-format name."""
    variants = [
        ("AUTHORIZATION", "Bearer token", ("AUTHORIZATION", "Bearer ***")),
        ("authorization", "Bearer token", ("authorization", "Bearer ***")),
        ("AuThOrIzAtIoN", "Bearer token", ("AuThOrIzAtIoN", "Bearer ***")),
        ("COOKIE", "x=y", ("COOKIE", "***")),
        ("cookie", "x=y", ("cookie", "***")),
        ("SET-COOKIE", "x=y; Secure", ("SET-COOKIE", "***")),
        ("set-cookie", "x=y; Secure", ("set-cookie", "***")),
        ("SeT-cOoKiE", "x=y; Secure", ("SeT-cOoKiE", "***")),
        ("PROXY-AUTHORIZATION", "Basic abc", ("PROXY-AUTHORIZATION", "Basic ***")),
        ("proxy-authorization", "Basic abc", ("proxy-authorization", "Basic ***")),
        ("X-API-KEY", "sk_live_x", ("X-API-KEY", "***")),
        ("x-api-key", "sk_live_x", ("x-api-key", "***")),
        ("X-Api-Key", "sk_live_x", ("X-Api-Key", "***")),
    ]
    for name, value, expected in variants:
        redacted = amc._redact_sensitive_headers([(name, value)])
        assert redacted == [expected], (
            f"variant {name!r}={value!r} masked incorrectly: "
            f"got {redacted!r}, expected {[expected]!r}"
        )


def test_redact_sensitive_headers_passes_through_non_sensitive(amc):
    """Headers outside the allowlist are surfaced verbatim — diagnostics
    like CF-Ray, X-Debug-Header, Content-Type, and Server are part of
    the value of the failure log."""
    pairs = [
        ("CF-Ray", "abc123-DFW"),
        ("X-Debug-Header", "visible"),
        ("Content-Type", "application/json"),
        ("Server", "cloudflare"),
        ("X-Request-Id", "req-42"),
    ]
    redacted = amc._redact_sensitive_headers(pairs)
    assert redacted == pairs


def test_redact_sensitive_headers_redacts_each_duplicate_set_cookie(amc):
    """``http.client.HTTPMessage.items()`` yields one entry per
    occurrence of a header — Set-Cookie commonly repeats. Every
    occurrence must be redacted independently; a partial redaction
    would still leak the second cookie."""
    pairs = [
        ("Set-Cookie", "session=abc; Path=/"),
        ("Set-Cookie", "csrftoken=xyz; Path=/; Secure"),
        ("X-Debug-Header", "kept"),
    ]
    redacted = amc._redact_sensitive_headers(pairs)
    assert redacted == [
        ("Set-Cookie", "***"),
        ("Set-Cookie", "***"),
        ("X-Debug-Header", "kept"),
    ]


def test_redact_sensitive_headers_mixed_sensitive_and_diagnostic(amc):
    """A realistic Cloudflare 403 response interleaves sensitive
    (Set-Cookie) and diagnostic (CF-Ray) headers. The redactor must
    preserve order and only touch the sensitive entries."""
    pairs = [
        ("Date", "Mon, 01 Jun 2026 11:00:00 GMT"),
        ("Content-Type", "text/html"),
        ("CF-Ray", "abc-DFW"),
        ("Set-Cookie", "__cf_bm=secret; Path=/; SameSite=None; Secure"),
        ("Server", "cloudflare"),
        ("Authorization", "Bearer echoed-back"),
        ("X-Api-Key", "sk_live_x"),
    ]
    redacted = amc._redact_sensitive_headers(pairs)
    assert redacted == [
        ("Date", "Mon, 01 Jun 2026 11:00:00 GMT"),
        ("Content-Type", "text/html"),
        ("CF-Ray", "abc-DFW"),
        ("Set-Cookie", "***"),
        ("Server", "cloudflare"),
        ("Authorization", "Bearer ***"),
        ("X-Api-Key", "***"),
    ]


def test_redact_sensitive_headers_empty_input(amc):
    """Empty input is empty output; the redactor must not raise."""
    assert amc._redact_sensitive_headers([]) == []


# ---------------------------------------------------------------------------
# _http_error_activity_fields — round-trip integration
# ---------------------------------------------------------------------------


def _build_http_error(status: int, header_lines: list[tuple[str, str]]):
    """Construct a ``urllib.error.HTTPError`` whose ``.headers`` mirrors
    a real response. ``http.client.parse_headers`` does the real-world
    parse (so duplicate Set-Cookie behaves correctly)."""
    raw = "\r\n".join(f"{k}: {v}" for k, v in header_lines) + "\r\n\r\n"
    headers = http.client.parse_headers(io.BufferedReader(io.BytesIO(raw.encode("ascii"))))
    return urllib.error.HTTPError(
        url="http://127.0.0.1/v1/metrics",
        code=status,
        msg="Forbidden",
        hdrs=headers,
        fp=None,
    )


def test_http_error_activity_fields_redacts_response_headers(amc):
    """End-to-end: a 403 response carrying Set-Cookie + Authorization
    + X-Api-Key must produce a ``response_headers`` JSON payload where
    each sensitive value is masked but the diagnostic CF-Ray header
    survives. This is the contract the activity log relies on."""
    exc = _build_http_error(
        403,
        [
            ("CF-Ray", "real-ray-001"),
            ("Set-Cookie", "session=plaintext-cookie; Secure"),
            ("Authorization", "Bearer echoed-token-abc"),
            ("Proxy-Authorization", "Basic dXNlcjpwYXNz"),
            ("Cookie", "csrftoken=plain"),
            ("X-Api-Key", "sk_live_super_secret"),
            ("X-Debug-Header", "kept"),
        ],
    )
    fields = amc._http_error_activity_fields(exc, body=b"{}", content_type="application/json")
    headers = json.loads(fields["response_headers"])
    headers_map = {name: value for name, value in headers}
    assert headers_map["CF-Ray"] == "real-ray-001"
    assert headers_map["X-Debug-Header"] == "kept"
    assert headers_map["Set-Cookie"] == "***"
    assert headers_map["Cookie"] == "***"
    assert headers_map["X-Api-Key"] == "***"
    assert headers_map["Authorization"] == "Bearer ***"
    assert headers_map["Proxy-Authorization"] == "Basic ***"
    # cf_ray field is sourced from the headers separately; verify it
    # still reads the CF-Ray value and is unaffected by redaction.
    assert fields["cf_ray"] == "real-ray-001"


def test_http_error_activity_fields_redacts_case_variant_response_headers(amc):
    """Cloudflare reports headers in canonical title-case
    (``Set-Cookie``), but other intermediaries lowercase them
    (``set-cookie``). Verify the round-trip survives both casings."""
    exc = _build_http_error(
        500,
        [
            ("set-cookie", "x=y; Secure"),
            ("authorization", "Bearer leaked"),
            ("x-api-key", "sk_live_x"),
        ],
    )
    fields = amc._http_error_activity_fields(exc, body=b"", content_type="text/plain")
    headers = json.loads(fields["response_headers"])
    headers_map = {name: value for name, value in headers}
    # http.client.parse_headers preserves the wire-format casing exactly
    # as received, so the lowercase keys are still lowercase here.
    assert headers_map["set-cookie"] == "***"
    assert headers_map["authorization"] == "Bearer ***"
    assert headers_map["x-api-key"] == "***"


def test_http_error_activity_fields_request_body_gated_on_verbose(amc):
    """The raw request body reaches the activity-log fields only under
    ``verbose=True`` — the ``--otel-verbose`` contract. Non-verbose
    error records keep the always-on diagnostics (``response_headers``,
    ``cf_ray``) but never the payload, which for the gauge stream can be
    a multi-thousand-data-point batch re-serialized on every retry."""
    exc = _build_http_error(403, [("CF-Ray", "ray-verbose-001")])
    body = b'{"resourceMetrics": []}'

    fields_quiet = amc._http_error_activity_fields(
        exc, body=body, content_type="application/json"
    )
    assert "request_body" not in fields_quiet
    assert fields_quiet["cf_ray"] == "ray-verbose-001"

    fields_verbose = amc._http_error_activity_fields(
        exc, body=body, content_type="application/json", verbose=True
    )
    assert fields_verbose["request_body"] == '{"resourceMetrics": []}'
    assert fields_verbose["cf_ray"] == "ray-verbose-001"

    # Non-JSON content types never include the body, verbose or not
    # (protobuf payloads are not human-readable in a text log).
    fields_proto = amc._http_error_activity_fields(
        exc, body=b"\x00\x01", content_type="application/x-protobuf", verbose=True
    )
    assert "request_body" not in fields_proto


def test_http_error_activity_fields_non_http_error_returns_empty(amc):
    """Non-HTTPError exceptions short-circuit out of the helper — the
    redaction call site is only reached for HTTPError, so this test
    pins the early-exit contract."""

    class _Other(Exception):
        pass

    assert amc._http_error_activity_fields(_Other("boom"), b"", "application/json") == {}

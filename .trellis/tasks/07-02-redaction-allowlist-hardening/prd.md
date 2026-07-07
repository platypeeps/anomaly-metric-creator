# Harden response-header redaction against nonstandard credential headers

## Audit context

- **Source:** first-time staff-engineer audit, 2026-07-02.
- **Confidence:** CONFIRMED (read end to end).
- **Severity:** MEDIUM — credential material written to disk under a
  misbehaving/compromised upstream. Credential handling is the stated stake.
- **Category:** security / secret handling.

## Goal

Ensure no credential-bearing HTTP response header can land unmasked in
`otel-activity.log`, regardless of the header's name, and correct the docstring
that currently overstates coverage.

## Problem (concrete failure scenario)

> **Location update (2026-07-06):** the decomposition epic moved these
> symbols out of `legacy.py` (steps 1 and 7). Current locations are used
> below; behavior re-verified unchanged on 2026-07-06 — the gap is still
> present.

`_http_error_activity_fields` at
[otel_stream.py:70](src/anomaly_metric_creator/otel_stream.py:70) serializes **every**
response header into the `response_headers` field of `otel-activity.log` on a
4xx/5xx from an OTEL endpoint (serialization at
[otel_stream.py:93](src/anomaly_metric_creator/otel_stream.py:93)). Redaction runs through
`_redact_sensitive_headers` ([redaction.py:83](src/anomaly_metric_creator/redaction.py:83)),
which only masks the five names in `_SENSITIVE_HEADER_NAMES`
([redaction.py:28](src/anomaly_metric_creator/redaction.py:28)):
`authorization`, `cookie`, `set-cookie`, `proxy-authorization`, `x-api-key`
(the non-allowlisted pass-through is the `else` branch at
[redaction.py:101](src/anomaly_metric_creator/redaction.py:101)).

**When** an upstream proxy/gateway echoes a credential in a nonstandard header
— `X-Auth-Token`, `X-Amz-Security-Token`, `X-Session-Id`, `X-Subject-Token`
(Keystone), `X-Vault-Token`, `Authentication-Info` — on an error response,
**the value is written to disk verbatim.** The docstring at
[otel_stream.py:75](src/anomaly_metric_creator/otel_stream.py:75) claims it prevents
credential leakage generally ("never leaks credential material into the
on-disk log"), and [redaction.py:11](src/anomaly_metric_creator/redaction.py:11)
repeats the framing; both only cover the five allowlisted names.

## Requirements

- Flip the redaction posture from **allowlist-of-sensitive** to
  **mask-unless-known-safe** for the response-header dump: mask every header
  value except a small allowlist of known-safe operational headers
  (`content-type`, `content-length`, `date`, `server`, `cf-ray`,
  `x-request-id`, `retry-after`, `cache-control`, `content-encoding`, etc.).
  A never-before-seen header defaults to masked.
- Keep the schemed-header behavior (`_SCHEMED_SENSITIVE_HEADERS`,
  [redaction.py:40](src/anomaly_metric_creator/redaction.py:40)) — preserve the
  `Bearer`/`Basic` scheme prefix on `Authorization`/`Proxy-Authorization`.
- Keep the request-side `_masked_headers`
  ([redaction.py:62](src/anomaly_metric_creator/redaction.py:62)) in lockstep so
  the two paths cannot drift (the file already documents this intent).
- Correct the `_http_error_activity_fields` docstring
  ([otel_stream.py:75](src/anomaly_metric_creator/otel_stream.py:75)) AND the
  `redaction.py` module docstring
  ([redaction.py:11](src/anomaly_metric_creator/redaction.py:11)) to describe
  the actual guarantee.
- Do not change the `--otel-verbose`-gated `request_body` behavior — that is a
  separate, already-correct gate.
- Consider whether the same posture should apply to the server-side request
  logger (`_redact_query`) — note the decision even if out of scope.

## Acceptance criteria

- [ ] A header with a novel name carrying a secret-shaped value is masked in
      `response_headers` (unit test with a synthetic `HTTPError` whose headers
      include e.g. `X-Amz-Security-Token`).
- [ ] Known-safe operational headers still appear unmasked (so the diagnostic
      stays useful) — covered by the same test.
- [ ] Existing redaction round-trip tests
      (`tests/test_redact_sensitive_headers.py`) and the live-HTTP-error tests
      (`test_otel_http_error_activity_log_includes_response_headers` in
      `tests/test_cli.py`, and the gauge variant in `tests/test_otel_gauges.py`)
      pass, updated for the new posture.
- [ ] The `_http_error_activity_fields` docstring matches the implementation.
- [ ] CLAUDE.md's redaction section (the `_SENSITIVE_HEADER_NAMES` paragraph) is
      updated to describe the mask-unless-known-safe posture.

## Notes

- Low effort, contained to `redaction.py` + `otel_stream.py` + their tests
  (`legacy.py` only re-imports these names — no edits there).
- The allowlist-of-safe list should be deliberately short and documented; when
  in doubt, mask. A false-mask costs a diagnostic; a false-pass costs a
  credential.

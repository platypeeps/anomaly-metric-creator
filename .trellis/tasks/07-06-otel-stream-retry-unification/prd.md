# Unify OTEL retry machinery and widen transport exception handling

## Review context

- **Source:** deep-dive generator-code review, 2026-07-06.
- **Confidence:** CONFIRMED.
- **Severity:** MEDIUM — a malformed server response kills the stream (and
  the serve-mode OTEL daemon thread), contradicting the documented
  contract; plus ~80 lines of duplicated transport machinery.
- **Category:** robustness / DRY.

## Goal

One shared HTTP send-with-retry implementation for both OTEL streamers,
with a transport exception guard wide enough that no malformed response
can abort streaming.

## Problem (verified 2026-07-06)

- `stream_otel_signals`
  ([otel_stream.py:207](src/anomaly_metric_creator/otel_stream.py:207)-282)
  and `stream_otel_gauges._flush`
  ([otel_stream.py:402](src/anomaly_metric_creator/otel_stream.py:402)-478)
  duplicate the SEND/OK/RETRY/FAIL activity records, retry counting, and
  the backoff formula `min(2 ** (attempts - 1), 8)` (:264 and :460 — the
  cap `8` is an unnamed magic number). `max_retries=3` is hardcoded at the
  gauge call site ([legacy.py:9136](src/anomaly_metric_creator/legacy.py:9136))
  rather than shared with the signal stream's default.
- Both loops catch only `(urllib.error.URLError, urllib.error.HTTPError)`
  (:236, :432 — redundant: HTTPError ⊂ URLError).
  `http.client.HTTPException` subclasses that are not OSError (e.g.
  `BadStatusLine` from a malformed response) escape, contradicting the
  docstring "Failures are logged to stderr and do not stop generation"
  ([otel_stream.py:127](src/anomaly_metric_creator/otel_stream.py:127)-128).
  Under `amc serve` this kills the `amc-otel-stream` daemon thread with a
  raw traceback.

## Requirements

- Extract one shared send-with-retry helper: single backoff formula with a
  named cap constant, shared `max_retries` default threaded from both call
  sites.
- Catch `(urllib.error.URLError, http.client.HTTPException)` in the
  helper; keep HTTPError-specific diagnostics via isinstance (the
  `_http_error_activity_fields` path is unchanged).
- Preserve the activity-log record shapes byte-for-byte (tests assert on
  them) and the `--otel-verbose` gating.
- No golden-hash impact (transport-only; CSV outputs untouched).

## Acceptance Criteria

- [ ] One retry implementation; both streamers route through it; the
      backoff cap is a named constant.
- [ ] A test injects `http.client.BadStatusLine` and asserts the stream
      records RETRY/FAIL and continues instead of raising.
- [ ] Existing activity-log and redaction tests pass unchanged.
- [ ] All locked golden hashes unchanged.

## Notes

- Coordinate with `07-02-redaction-allowlist-hardening` (same file) to
  avoid overlapping edits — sequence one after the other.

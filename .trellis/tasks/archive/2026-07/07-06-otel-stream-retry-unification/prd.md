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

- [x] One retry implementation; both streamers route through it; the
      backoff cap is a named constant.
- [x] A test injects `http.client.BadStatusLine` and asserts the stream
      records RETRY/FAIL and continues instead of raising.
- [x] Existing activity-log and redaction tests pass unchanged.
- [x] All locked golden hashes unchanged.

## Resolution (2026-07-07)

Extracted `_post_with_retries(req, body, content_type, *, ...)` in
`otel_stream.py`; both `stream_otel_signals` and `stream_otel_gauges` route
their SEND/OK/RETRY/FAIL loop through it. Named constants
`_OTEL_DEFAULT_MAX_RETRIES = 3` (defaulted into both signatures; the gauge
call site's hardcoded `max_retries=3` in `legacy.py` dropped) and
`_OTEL_BACKOFF_MAX_SECONDS = 8` (the previously-magic backoff cap) replace
the duplicated literals.

**Byte-identical records:** the helper emits fields in the exact
`signal, endpoint, *id_fields, attempt, *verbose` order both loops used, so
the activity-log lines and stderr WARNING text are unchanged (verified — the
locked `test_otel_http_error_activity_log_includes_response_headers` and the
gauge variant pass without edits). `id_fields` carries the per-item identity
(`event_ts`/`component`/`metric` for signals; batch fields for gauges).

**Robustness fix:** the catch widened from
`(urllib.error.URLError, urllib.error.HTTPError)` (redundant — HTTPError ⊂
URLError) to `(urllib.error.URLError, http.client.HTTPException)`. A
`BadStatusLine` from a malformed response is not an OSError, so `urllib`'s
handler never wraps it as URLError and it previously escaped `urlopen`,
killing the serve-mode daemon OTEL thread with a bare traceback. New
regression test
`test_otel_stream_survives_malformed_response_bad_status_line` (test_cli.py)
monkeypatches `urlopen` to raise `BadStatusLine` and asserts the stream
returns 0, records RETRY then FAIL (`error_type=BadStatusLine`), and does
not raise.

Transport-only — no generator path; golden hashes unaffected. 124 focused
tests pass; ruff clean; mypy unchanged at the 137 baseline. No CLAUDE.md
change: the retry loop internals were never documented there (its only
retry mention is the unchanged `--otel-verbose` `request_body` gating).

## Notes

- Coordinate with `07-02-redaction-allowlist-hardening` (same file) to
  avoid overlapping edits — sequence one after the other. *(Done: #213
  landed first; this task branched off post-#213 main.)*

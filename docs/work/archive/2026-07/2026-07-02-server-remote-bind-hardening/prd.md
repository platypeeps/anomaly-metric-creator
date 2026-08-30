---
title: Bound server concurrency and rate-limiter memory for remote binds
status: done
created: 2026-07-02
branch: fix/server-remote-bind-hardening
---
# Bound server concurrency and rate-limiter memory for remote binds

## Audit context

- **Source:** first-time staff-engineer audit, 2026-07-02.
- **Confidence:** CONFIRMED (read end to end).
- **Severity:** MEDIUM for non-loopback binds; LOW for loopback workshops.
- **Category:** security / denial-of-service / resource management.
- **Systemic pattern:** "remote-bind hardening is half-built" — the auth *gate*
  is solid, but everything *behind* it assumes one cooperative client. This task
  and the redaction task are the two security items; pair with
  `07-02-security-md-and-threat-model` to decide whether remote is a supported
  posture at all (which scopes how far this hardening must go).

## Goal

Prevent a reachable non-loopback `amc serve` instance from being driven into
thread or memory exhaustion by connection volume or client-address churn.

## Problem (two confirmed sub-issues)

1. **Uncapped worker threads + long SSE holds.** The server is a
   `ThreadingHTTPServer`
   ([server.py:1270](src/anomaly_metric_creator/server.py:1270)) — one worker
   thread per connection, spawned **before** any auth check. SSE handlers
   `_send_debug_events` ([server.py:759](src/anomaly_metric_creator/server.py:759))
   and `_send_log_stream` ([server.py:855](src/anomaly_metric_creator/server.py:855))
   loop up to 300 iterations × 1s, holding a thread ~5 minutes each. There is no
   connection cap and no slow-loris read timeout. **When** a reachable instance
   receives many concurrent connections (or slow-trickle bodies), threads and
   memory grow unbounded.

2. **Rate-limiter client table never evicted.** `_RateLimiter._calls`
   ([server.py:100](src/anomaly_metric_creator/server.py:100)) does
   `setdefault((client, bucket), deque())`
   ([server.py:108](src/anomaly_metric_creator/server.py:108)) and never removes
   a key. The client is the real peer IP (not header-spoofable — good), but on a
   public bind the dict accrues one entry per distinct source IP **forever**.
   Ironically the DoS-hardening feature is itself an unbounded allocation.

## Requirements

- Add a bound on concurrent connections/worker threads (e.g. a
  `ThreadingHTTPServer` subclass with a bounded worker pool or a semaphore
  gating `process_request`, returning 503 when saturated). Pick a sane default
  and make it configurable (`--max-concurrent-requests` or similar).
- Add a total-SSE-connection ceiling and/or a hard wall-clock cap so long-lived
  streams cannot monopolize the pool; return 503 when the SSE ceiling is hit.
- Add a socket read timeout so a slow-loris connection cannot pin a thread
  indefinitely.
- Evict idle rate-limiter buckets: sweep keys whose newest timestamp is older
  than the window on each `check`, or cap the dict with an LRU and document the
  bound. Keep the fixed-window semantics otherwise intact.
- Gate the scope on the trust-boundary decision from
  `07-02-security-md-and-threat-model`: if remote is explicitly *unsupported*,
  a smaller change plus a louder warning may suffice; if supported, implement
  the full set above.

## Acceptance criteria

- [x] A test opens more than the configured concurrent-connection limit and
      confirms excess requests get a fast 503 (or queue-and-serve) rather than
      unbounded thread growth.
- [x] A test confirms the rate-limiter `_calls` map does not grow without bound
      across many distinct simulated client keys (post-sweep size is bounded).
- [x] SSE endpoints remain functionally correct (still deliver generation /
      command-version events and the terminal `shutdown` event) under the new
      ceiling.
- [x] New flags are covered in `tests/test_server.py` and documented in
      `README.md` + the CLAUDE.md server section.
- [x] The startup remote-bind warning
      ([server.py:1281](src/anomaly_metric_creator/server.py:1281)) is reviewed
      against the final posture.

## Notes

- The auth gate itself is correct and constant-time
  ([server.py:608](src/anomaly_metric_creator/server.py:608)) — do not touch it.
- Client identity is already the peer address, not `X-Forwarded-For` — keep it
  that way (spoof-resistant).

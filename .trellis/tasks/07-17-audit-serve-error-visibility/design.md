# Serve error plane observable by default — Design (SD Work Designs, 2026-07-17)

## Overview

Seven ledger items, one theme: in the default posture the server's error
plane has no sink. Unhandled-500 detail is discarded when
`request_logger` is None (server.py:1030-1034, :1541-1543); background
regen/OTEL failures record `str(exc)` only into the eval-hidden
`/v1/state`; PUT/PATCH/DELETE have no catch-all (connection reset, no
record); `/readyz` hardcodes ready; DoS-bound refusals are uncounted; no
boundary anywhere captures a traceback. The refuter downgrade note on
A-071 stands: request-plane *silence* (access-log no-op) is documented
design — this task fixes only the **error** plane.

## Proposal — two PRs

### PR A — error sinks + boundaries (A-071, A-072, A-073, A-076, A-074)

- A-071/A-076: introduce one tiny helper (`server.py`) —
  `_record_server_error(request_logger, *, where, exc, path=None)` —
  that writes the structured error record (now including
  `traceback.format_exc()` tail, capped ~30 lines) when a logger exists,
  and **falls back to one stderr block** when it does not. Every existing
  error-record call site routes through it; client response bodies stay
  the generic `{"error": "internal server error"}` / Status shapes
  (SECURITY.md contract: detail never in the body).
- A-072: the continuous-generation and OTEL background arms
  (server.py:1664-1674, :1704-1710; server_ops.py:3874-3880) call the
  same helper (WARNING + traceback tail to stderr/structured log) in
  addition to their `/v1/state` status fields. `SystemExit` gets its
  code *and* the captured generation stderr summarized, not just "2".
- A-073: wrap `_handle_mutating_method` dispatch in the same
  except-Exception boundary as do_GET/do_POST: Kubernetes `Status` 500
  for API paths, JSON 500 for app paths, routed through the helper.
- A-074: `/readyz` checks two dimensions — artifacts present
  (`anomalies.csv` exists when the run emits it / state rows loaded) and
  generation-thread health (last-pass status not failed) — 503 with
  `{"ready": false, "reason": "<dimension>"}` on failure. Stays
  auth-exempt and eval-open; reasons name dimensions, never scenario
  content (wall-safe by construction — assert no slugs in the response
  in the eval sweep's spirit).

### PR B — counters + join key (A-075, A-077)

- A-075: refusal counters (worker-cap 503, SSE 503, rate-limit 429) as
  thread-safe counters on the bounded-server/limiter objects, surfaced
  into `state.summary()` (`/v1/state.refusals`), plus one stderr line on
  first trip per rate-limit window ("saturation is visible even with no
  logger"). The worker-cap refusal happens pre-handler in
  `process_request` — the counter lives on the server object, which
  `build_state`/serve wiring already reaches.
- A-077: per-request id (`uuid4().hex[:12]`) minted at dispatch entry,
  included in structured request/error records and threaded into
  `CommandTrace` recording as `request_id` so cross-sink reconstruction
  joins on it. Additive field only; trace schema version bump per the
  SQLite store's versioning rules if the column lands in SQLite (decide:
  JSON payload field only — no schema migration — unless search needs
  it; start payload-only).

## Boundaries And Non-Goals

- No access-log-by-default (documented design; refuter-confirmed).
- No client-visible error detail; no new endpoints; no log framework —
  stderr + the existing StructuredRequestLogger only.
- Eval-mode wall untouched; stderr is operator-side.

## Affected Files

`src/anomaly_metric_creator/server.py` (helper, boundaries, readyz,
counters), `src/anomaly_metric_creator/server_ops.py` (background arm),
`src/anomaly_metric_creator/server_mcp.py` (internal-error path uses the
helper's traceback capture), `server_traces.py` (request_id payload
field), tests (`test_server.py` + eval sweep untouched-but-green),
SECURITY.md/README notes, `.trellis/audit/ledger.md` flips.

## Risks And Edge Cases

- stderr fallback must not double-print when a logger exists (helper owns
  the either/or).
- readyz semantics under `--no-generate` + `--emit` variations: "artifacts
  present" must key off what the run *declared*, not a hardcoded
  filename list — read the emit selection the state already carries.
- Counter locks are hot-path adjacent (per-refusal only, not per-request)
  — no measurable cost; say so in the PR.
- Traceback content may embed exception messages; operator-side sinks
  only — note the wall rationale in the helper docstring.

## Validation

- Acceptance tests: forced-500 with default flags → stderr carries the
  detail (capsys/subprocess); kubectl-shaped PATCH against a raising
  handler → 500 Status, no reset; `/readyz` → 503 with reason on empty
  dir under `--no-generate`; refusal counter visible in `/v1/state`
  after an SSE-cap trip.
- `pytest tests/test_server.py tests/test_server_eval_mode.py
  tests/test_server_mcp.py -n 0` + full suite.

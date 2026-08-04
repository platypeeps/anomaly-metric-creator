# Operations, Security, and Logging

## Trace Persistence and Search

Command traces live in a thread-safe in-memory ring buffer by default.
`--persist-command-log` writes JSONL, and `--persist-command-db` enables SQLite
persistence owned by `server_traces.py`. Sources: `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

The SQLite trace store records `COMMAND_TRACE_DB_SCHEMA_VERSION`, stores JSON
payloads plus indexed columns, uses WAL mode and a dedicated SQLite write lock,
reloads recent traces on startup, supports bounded retention, uses FTS5 when
available, and falls back to LIKE search otherwise. Sources: `CLAUDE.md`;
`src/anomaly_metric_creator/server_traces.py`; `tests/test_server.py`.

JSONL append, SQLite insert, and SQLite history replacement are write paths in
the threaded server and must be serialized with the trace-store locks. SQLite
retention is authoritative when enabled, so trace lookup/search/list behavior
must not surface records already trimmed from persisted history. Sources:
`src/anomaly_metric_creator/server_traces.py`; `tests/test_server.py`.

Online debug search and offline bundle search must use shared
`trace_matches_search()` and `unsupported_summary_from_traces()` helpers so
filters and unsupported grouping stay aligned. Sources: `CLAUDE.md`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/trace_bundle.py`; `tests/test_server.py`;
`tests/test_trace_bundle.py`.

Scenario filters must use exact membership semantics across memory, SQLite, and
offline bundle search. Avoid substring matching against serialized scenario
lists; fallback SQL search should match JSON-quoted ids, then defer to shared
in-memory predicates when exactness cannot be guaranteed. Sources:
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/trace_bundle.py`; `tests/test_server.py`;
`tests/test_trace_bundle.py`.

Imported trace bundles and persisted trace data are untrusted input. Decode and
validate the full payload before replacing persisted history, and reject
booleans for integer fields. Sources: `CLAUDE.md`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/trace_bundle.py`; `tests/test_server.py`;
`tests/test_trace_bundle.py`.

## Security Boundary

Loopback binds may run unauthenticated for local workshops. Non-loopback
`--host` values require `--auth-token` unless the operator explicitly passes
`--allow-remote-without-auth`. Sources: `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

When bearer auth is enabled, `/healthz`, `/readyz`, `/`, and `/debug` remain
loadable, but JSON/debug data, command, kubeconfig, and Kubernetes/Helm API
requests require `Authorization: Bearer TOKEN`. `/v1/kubeconfig` embeds the
token for real clients. Sources: `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

Request body caps return JSON `413` for app endpoints and Kubernetes `Status`
objects for Kubernetes API endpoints. Rate limits return JSON `429` for app
endpoints and Kubernetes `Status` with `reason: TooManyRequests` for API
endpoints. Sources: `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

Because the server is a `ThreadingHTTPServer` spawning one worker per
connection, a reachable (especially non-loopback) bind is hardened by three
defaults-on resource bounds, each disablable with `0`: a concurrent
worker-thread cap (`--max-concurrent-requests`, default 64) enforced by a
`BoundedSemaphore` acquired before the worker thread starts (an over-cap
connection gets a raw `503` and is closed, never spawning a thread); a
concurrent-SSE ceiling (`--max-sse-connections`, default 16) gating the two
long-lived streams (`/v1/debug/events`, `/v1/logs/stream`) with a JSON `503`
before any event-stream headers; and a per-request socket timeout
(`--socket-timeout-seconds`, default 30) applied in the handler's `setup()` so
a slow-loris client cannot pin a worker. The rate limiter also sweeps idle
per-client buckets each window so the limiter's own table stays bounded on a
public bind. These bounds harden the surface behind the auth gate but do not
make an unauthenticated remote bind a supported posture — see `SECURITY.md` for
the trust boundary and the remote-bind decision. Sources: `README.md`;
`CLAUDE.md`; `SECURITY.md`; `src/anomaly_metric_creator/server.py`;
`tests/test_server_hardening.py`; `tests/test_server.py`.

`--cors-allow-origin` is the only CORS enablement path. Preflight requests are
answered without bearer auth, and normal responses include access-control
headers only for the configured origin or `*`. Sources: `README.md`;
`CLAUDE.md`; `src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

## Redaction and Structured Logs

Structured request logging is opt-in through `--structured-log` or
`--structured-log-file`; it emits JSONL request summaries and request-handling
exception rows, redacts query secrets, and records bearer auth only as
present/absent. Sources: `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

Never write bearer tokens, auth tokens, passwords, cookies, API keys,
kubeconfig client keys, token-like query parameters, or command secrets to
memory traces, JSONL logs, SQLite rows, OTEL logs, or debug UI payloads.
Sources: `README.md`; `CLAUDE.md`; `src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_redact_sensitive_headers.py`;
`tests/test_server.py`.

`otel-activity.log` is transport diagnostics, not a broad application log. The
main signal stream starts it fresh, the gauge pass appends during the same run,
gauges-only streaming starts it fresh, and verbose request bodies are gated by
`--otel-verbose`. Sources: `CLAUDE.md`; `README.md`;
`src/anomaly_metric_creator/legacy.py`; `tests/conftest.py`;
`tests/test_otel_gauges.py`; `tests/test_redact_sensitive_headers.py`.

## Debug UI

The debug UI is inline HTML/CSS/JS served from `server_debug_ui.py`/`GET
/debug` to avoid a frontend build chain. The static shell may be accessible
without bearer auth, but data requests must attach the token when auth is
enabled. Sources: `CLAUDE.md`; `README.md`;
`src/anomaly_metric_creator/server_debug_ui.py`;
`src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

Debug UI data should come from the same command traces, resource snapshots,
mutation overlay, scenario catalog, and fake Kubernetes object paths that the
command/API surfaces use. Do not create a separate debug-only state model.
Sources: `CLAUDE.md`; `README.md`;
`src/anomaly_metric_creator/server_debug_ui.py`;
`src/anomaly_metric_creator/server_ops.py`;
`src/anomaly_metric_creator/server_mutations.py`; `tests/test_server.py`.

Frequently polled debug endpoints are hot paths. Avoid full anomaly-row copies,
full resource snapshots, repeated static catalog fetches, and per-row clock
reads when a cheaper snapshot, cached static response, or bounded slice would
preserve the same user-visible behavior. Sources:
`src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_debug_ui.py`;
`src/anomaly_metric_creator/server_ops.py`; `tests/test_server.py`.

Unsupported and partial commands should be captured and grouped by normalized
fingerprint so real operator/tool behavior becomes a backlog for future command
renderers. Sources: `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/server_debug_ui.py`; `tests/test_server.py`;
`tests/test_trace_bundle.py`.

## Mutation Overlay Reset

`POST /v1/mutations/reset` (and the debug UI Reset button, which posts to it)
is the quick reset path for an already-running interactive environment. It is
**overlay-scoped**: it calls `state.mutations.reset()` and returns
`{"scope": "mutation-overlay", "mutations": <summary>}`. The `scope` field is
additive — existing callers that read only the `mutations` summary are
unaffected.

Reset **restores to the selected scenario baseline**: workload
scale/restart/delete overlays, deleted pods, created/deleted generic resources,
extra events, and the Helm release overlay. Because the snapshot renderers are
deterministic, every rendered surface (`kubectl get …`, `helm list/history`)
returns byte-identical to its pre-mutation baseline after reset.

Reset intentionally **does not** touch: generated artifacts (they are the
baseline the overlay sits on — regeneration is `--continuous-generate` or a
restart), recorded command traces (debug history and eval-harness scoring
data), or the simulated clock and generation counters. A full environment
reset, trace clearing, or clock rewind would each be a separate explicit
operation with its own consent gates, not a widening of this endpoint.

Byte-equality note for tests: `kubectl get events` (LAST SEEN) and `helm list`
(UPDATED) embed the simulated clock, which advances on every command, so the
reset contract tests pause the clock (`state.clock.pause()`) before capturing
the baseline — with `now()` frozen, only the overlay can move a render. Static
age columns (`7d`, `0s`) are constants and need no freezing. Sources:
`README.md`; `CLAUDE.md`; `src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_mutations.py`;
`tests/test_server_reset.py`; `tests/test_server.py`.

# Bounded Kubernetes watch streams — Design (SD Work Designs, 2026-07-17)

## Overview

Verified state: the API layer already parses query params
(server.py:543–545), objects/lists already carry `resourceVersion`
(`_k8s_list_resource_version`, server_ops.py:5988/6048; per-object :7140),
and command mode already *parses* `--watch`/`-w` as `_BOOL_FLAGS`
(server_ops.py:1100) but renders a one-shot table, silently ignoring the
flag. Real-client `?watch=true` requests today get a plain list response
(kubectl then misbehaves) with no dedicated trace classification.

## Proposal

Two halves, both backed strictly by `resource_snapshot()` +
`SimulationMutations` (no second state model):

### Real-client API watch (the meaningful half)

- On modeled list paths, when `query` has `watch` in (`true`, `1`):
  dispatch to a new `_send_k8s_watch(...)` in `server.py` that:
  - acquires an SSE slot via the existing `_with_sse_slot` gate (watches
    are long-lived streams; the bound must apply — over-cap → the existing
    JSON 503 shape, here a Kubernetes `Status` 503),
  - emits newline-delimited JSON watch events (`{"type": "ADDED",
    "object": …}`) — chunked `application/json`, the K8s watch wire shape —
    one `ADDED` per object from the same `_k8s_objects_for_resource()`
    call the list path uses (overlay-aware by construction),
  - then polls the snapshot every `_WATCH_POLL_SECONDS` (module constant,
    default 2.0, monkeypatchable for tests), diffing by object
    `(uid or name)` → emits `ADDED`/`MODIFIED`/`DELETED` for overlay
    changes,
  - closes cleanly at `min(timeoutSeconds, _WATCH_MAX_SECONDS)` —
    `_WATCH_MAX_SECONDS` default 300, the bound that keeps the stream
    finite even for kubectl's long default timeouts; also exits on the
    server shutdown event (same contract as the SSE streams).
- Scope of "modeled": start with the families `kubectl get --watch` most
  plausibly hits — pods and deployments — sharing one generic
  implementation over `_k8s_objects_for_resource()`; other modeled list
  paths can opt in trivially since the mechanism is generic, but v1
  asserts only these two. Unmodeled resources with `?watch` keep the
  existing unsupported `Status` + `kubernetes-api` trace path.
- Trace classification: supported `kubernetes-api` trace recording
  `watch=true` and the event count on close.

### Command mode (`POST /v1/commands` is one-shot — no stream possible)

- `kubectl get pods --watch` renders the initial table exactly as today,
  appends one stderr-style note line ("watch: live streaming is not
  available over the one-shot command API; fetch /v1/kubeconfig and use
  real kubectl for --watch"), exits 0, and the trace is classified
  **partial** (matched rule notes the ignored flag) so the debug UI
  backlog shows real demand instead of silently swallowing the flag.

## Boundaries And Non-Goals

- No unbounded streams, no server-push architecture change, no
  resourceVersion continuation/`resourceVersion=` resume semantics
  (kubectl re-lists on reconnect; acceptable for a simulator — document
  it), no `watch=true` on *get-single-object* paths.
- The eval-mode wall is untouched: watch serves the same investigation
  surfaces as list; rubric endpoints never reach the watch dispatch.

## Affected Files

- `src/anomaly_metric_creator/server.py` (watch dispatch + stream loop),
- `src/anomaly_metric_creator/server_ops.py` (command-mode partial note +
  classification; possibly a tiny helper exposing per-object identity for
  diffing),
- `tests/test_server.py` (or focused `tests/test_server_watch.py`),
- CLAUDE.md server section (one paragraph), README serve/kubectl notes,
  `.trellis/spec/amc/backend/api-cli-server.md`.

## Risks And Edge Cases

- Worker-thread pinning: the poll loop must respect the handler socket
  `timeout` and the shutdown event; always release the SSE slot in
  `finally` (mirror `_with_sse_slot`'s existing contract).
- Determinism for tests: initial-event set is deterministic; poll-driven
  events depend only on overlay changes the test itself makes; keep
  `timeoutSeconds=1` + patched `_WATCH_POLL_SECONDS=0.05` in tests.
- kubectl compatibility: kubectl sends `watch=true&resourceVersion=N`
  after its initial list — serving the full ADDED replay regardless of
  the passed resourceVersion makes kubectl print duplicates once;
  acceptable simulator behavior, but verify real `kubectl get pods -w`
  output once manually and note the observed shape in the PR.
- Chunked encoding: use the same write/flush pattern as the existing SSE
  streams; a client disconnect must not traceback (catch BrokenPipe like
  the SSE handlers do).

## Validation

- New tests: initial ADDED set equals snapshot; mid-watch scale mutation →
  MODIFIED observed; timeout closes stream; SSE-slot exhaustion → 503;
  unmodeled resource watch → unsupported Status + trace; command-mode
  `--watch` → table + note + partial trace.
- Full suite + one manual real-kubectl smoke (`kubectl get pods -w`
  against a live serve, Ctrl-C).

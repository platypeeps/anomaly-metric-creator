# Operations, Security, and Logging

## Trace Persistence and Search

Command traces live in a thread-safe in-memory ring buffer by default.
`--persist-command-log` writes JSONL, and `--persist-command-db` enables SQLite
persistence owned by `server_traces.py`. Sources: `README.md`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

The SQLite trace store records `COMMAND_TRACE_DB_SCHEMA_VERSION`, stores JSON
payloads plus indexed columns, uses WAL mode and a dedicated SQLite write lock,
reloads recent traces on startup, supports bounded retention, uses FTS5 when
available, and falls back to LIKE search otherwise. Sources:
`src/anomaly_metric_creator/server_traces.py`; `tests/test_server.py`.

JSONL append, SQLite insert, and SQLite history replacement are write paths in
the threaded server and must be serialized with the trace-store locks. SQLite
retention is authoritative when enabled, so trace lookup/search/list behavior
must not surface records already trimmed from persisted history. Sources:
`src/anomaly_metric_creator/server_traces.py`; `tests/test_server.py`.

Online debug search and offline bundle search must use shared
`trace_matches_search()` and `unsupported_summary_from_traces()` helpers so
filters and unsupported grouping stay aligned. Sources:
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
booleans for integer fields. Sources:
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/trace_bundle.py`; `tests/test_server.py`;
`tests/test_trace_bundle.py`.

### The payload shape: `TracePayload` / `TraceListItem`

`CommandTrace.to_dict()` returns a `TracePayload` — a module-level `TypedDict`
in `server_traces.py` — and the listing paths return `TraceListItem`, which
inherits it and adds `version: int`. Both are module-level on purpose: nested
in `CommandTraceStore` they would sit under a class-body name shadow.

The required / `NotRequired` split is derived, not chosen. A key
`CommandTrace.from_dict` *subscripts* (directly or via `_trace_int_field`) is
required — 13 today — because a row missing it already raises `KeyError`. A key
`from_dict` *defaults* (via `payload.get` or `_trace_tuple_field`) is
`NotRequired` — 11 today — because the store persists whole `to_dict` blobs and
a row written by an older build legitimately omits it. Add a `CommandTrace`
field and the `TypedDict` gains a key on the same side as the `from_dict` read
you write for it. Making all keys required would type-assert a shape the reader
is explicitly built to tolerate the absence of.

The split is enforced by behavior, not by a restated list: `tests/test_server.py`
deletes each key from a real `to_dict()` payload and asserts `from_dict` either
survives it (optional) or raises `KeyError` (required). **Do not read the split
from `TracePayload.__optional_keys__`** — the module uses `from __future__
import annotations`, so the class body stores `"NotRequired[...]"` as a string,
the `TypedDict` machinery never sees the qualifier, and every key reports as
required at runtime. mypy is unaffected because it reads the source; runtime
introspection must resolve first, via
`typing.get_type_hints(..., include_extras=True)`.

Two trust tiers, and they are not the same boundary:

- **Imported bundles and any user-authored payload are untrusted** — decode and
  validate the full payload before it replaces persisted history, per the rule
  above.
- **A row this store itself wrote is machine-written and only ever *older***.
  `_row_to_payload` therefore checks only that the decoded value is a JSON
  object — raising `TypeError` if not, without naming a row id, since every
  query feeding it selects `payload_json` alone — and then casts. Per-field
  validation on every row of a listing would be a real cost for no reachable
  failure. Do not "harden" this into full validation without moving the cost
  question with it.

Sources: `src/anomaly_metric_creator/server_traces.py`; `tests/test_server.py`.

### One row writer: `_insert_trace_row`

Both SQLite write paths — `CommandTraceStore._insert_sqlite` (live insert) and
`._replace_sqlite_traces` (bundle import) — go through the single
`_insert_trace_row(conn, trace, payload, *, delete_fts_first)`. Do not
reintroduce a second copy of the `command_traces` INSERT or the
`command_traces_fts` row insert; a schema column added to one copy and missed
in the other breaks insert or import silently, which is what audit A-031
recorded.

Two parameters carry load-bearing contracts:

- **`payload` is passed in, never computed inside the helper.** `_insert_sqlite`
  computes `trace.to_dict()` *outside* its `_locked_conn()`; `_replace_sqlite_traces`
  computes it *inside* the lock, per row. A helper that called `to_dict()` itself
  would pull that serialization under the SQLite lock on every recorded command.
  This is the same off-lock discipline as the JSONL handle (A-041). Pinned by
  `test_command_trace_sqlite_record_serializes_payload_off_the_sqlite_lock`,
  which observes `_sqlite_lock.locked()` from inside `to_dict`.
- **`delete_fts_first` is `True` only for the live-insert path**, which can
  overwrite an existing trace id and must drop that id's stale FTS row first.
  The import path derives the flag from whether its bulk clear of
  `command_traces_fts` actually ran, rather than hard-coding `False`, so the
  flag cannot go stale if that clear is removed. Treat that as defense in depth
  only — the bulk clear is independently required, because it drops FTS rows
  for traces *absent* from the replacement set, which no per-row delete can
  reach. Deleting the clear still fails the import test;
  `test_command_trace_sqlite_per_row_fts_delete_cannot_reach_absent_traces`
  pins the reason directly. Both halves are pinned:
  `test_command_trace_sqlite_record_replaces_rather_than_duplicates_fts_row`
  fails if the per-row delete is dropped, and
  `test_command_trace_sqlite_import_clears_superseded_fts_rows` fails if the
  bulk clear is — verified by mutating each in turn.

Sources: `src/anomaly_metric_creator/server_traces.py`; `tests/test_server.py`;
`.trellis/audit/ledger.md` (A-031).

### Gotcha: two ways a trace-store test can silently prove nothing

> **Warning:** the dedicated `command_traces` columns are effectively
> write-only from a reader's perspective.

Every read path — `_load_sqlite_tail`, `_list_sqlite`, `_get_sqlite`, and
`_search_sqlite` — reconstructs a `CommandTrace` from **`payload_json` alone**.
The other 20 columns exist only to back WHERE clauses and the FTS mirror. So a
test that writes a trace, reloads it, and compares `to_dict()` passes even if
every dedicated column were dropped. **Assert the raw row** with a direct
`SELECT * FROM command_traces WHERE id = ?` when the property under test is
"the columns were written".

> **Warning:** a passing `search()` does not prove an FTS write happened.

`_search_sqlite` catches `sqlite3.OperationalError` and falls back to a LIKE
scan over `command_traces`, so search still returns the row when the FTS mirror
is empty or broken. `test_command_trace_sqlite_search_reports_backend_and_schema`
deliberately accepts either backend. **Query `command_traces_fts` directly** for
FTS assertions, and guard the test on `store._sqlite_fts_enabled` so a build
without FTS5 skips rather than reporting a false pass.

Sources: `src/anomaly_metric_creator/server_traces.py`; `tests/test_server.py`.

## Security Boundary

Loopback binds may run unauthenticated for local workshops. Non-loopback
`--host` values require `--auth-token` unless the operator explicitly passes
`--allow-remote-without-auth`. Sources: `README.md`;
`src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

When bearer auth is enabled, `/healthz`, `/readyz`, `/`, and `/debug` remain
loadable, but JSON/debug data, command, kubeconfig, and Kubernetes/Helm API
requests require `Authorization: Bearer TOKEN`. `/v1/kubeconfig` embeds the
token for real clients. Sources: `README.md`;
`src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

`/readyz` is a two-dimension readiness check (`_readyz_check`): `200 {"ready":
true}` only when every artifact the run declared it would emit (via
`_collect_emitted_filenames`) is on disk AND the continuous-generation thread
has not failed; otherwise `503 {"ready": false, "reason":
"artifacts"|"generation"}`. The reason names only the failing dimension, never
scenario content, so it stays eval-wall-safe (the endpoint is auth-exempt and
eval-open). Sources: `README.md`; `SECURITY.md`;
`src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

Request body caps return JSON `413` for app endpoints and Kubernetes `Status`
objects for Kubernetes API endpoints. Rate limits return JSON `429` for app
endpoints and Kubernetes `Status` with `reason: TooManyRequests` for API
endpoints. Sources: `README.md`;
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
`SECURITY.md`; `src/anomaly_metric_creator/server.py`;
`tests/test_server_hardening.py`; `tests/test_server.py`.

Those DoS-bound refusals are counted so saturation is observable by default
(A-075). `RefusalCounters` (server_ops.py) is a thread-safe tally shared with
`_BoundedThreadingHTTPServer`: the worker-cap `503` (`_refuse_saturated`, which
fires before any handler exists), both SSE-ceiling `503`s (the app streams via
`_with_sse_slot` and the Kubernetes watch path), and the rate-limit `429`
(`_send_rate_limited`) each `record()` their kind (`worker_cap` / `sse` /
`rate_limit`). `SimulationState.summary()` surfaces the running counts as
`refusals` on `/v1/state`, and the first trip of each kind writes one
`[serve-refusal]` stderr line so saturation is visible even without
`--structured-log`. The stderr line is capped one-per-kind-per-process on
purpose: per-window re-logging under a sustained attack would make the refusal
path its own stderr-amplification vector, so the first-trip line announces the
condition and `/v1/state.refusals` carries the live count thereafter. The
counter kinds are fixed strings with no scenario content, so `/v1/state` staying
eval-hidden is a wall property of that endpoint, not of the counts. Sources:
`src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_ops.py`; `tests/test_server.py`;
`tests/test_serve_main_wiring.py`.

`--cors-allow-origin` is the only CORS enablement path. Preflight requests are
answered without bearer auth, and normal responses include access-control
headers only for the configured origin or `*`. Sources: `README.md`;
`src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

A `*` origin requires `--auth-token`; `serve_main` calls `parser.error` on the
combination and `start_test_server` raises `ValueError` for parity (A-019). The
gate sits *after* the `--config` merge in `_parse_serve_args`, so a config file
cannot smuggle the wildcard past it — put any future serve-flag combination gate
in the same place for the same reason. `--allow-remote-without-auth` does not
unlock it: that flag governs the bind host, while the wildcard exposure is a
browser-origin property that applies to loopback binds too. Sources:
`src/anomaly_metric_creator/server.py`; `SECURITY.md`; `tests/test_cli.py`;
`tests/test_serve_main_wiring.py`.

## Redaction and Structured Logs

Structured request logging is opt-in through `--structured-log` or
`--structured-log-file`; it emits JSONL request summaries and request-handling
exception rows, redacts query secrets, and records bearer auth only as
present/absent. Sources: `README.md`;
`src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

Every request carries a `request_id` join key (A-077): `handle_one_request`
mints a `uuid4().hex[:12]` once per request at the single shared dispatch entry,
so it covers `do_GET` / `do_POST` / the mutating methods. It lands in the
structured request and error records (`base_record["request_id"]`) and threads
into every `CommandTrace` recorded while handling the request — `run_command`,
`record_kubernetes_api_call`, and the MCP `_record_mcp_trace` — via the
payload-only `CommandTrace.request_id`. That field rides `to_dict` / `from_dict`
(live API echo, JSONL, export) with no dedicated SQLite column, so no schema
migration is needed; it still round-trips a SQLite restart because the store
persists the whole `to_dict` blob in `payload_json` and reloads via `from_dict`.
A structured request/error record and its trace therefore share one key, making
cross-sink incident reconstruction exact rather than timestamp guesswork.
Sources: `src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/server_mcp.py`; `tests/test_server.py`.

The error plane always has one operator sink, independent of the opt-in access
log. `_record_server_error` / `_emit_error_record` write an error record — type,
message, and a capped (~30-line) `traceback.format_exc()` tail — to the
structured logger when configured, otherwise to a stderr block. Every HTTP 500
boundary (`do_GET` / `do_POST` / `_handle_mutating_method`), the MCP
internal-error path, and the background continuous-generation / OTEL failure
arms route through it (`state.request_logger` carries the sink to the background
threads). Client response bodies stay generic (`{"error": "internal server
error"}` or a Kubernetes `Status`); detail never reaches a client body, and the
operator-side traceback is outside the eval-mode ground-truth wall. Request
(access) logging stays opt-in; only the error arm is always-on. Sources:
`SECURITY.md`; `src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_ops.py`;
`src/anomaly_metric_creator/server_mcp.py`; `tests/test_server.py`;
`tests/test_server_mcp.py`.

Never write bearer tokens, auth tokens, passwords, cookies, API keys,
kubeconfig client keys, token-like query parameters, or command secrets to
memory traces, JSONL logs, SQLite rows, OTEL logs, or debug UI payloads.
Sources: `README.md`; `src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_redact_sensitive_headers.py`;
`tests/test_server.py`.

The two header-redaction shims in `redaction.py` run before the
`otel-activity.log` JSON dump and take **deliberately different postures** for
their two trust origins; the asymmetry is correct threat modeling, not drift.
The **response side** (`_redact_sensitive_headers`, untrusted upstream) is
*mask-unless-known-safe*: every response-header value is masked except the short
`_SAFE_RESPONSE_HEADER_NAMES` allowlist (`content-type`, `content-length`,
`content-encoding`, `content-language`, `cache-control`, `date`, `server`,
`vary`, `age`, `retry-after`, `cf-ray`, `x-request-id`). A never-before-seen
header defaults to masked, so a credential an upstream echoes under a novel name
(`X-Amz-Security-Token`, `X-Vault-Token`, `X-Subject-Token`,
`Authentication-Info`) cannot reach disk; the `x-*` namespace is the riskiest, so
only `x-request-id` is allowlisted from it. The **request side**
(`_masked_headers`, headers this process builds) stays *allowlist-of-sensitive*,
masking only `_SENSITIVE_HEADER_NAMES` (`Authorization`, `Cookie`, `Set-Cookie`,
`Proxy-Authorization`, `X-Api-Key`), because we control the outbound set and
operational headers like `Content-Type` should stay legible. Both paths share
`_mask_sensitive_value`: `Authorization` / `Proxy-Authorization` are in
`_SCHEMED_SENSITIVE_HEADERS`, so the scheme prefix (`Bearer` / `Basic`) is kept
and only the credential becomes `***`; every other masked header has its full
value replaced. Do not "simplify" the response side to the request side's
posture. Sources: `src/anomaly_metric_creator/redaction.py`;
`src/anomaly_metric_creator/otel_stream.py`;
`tests/test_redact_sensitive_headers.py`; `tests/test_cli.py`;
`tests/test_otel_gauges.py`.

`otel-activity.log` is transport diagnostics, not a broad application log. The
main signal stream starts it fresh, the gauge pass appends during the same run,
gauges-only streaming starts it fresh, and verbose request bodies are gated by
`--otel-verbose`. Sources: `README.md`;
`src/anomaly_metric_creator/legacy.py`; `tests/conftest.py`;
`tests/test_otel_gauges.py`; `tests/test_redact_sensitive_headers.py`.

## Debug UI

The debug UI is inline HTML/CSS/JS served from `server_debug_ui.py`/`GET
/debug` to avoid a frontend build chain. The static shell may be accessible
without bearer auth, but data requests must attach the token when auth is
enabled. Sources: `README.md`;
`src/anomaly_metric_creator/server_debug_ui.py`;
`src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

Debug UI data should come from the same command traces, resource snapshots,
mutation overlay, scenario catalog, and fake Kubernetes object paths that the
command/API surfaces use. Do not create a separate debug-only state model.
Sources: `README.md`;
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
renderers. Sources: `README.md`;
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
deterministic in the overlay, every rendered surface (`kubectl get …`,
`helm list/history`) returns to its pre-mutation baseline after reset — and is
byte-identical to that baseline when the only other render input, the simulated
clock, is held constant. Renders that embed `state.clock.now()` (e.g.
`kubectl get events` LAST SEEN, `helm list` UPDATED) still track the advancing
clock in normal interactive use; the byte-equality note below covers how the
contract tests freeze the clock to isolate the overlay.

When `--persist-mutations PATH` is in effect, reset also truncates that file
to the empty envelope. Reset means baseline in memory *and* on disk: leaving
the file populated would resurrect the discarded overlay at the next restart,
which is the one outcome an operator pressing Reset cannot have intended. The
truncation is not a second code path — reset is an ordinary overlay commit,
and every commit writes.

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
`README.md`; `src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_mutations.py`;
`tests/test_server_reset.py`; `tests/test_server.py`;
`tests/test_server_mutation_persistence.py`.

## Mutation Overlay Persistence

`--persist-mutations PATH` (serve-only, default off) gives the mutation
overlay restart continuity through a JSON file. It persists **only the modeled
overlay** — workload scale/restart/delete state, deleted pods, created and
deleted generic resources, extra events, and the Helm release overlay. It is
not a second Kubernetes state model, and it does not persist traces, the
simulated clock, or generation counters; those have their own stores or are
deliberately per-run.

Three properties carry the design:

*Every commit writes.* `SimulationMutations._commit_locked()` bumps `version`
and writes in the same locked block, so there is no flush-on-shutdown
assumption and a `SIGKILL` loses at most the mutation in flight. Writes go
through `_atomic_write_text`, so a concurrent reader or a restart never sees a
torn file. `put_resource` and `delete_resource` write twice per logical
mutation — they commit state under the lock and record their event just
outside it, re-entering the `RLock` — and both writes are atomic, so the
intermediate file is valid state merely missing an event.

*Load refuses rather than half-hydrates.* Corrupt JSON, an unsupported
`schema_version`, a key this build does not declare, or a value whose JSON
type is wrong raises at startup with the file named. A partially restored
overlay would render a snapshot that never existed, which is worse than not
starting. The envelope is `{"schema_version": 1, "mutations": {…}}`; a field
change bumps the version.

The type checks are not decoration: the file is an untrusted read-back
boundary, and every wrong type it can carry is *iterable* or *coercible*,
so the unguarded form would accept the file and quietly change its meaning
rather than fail. A dict where an array belongs (`deleted_pods`,
`extra_events`, a `deleted_resources` value) would be read as its keys and a
string as its characters, so `_require_sequence` names the offending type
instead. `version` goes through `_require_version` rather than `int()`, which
coerces rather than validates — `True` would load as 1 and `3.9` as 3, and
`bool` is an `int` subclass, so the check has to exclude it explicitly.

Arming persistence is itself a write, and it fails for reasons that have
nothing to do with the file's contents — an unwritable directory, a missing
parent, a failed fsync. `_arm_persistence` converts that `OSError` into the
same path-naming `ValueError` at **both** load routes, because `serve_main`
refuses on `ValueError` alone and an escaping `OSError` would reach the
operator as a traceback. The missing-file first run needs this as much as the
hydrated one, and is the likelier operator error of the two:
`--persist-mutations /no/such/dir/mutations.json` reaches it with nothing to
hydrate and fails on the very first write. A write that fails *later*, mid
serve, is deliberately left as the `OSError` it is rather than disguised as a
malformed file.

*Stale components are dropped, not refused.* An entry keyed by a component
this run does not have — the operator narrowed `--components` — is dropped
with a stderr `WARNING` naming it, and the post-drop overlay is written back
so the ghost does not reappear. Keeping it would put the Kubernetes facade out
of parity with the generated data; refusing outright would strand an operator
over a compatible narrowing.

Serialization is driven by an explicit `_PERSISTED_MUTATION_FIELDS` /
`_UNPERSISTED_MUTATION_FIELDS` partition rather than `dataclasses.asdict`
(which cannot serialize the overlay's `RLock`). A new `SimulationMutations`
field that appears in neither set raises at serialization time, so it cannot
be silently omitted from the file.

Point the flag **outside `--output-dir`**: the pre-clean registry does not
know the file, and `amc validate`'s unknown-file check would flag it. Sources:
`README.md`; `src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_ops.py`;
`src/anomaly_metric_creator/server_mutations.py`;
`tests/test_server_mutation_persistence.py`.

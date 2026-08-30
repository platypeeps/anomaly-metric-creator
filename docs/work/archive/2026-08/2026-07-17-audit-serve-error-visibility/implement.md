# Serve error plane observable by default — Implementation Plan

## Execution Order

**PR A (sinks + boundaries):**

1. Branch from `main`. Add `_record_server_error()` (either/or sink,
   traceback tail cap, wall-rationale docstring) + tests for both sink
   modes.
2. Route the existing 500 boundary (do_GET/do_POST) and the MCP
   internal-error path through it (A-071, A-076).
3. Add the `_handle_mutating_method` except-Exception boundary (A-073):
   Status-shaped for API paths, JSON for app paths + test via a
   monkeypatched raising handler.
4. Background arms (A-072): continuous-generation + OTEL threads call the
   helper; SystemExit summarization; test via a failing regen argv.
5. `/readyz` dimensions (A-074): artifact-presence keyed off the declared
   emit selection + generation-thread health; 503 + reason tests
   (`--no-generate` + empty dir; healthy default).
6. Flip A-071/072/073/074/076 → `fixed`; draft PR → checklist → merge.

**PR B (counters + join key):**

7. [x] `RefusalCounters` (server_ops.py) shared with `_BoundedThreadingHTTPServer`;
   worker-cap `503`, both SSE `503`s (`_with_sse_slot` + watch), and rate-limit
   `429` each `record()`; surfaced as `refusals` on `/v1/state`; one-per-kind
   first-trip stderr line; rate-limit + SSE-cap trip tests assert the counter
   (A-075).
8. [x] `request_id` (`uuid4().hex[:12]`) minted in `handle_one_request`; added to
   structured records (`base_record`) and threaded into `run_command` /
   `record_kubernetes_api_call` / MCP `_record_mcp_trace` via payload-only
   `CommandTrace.request_id` (rides `payload_json`, no SQLite column); join-key
   test: one request → matching id in structured record and trace (A-077).
9. [x] Flipped A-075/A-077 → `fixed` in ledger; draft PR → checklist → merge (ship).

## Validation Plan

```bash
.venv/bin/pytest tests/test_server.py tests/test_server_eval_mode.py \
  tests/test_server_mcp.py tests/test_server_ops_fuzz.py -n 0
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
```

Manual: run `amc serve` default-flags, curl a monkeypatch-free forced
error path (delete an artifact mid-run), observe the stderr block.

## Documentation And Spec Updates

- SECURITY.md: error-detail-to-sink posture sentence (bodies unchanged).
- README serve section: readyz semantics + refusal counters in
  `/v1/state`.
- `.trellis/spec/amc/backend/operations-security-logging.md` alignment.

## Review Notes

- The 500-body contract (generic body, detail in sink only) is the
  security-sensitive invariant — reference the existing SECURITY.md
  contract in the PR and show the body diff is empty.
- readyz changes can affect harness scripts gating on it — call out the
  new 503 conditions prominently (that behavior change is the fix).

## Follow-Ups

- Request-id column in SQLite search (only if harness demand appears);
  needs the schema-version migration machinery.

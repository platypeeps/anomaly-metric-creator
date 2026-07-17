# Bounded Kubernetes watch streams — Implementation Plan

## Execution Order

1. Branch from `main`. Read the two existing SSE stream handlers
   (`/v1/debug/events`, `/v1/logs/stream`) and `_with_sse_slot` first —
   the watch loop copies their slot/shutdown/BrokenPipe discipline.
2. Implement `_send_k8s_watch` (generic over
   `_k8s_objects_for_resource()`): initial ADDED replay → poll/diff loop →
   bounded close. Add `_WATCH_POLL_SECONDS` / `_WATCH_MAX_SECONDS`
   module constants.
3. Wire the dispatch: modeled pods + deployments list paths check
   `query["watch"]` before the plain-list branch; unmodeled watch paths
   fall through to the existing unsupported handling.
4. Command mode: in the `kubectl get` renderer, when `--watch`/`-w` is
   set, append the note line and classify the trace `partial` with a
   matched-rule note.
5. Tests (`tests/test_server_watch.py`, using `start_test_server` +
   patched `_WATCH_POLL_SECONDS`): the six cases in design.md Validation.
6. Docs: CLAUDE.md server paragraph, README kubectl notes (including the
   no-resume/resourceVersion caveat), spec file update.
7. Manual smoke with real kubectl; paste the observed `-w` output into
   the PR description.
8. Draft PR → checklist → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_server_watch.py -n 0
.venv/bin/pytest tests/test_server.py tests/test_server_ops_fuzz.py -n 0
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

- CLAUDE.md (server facade section: watch support + bounds), README,
  `.trellis/spec/amc/backend/api-cli-server.md`.

## Review Notes

- Reviewer-sensitive: the SSE-slot accounting (watch must consume a slot
  and always release it) and the shutdown-event exit — both are the
  DoS-bound surface the remote-bind hardening task established.
- The fuzz corpus (`test_server_ops_fuzz.py`) should gain a couple of
  malformed watch shapes (`watch=banana`, watch on a POST) — cheap
  insurance consistent with that file's charter.

## Follow-Ups

- resourceVersion resume semantics — only if a real client workflow needs
  reconnect fidelity; record demand via the partial/unsupported traces
  first.

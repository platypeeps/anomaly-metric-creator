# Test serve_main composition — Design (SD Work Designs, 2026-07-17)

## Overview

Verified state: `serve_main` (server.py:1473) is the only production path
that threads `--mcp-eval-mode` into `build_state` (`eval_mode=
serve_args.mcp_eval_mode`, server.py:1519) and maps the eight serve flags
onto `ServerSecurityConfig` (server.py:1531–1540; fields at :105). Tests
exercise `build_state` and `start_test_server` (:1770) directly, so the
composition body between argument validation and `serve_forever` runs under
zero tests — dropping the default-`False` `eval_mode` kwarg would silently
disable the ground-truth wall.

## Proposal

New focused file `test_serve_main_wiring.py` under `tests/` (small-files rule;
uses the session `amc` fixture). Two tests run the real `serve_main` with patch
points *below* the code under test and no generated-artifact fixture:

1. **Eval-kwarg threading.** Monkeypatch `server.build_state` with a
   wrapper that captures `kwargs` and raises a private `_StopWiring`
   sentinel exception (so nothing past state-construction runs). Call
   `serve_main(["--no-generate", "--port", "0", "--output-dir", d,
   "--mcp-eval-mode"])` inside `pytest.raises(_StopWiring)`; assert
   `captured["eval_mode"] is True`. Control case: same argv without the
   flag → `is False`. This fails if the kwarg is dropped, renamed, or
   mis-threaded — the exact A-020 regression.
2. **Flag → ServerSecurityConfig mapping.** Monkeypatch `build_state` to
   return a minimal state with a real shutdown event, patch the background
   starters to no-ops, and monkeypatch `server._BoundedThreadingHTTPServer`
   with a stub class recording its init args and exposing `server_address`, a
   no-op `serve_forever`, and `server_close`. Monkeypatch `server.make_handler`
   to capture the `security=` kwarg. Run `serve_main` with
   non-default values for **all eight** flags (`--auth-token`,
   `--max-request-body-bytes`, `--allow-remote-without-auth`,
   `--cors-allow-origin`, `--rate-limit-per-minute`,
   `--max-concurrent-requests`, `--max-sse-connections`,
   `--socket-timeout-seconds`) plus `--no-generate --port 0`; assert each
   `ServerSecurityConfig` field equals its flag value, and that the stub
   server received `max_workers`/`max_sse` from the same config.
The optional live HTTP smoke is intentionally omitted: existing eval-mode
tests already pin the hidden endpoint's `404` behavior through the real
handler, while this task closes the distinct parser-to-state composition gap.
Repeating that HTTP contract here would add thread/socket timing without making
the silent `eval_mode` drop or security-field swap more detectable.

## Boundaries And Non-Goals

- No `serve_main` refactor (no seam extraction) — patch-based capture keeps
  the production body byte-identical.
- `_generation_argv_without_otel` is already covered by
  continuous-generation tests (refuter scope note) — do not duplicate.
- Config-file (`--config`) merge order is covered elsewhere; not re-tested.

## Affected Files

- `test_serve_main_wiring.py` (new, under `tests/`),
- `.trellis/audit/ledger.md` (flip A-020 → fixed).

## Risks And Edge Cases

- Test 2's `serve_main` runs to completion (prints the listening banner) —
  capture stdout with `capsys` or ignore; assert no exception.
- Keep every patch inside `monkeypatch`; no real socket, background thread, or
  process-global server state is created.
- `--allow-remote-without-auth` on a loopback bind is harmless (the
  parser gate only fires for non-loopback hosts) — safe to set for the
  mapping test.
- Port 0 remains in the argv to exercise the real parser while the server
  double supplies a deterministic loopback address.

## Validation

- `pytest tests/test_serve_main_wiring.py -n 0` then full suite.
- Mutation check: delete `eval_mode=...` from the `build_state` call
  locally → test 1 must fail; swap two security kwargs → test 2 must fail.

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

New focused file `test_serve_main_wiring.py` under `tests/` (small-files rule; uses
the session `amc` fixture). Three tests, each running the real `serve_main`
with patch points *below* the code under test:

1. **Eval-kwarg threading.** Monkeypatch `server.build_state` with a
   wrapper that captures `kwargs` and raises a private `_StopWiring`
   sentinel exception (so nothing past state-construction runs). Call
   `serve_main(["--no-generate", "--port", "0", "--output-dir", d,
   "--mcp-eval-mode"])` inside `pytest.raises(_StopWiring)`; assert
   `captured["eval_mode"] is True`. Control case: same argv without the
   flag → `is False`. This fails if the kwarg is dropped, renamed, or
   mis-threaded — the exact A-020 regression.
2. **Flag → ServerSecurityConfig mapping.** Let `build_state` run for real
   (needs artifacts — see below). Monkeypatch
   `server._BoundedThreadingHTTPServer` with a stub class recording its
   init args and exposing `server_address` + a no-op `serve_forever`;
   monkeypatch `server.make_handler` with a wrapper that captures the
   `security=` kwarg and returns the real handler. Run `serve_main` with
   non-default values for **all eight** flags (`--auth-token`,
   `--max-request-body-bytes`, `--allow-remote-without-auth`,
   `--cors-allow-origin`, `--rate-limit-per-minute`,
   `--max-concurrent-requests`, `--max-sse-connections`,
   `--socket-timeout-seconds`) plus `--no-generate --port 0`; assert each
   `ServerSecurityConfig` field equals its flag value, and that the stub
   server received `max_workers`/`max_sse` from the same config.
3. **Optional live smoke.** Wrap `_BoundedThreadingHTTPServer.__init__` to
   stash the instance (calling the real init), run `serve_main` with
   `--mcp-eval-mode --no-generate --port 0` in a daemon thread, wait for
   the stashed instance, then over HTTP to `127.0.0.1:<port>`: GET
   `/v1/anomalies` → 404 (rubric hidden), GET `/healthz` → 200; finally
   `httpd.shutdown()` and join the thread — pins bind + wall + clean
   shutdown end-to-end.

Artifacts for tests 2–3: generate once per module into `tmp_path_factory`
via `amc.main(["--output-dir", d, "--interval-seconds", "3600", "--seed",
"7"])` — 24 rows/component, cheap, no session GB fixture needed;
`--no-generate` then points serve_main at it.

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
- Thread hygiene in test 3: always `shutdown()` in a `finally`; give the
  daemon thread a join timeout so a hang fails fast rather than wedging
  xdist.
- `--allow-remote-without-auth` on a loopback bind is harmless (the
  parser gate only fires for non-loopback hosts) — safe to set for the
  mapping test.
- Port 0 gives an OS-assigned port; read it from the stashed instance's
  `server_address`, never hardcode.

## Validation

- `pytest tests/test_serve_main_wiring.py -n 0` then full suite.
- Mutation check: delete `eval_mode=...` from the `build_state` call
  locally → test 1 must fail; swap two security kwargs → test 2 must fail.

---
title: Collapse the inline OTLP capture servers onto one conftest fixture
status: planning
created: 2026-08-06
---
# Collapse the inline OTLP capture servers onto one conftest fixture

## Goal

A-032: a `capture_otlp_server` conftest fixture replacing the inline `_Handler`
scaffolds in `tests/test_cli.py`.

Child 3 of epic `07-17-audit-test-harness-dedupe`, and the largest diff in it.

## Blocked on

`08-06-conftest-helper-consolidation` (child 2). Both tasks add to
`tests/conftest.py`; the epic's `implement.md` fixes the order so this one
rebases onto a settled conftest rather than the reverse. Its acceptance
criteria include clearing this task's `blocked` / `blockedOn` markers, so this
task becomes selectable when that PR merges.

## Measured baseline (main @ `29ee1bf`)

- `tests/test_cli.py` is 1,931 lines and defines **22** `_Handler` classes,
  each with its own `ThreadingHTTPServer` start/stop scaffold (23
  `HTTPServer`/`ThreadingHTTPServer` references in the file).
- Those 22 classes reduce to **11 distinct bodies**, 8–13 lines each. The
  duplication is uneven, which is why a single fixture is not enough on its own:

  | copies | first occurrence | body length |
  | --- | --- | --- |
  | 5 | `test_cli.py:1054` | 10 lines |
  | 3 | `test_cli.py:861` | 9 lines |
  | 3 | `test_cli.py:1095` | 8 lines |
  | 2 | `test_cli.py:703` | 11 lines |
  | 2 | `test_cli.py:743` | 9 lines |
  | 2 | `test_cli.py:970` | 9 lines |
  | 1 each | `:372`, `:660`, `:823`, `:1147`, `:1537` | 9–13 lines |

  10 of the 22 collapse onto the three multi-copy bodies; 5 are genuine
  one-offs.
- The reusable model already exists: `tests/test_otel_gauges.py`'s
  `_MockCollector` + `_start_mock()` / `_stop_mock()` (capture every POST to
  `server.received` as `(path, content_type, raw_body)`, always 200).
- Three other test files also carry a `BaseHTTPRequestHandler`:
  `test_cli_surface.py`, `test_correctness.py`, `test_otel_gauges.py`. Whether
  they migrate is a scope decision — see requirement 5.

## Requirements

1. Add a `capture_otlp_server` fixture to `tests/conftest.py`, modeled on
   `test_otel_gauges._MockCollector` / `_start_mock` / `_stop_mock`: yields a
   started collector with a `received` list of `(path, content_type,
   raw_body)`, a base URL, and teardown that calls `shutdown()`,
   `server_close()`, and joins the thread with a timeout. Binds `127.0.0.1:0`.
2. Migrate `test_cli.py`'s 22 sites onto it **in batches of roughly 5, with the
   file's suite green between batches** — the epic's `implement.md` sets this
   cadence so a behavior fold is caught near where it was introduced.
3. Keep genuinely divergent handlers as explicit variants — fixture parameters
   or small subclasses — not as a fixture that grows a flag per caller. The
   PR body lists each collapsed site and the variant it mapped to, so a
   reviewer can spot a fold mistake without re-deriving the table above.
4. No production change under `src/`. No golden-hash change. Tests that assert
   on captured OTLP payload content keep asserting the same bytes.
5. Decide and state in the PR whether the three other
   `BaseHTTPRequestHandler` files migrate now or stay. Default is **stay**:
   A-032's evidence and fix sketch are scoped to `test_cli.py`, and
   `test_otel_gauges.py` is the donor model, not a duplicate.
6. The fixture must stay xdist-safe: port 0 per test, no module- or
   session-scoped shared server, no state carried between tests. If it needs
   session scope for cost reasons, that is a scope change, not a detail.

## Acceptance criteria

- [ ] `grep -cE '^[[:space:]]*class _Handler' tests/test_cli.py` returns 0.
      (POSIX class, not `\s` — BSD `grep` does not treat `\s` as whitespace.)
- [ ] `grep -c 'HTTPServer' tests/test_cli.py` returns 0 — no start/stop
      scaffold survives in the file.
- [ ] `grep -rn 'def capture_otlp_server' tests/` returns **exactly one
      line**, and that line is in `tests/conftest.py`. Both halves are load
      bearing: confinement to `tests/conftest.py` alone would still pass with
      two definitions inside that file. Pre-change this returns no lines, so
      the criterion is what creates the fixture; a second line anywhere —
      including a second one in `conftest.py` — means a copy survived.
- [ ] The collapsed-site → variant mapping table is in the PR body, covering
      all 22 sites.
- [ ] `.venv/bin/pytest tests/test_cli.py -n 0` passes, and its pass/fail set
      is unchanged from the pre-change run (capture both with `-q` and diff).
- [ ] `.venv/bin/pytest` full suite green; `.venv/bin/pre-commit run --all-files` clean.
- [ ] `git diff --stat src/` is empty.
- [ ] A-032 reads `status: fixed` in `.trellis/audit/ledger.md` in this PR
      (epic convention), with a `last-seen` bump.
- [ ] Epic `07-17-audit-test-harness-dedupe` can close: A-031, A-032, A-033,
      and A-037 all read `status: fixed`.

## Notes

- CLAUDE.md's test-hygiene guidance should name `capture_otlp_server` as the
  canonical OTLP capture harness once it exists — the epic's `implement.md`
  lists this under documentation updates. That bullet and the matching one for
  `run_tool` can land together in whichever of the two PRs is second.
- ~500 lines of boilerplate is the ledger's estimate of the win; the measured
  22 classes × 8–13 lines plus scaffolds is consistent with it.

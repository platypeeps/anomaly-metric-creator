# Missing test-guard lints and sync checks — Design (SD Work Designs, 2026-07-17)

## Overview

Four recurrence classes still rely on reviewer attention despite the
repo's lints-over-prose policy (CLAUDE.md:2061 names test-resource-cost
as the class that recurred *after* being documented). This task builds
the mechanical guards: a resource-cost lint, a README↔SCENARIOS sync
test, heavy-registry validation, debug-UI JS syntax checking, and a
deterministic pacing test.

## Proposal

- **A-058 — `tools/check_test_resource_cost.py`:** parse Python ASTs and flag,
  in `tests/` only, executable `.read_bytes()`, `.readlines()`, and
  `.read_text().splitlines()` calls. AST matching prevents examples in strings
  or comments from tripping the guard and closes multiline-call bypasses.
  Escape hatch: trailing `# resource-lint: allow` via the role-name lint's
  exact `rstrip().endswith(...)` semantics on the call's source span. Accept
  explicit Python files or directories (recursive `*.py` discovery), aggregate
  violations, and use the 0/1/2 contract with missing/unreadable/syntax-invalid
  input as structural exit 2. Current-main triage found 46 calls across nine
  files: rewrite the two unsafe patterns called out by the audit
  (`test_instances.py` byte equality via `sha256_path` and
  `test_combine.py` row counting via streaming iteration); mark intentional
  small control/log/schema reads explicitly. Wire the guard into
  `.pre-commit-config.yaml` and the already-shipped always-run CI changes job,
  then pin both anchors in `check_ci_review_contract.py` with mutation tests.
- **A-059 — README scenario-table sync test:** parse the
  `### Scenario catalog` markdown table by named headers; normalize backticks
  and bold severity, and compare slug, signal, days, and component sets against
  `amc.SCENARIOS`. Assert registry and table slugs are bidirectionally equal and
  both sides are non-empty (vacuous-pass rule).
- **A-023 — heavy-registry resolution:** a test resolving every name in
  `_HEAVY_SESSION_FIXTURES` through pytest's fixture manager
  (`request.session._fixturemanager._arg2fixturedefs`) — a renamed
  fixture fails loudly instead of silently routing GB fixtures into the
  parallel lane.
- **A-024 — debug-UI JS check:** extract the `<script>` body from
  `server_debug_ui.py`'s HTML (string slice between markers), write to
  tmp, `node --check`; `pytest.mark.skipif(shutil.which("node") is
  None)`. Syntax-only by design (no DOM execution) — record that scope
  limit in the test docstring.
- **A-025 — pacing determinism:** replace the wall-clock window
  assertion (test_otel_gauges.py:902-913) with monkeypatched
  `time.sleep` capture; assert the *requested* sleep durations sum to
  the expected pacing. Removes the loaded-runner flake class.

## Boundaries And Non-Goals

- No new CI lanes (the existing changes job owns the sub-second guard); no debug-UI JS
  *execution* harness (syntax check only); no lint for non-test trees.

## Affected Files

New `tools/check_test_resource_cost.py` + its test file;
focused scenario sync and debug-UI syntax test files;
`tests/test_heavy_marker.py`; `tests/test_otel_gauges.py` (pacing rewrite);
`.pre-commit-config.yaml`; `.github/workflows/ci.yml`; CI contract guard/tests;
triaged test sites; CLAUDE.md and the testing-quality spec;
`.trellis/audit/ledger.md` flips (A-058/059/023/024/025).

## Risks And Edge Cases

- The AST matcher must not flag `f.read(1 << 20)` chunked loops, examples in
  strings/comments, or standalone `read_text()` calls; acceptance tests pin
  those directions plus multiline calls and exemption placement.
- README table parsing must tolerate cosmetic column spacing (split on
  `|`, strip) but stay anchored on the section heading — a moved
  heading should fail loudly, not skip silently (assert the section is
  found before parsing; non-empty guard covers it).
- A-025 must keep at least one real-transport assertion (the HTTP
  round-trips still happen; only timing moves to the patch) so the test
  still proves streaming works.

## Validation

- Lint mutation checks: a `read_bytes` in a scratch test file → exit 1;
  `allow`-marked → 0; missing path → 2.
- Sync-test mutation: comment one SCENARIOS entry's README row →
  failure naming the slug.
- `pytest tests/test_test_resource_cost_lint.py
  tests/test_readme_scenario_catalog_sync.py tests/test_debug_ui_javascript.py
  tests/test_heavy_marker.py tests/test_otel_gauges.py -n 0` + CI contract
  tests + full suite + pre-commit.

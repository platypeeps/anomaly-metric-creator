# Missing test-guard lints and sync checks — Design (SD Work Designs, 2026-07-17)

## Overview

Four recurrence classes still rely on reviewer attention despite the
repo's lints-over-prose policy (CLAUDE.md:2061 names test-resource-cost
as the class that recurred *after* being documented). This task builds
the mechanical guards: a resource-cost lint, a README↔SCENARIOS sync
test, heavy-registry validation, debug-UI JS syntax checking, and a
deterministic pacing test.

## Proposal

- **A-058 — `tools/check_test_resource_cost.py`:** flags, in `tests/`
  only: `.read_bytes()`, `.readlines()`, `.read_text().splitlines()`
  (line-based regex on source lines; anchored patterns, not bare
  substrings). Escape hatch: trailing `# resource-lint: allow` via the
  role-name lint's exact `rstrip().endswith(...)` semantics. Exit
  contract 0/1/2 with wrapped IO (`path.exists()` before read → 2 on
  structural failure). Acceptance tests file
  (`tests/test_test_resource_cost_lint.py`) mirroring the sibling lint
  tests. Triage pass: fix or `allow`-annotate the two known in-tree
  sites (test_instances_per_component.py:144, test_combine.py:384) in
  the same PR. Wire: `.pre-commit-config.yaml` (tests/ file filter) —
  CI mirroring rides `07-17-audit-ci-cadence-closures` A-060's guards
  step; if that lands first add this lint there too, else leave a note
  in its PRD (avoid cross-PR collision on the same workflow lines).
- **A-059 — README scenario-table sync test:** parse the
  `## Scenario catalog` markdown table (slug/severity/days/description
  columns per its header); parametrize over `amc.SCENARIOS`; assert each
  scenario has a row whose severity + `days_required` match; assert
  no table row lacks a registry entry (bidirectional). Non-empty guards
  on both sides (vacuous-pass rule).
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

- No new CI lanes (ci-cadence owns workflow edits); no debug-UI JS
  *execution* harness (syntax check only); no lint for non-test trees.

## Affected Files

New `tools/check_test_resource_cost.py` + its test file;
`tests/conftest.py`-adjacent new tests (scenario sync, heavy registry);
`tests/test_server.py` or a focused file (JS check);
`tests/test_otel_gauges.py` (pacing rewrite); `.pre-commit-config.yaml`;
two triaged test sites; CLAUDE.md lints section;
`.trellis/audit/ledger.md` flips (A-058/059/023/024/025).

## Risks And Edge Cases

- The resource-cost regexes must not flag `f.read(1 << 20)` chunked
  loops (the sanctioned pattern) — patterns target the three exact
  method spellings; acceptance tests pin both directions.
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
- `pytest tests/test_test_resource_cost_lint.py tests/test_heavy_marker.py
  tests/test_otel_gauges.py -n 0` + full suite + pre-commit.

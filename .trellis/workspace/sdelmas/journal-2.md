# Journal - sdelmas (Part 2)

> Continuation from `journal-1.md` (archived at ~2000 lines)
> Started: 2026-07-27

---



## Session 48: Complete SD command-pack refresh PR 306

**Date**: 2026-07-27
**Task**: Complete SD command-pack refresh PR 306
**Package**: amc
**Branch**: `automation/sd-ai-command-pack-sync`

### Summary

Completed the guarded command-pack refresh lifecycle for merged PR #306 at the actual GitHub squash commit 4f12c2a.

### Main Changes

- Merged and released sd-ai-command-pack 0.55.5, then refreshed the consumer installation and provenance.
- Added the shareable Claude integration while preserving local-only settings, documented fail-open hook behavior, and resolved every review thread.
- Archived the task against the published squash commit so the linear follow-up can carry canonical lifecycle evidence.


### Git Commits

| Hash | Message |
|------|---------|
| `4f12c2a` | chore: refresh sd-ai-command-pack (#306) |

### Testing

- [OK] Pack install audit: 174 managed targets current at version 0.55.5 with zero pending changes.
- [OK] sd-check: 7 of 7 checks passed at the reviewed PR #306 head.
- [OK] Hook exception lint: 14 tests passed; all four hooks compiled and passed targeted exception checks.
- [OK] GitHub CI aggregate and CodeQL passed before PR #306 merged.

### Status

[OK] **Completed**

### Next Steps

- Publish the linear follow-up PR carrying lifecycle bookkeeping and CI cache hardening.


## Session 49: Replace Ruff Dependabot PR 300

**Date**: 2026-07-27
**Task**: Replace Ruff Dependabot PR 300
**Package**: amc
**Branch**: `codex/ruff-0-16-lockstep`

### Summary

Replaced the failing one-sided Dependabot Ruff update with a validated lockstep Ruff 0.16.0 pull request.

### Main Changes

- Updated the Ruff dev pin and ruff-pre-commit revision to 0.16.0 together.
- Regenerated uv.lock with only Ruff package and platform artifact movement.
- Created and reviewed PR 310, refreshed the generated repository map and Obsidian KB, and preserved PR 300 until the replacement merges.


### Git Commits

| Hash | Message |
|------|---------|
| `c938266` | chore(deps): update Ruff to 0.16.0 |
| `89a0d8a` | docs: refresh repository map |

### Testing

- [OK] Ruff lockstep checker, 7 acceptance tests, F401, F841, and both Ruff pre-commit hooks passed.
- [OK] Deterministic full-check passed with Prism disabled and Gito skipped.
- [OK] Full pytest suite passed: 1723 passed and 2 opt-in real-client smoke tests skipped.
- [OK] PR 310 exact-head Copilot review produced no comments; CI, CodeQL, coverage, and aggregate gates passed.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 50: Refresh sd-ai-command-pack to 0.64.3

**Date**: 2026-08-03
**Task**: Refresh sd-ai-command-pack to 0.64.3
**Package**: amc
**Branch**: `refresh-sd-ai-command-pack-0.64.3`

### Summary

Installer-managed refresh of vendored sd-ai-command-pack 0.64.0 to 0.64.3 (TOCTOU helper-loader hardening); install audit passed, 4 platforms.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `b415870246b834bf6246cf7a049e7ea290fa35e2` | chore: refresh sd-ai-command-pack to 0.64.3 |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 51: Quick simulator environment reset scope field

**Date**: 2026-08-03
**Task**: Quick simulator environment reset scope field
**Package**: amc
**Branch**: `feat/quick-simulator-environment-reset`

### Summary

Added an additive scope field to POST /v1/mutations/reset and a contract test module pinning the overlay-only reset semantics.

### Main Changes

- server.py: /v1/mutations/reset returns {"scope": "mutation-overlay", "mutations": <summary>} (additive)
- tests/test_server_reset.py: 9 contract tests — per-family + combined byte-equal-baseline (clock paused), not-reset invariants, endpoint scope field, concurrent-poll safety with thread-termination assert
- README + operations-security-logging.md: documented overlay-only reset scope, curl one-liner, does/does-not list; qualified byte-equality to clock-held-constant


### Git Commits

| Hash | Message |
|------|---------|
| `771ead3` | feat(server): report mutation-overlay scope on reset + contract tests |
| `39fcbc1` | test(server): assert reset poller thread terminates; qualify byte-equality spec |

### Testing

- [OK] .venv/bin/pytest tests/test_server_reset.py -> 9 passed
- [OK] PR #319 CI Result SUCCESS, mergeState CLEAN, review threads resolved

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 52: Bounded Kubernetes watch streams (server-watch-semantics)

**Date**: 2026-08-04
**Task**: Bounded Kubernetes watch streams (server-watch-semantics)
**Package**: amc
**Branch**: `feat/server-watch-semantics`

### Summary

Implemented bounded Kubernetes watch semantics for amc serve: real-client GET ?watch=true streams NDJSON ADDED/MODIFIED/DELETED events for pods and apps/v1 deployments (bounded by timeout/300s ceiling/shutdown, one SSE slot, one kubernetes-api trace), and command-mode kubectl get --watch renders the one-shot table plus a note and is classified partial. Backed by the same resource_snapshot()/SimulationMutations surface as the list path. Shipped as PR #320.

### Main Changes

- Real-client watch dispatch (server._send_k8s_watch/_stream_k8s_watch) streaming newline-delimited watch events with bounded lifetime and one SSE slot
- server_ops watch helpers (_WATCHABLE_LIST_RESOURCES, k8s_watch_plan/objects/object_key/trace_response) reusing the list path's snapshot->namespace->selector chain
- Command-mode _render_get_watch: one-shot table + real-kubectl note, classified partial under kubectl.get.<kind>.watch
- Review fixes: dict-equality object diff (was json.dumps), de-flaked sleep-based test sync, corrected a comment and a docstring per Copilot


### Git Commits

| Hash | Message |
|------|---------|
| `a2ddbaf` | feat: support bounded Kubernetes watch streams |
| `4dc29df` | perf: compare watch objects by dict equality, not JSON round-trip |
| `59e7a8e` | test: de-flake watch tests and fix a misleading fuzz comment |
| `70dbfc9` | docs: address Copilot review comments on watch comments/docstring |

### Testing

- [OK] tests/test_server_watch.py + tests/test_server_ops_fuzz.py: 13 passed
- [OK] full suite: 1746 passed, 2 skipped
- [OK] deterministic sd-check gate: 7/7 passed; ruff + mypy gate clean

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 53: Extract server_ops_profiles.py (epic step 1)

**Date**: 2026-08-04
**Task**: Extract server_ops_profiles.py (epic step 1)
**Package**: amc
**Branch**: `sdelmas/extract-server-ops-profiles`

### Summary

Moved the ops scenario-profile registry, its OpsComponentImpact/OpsScenarioProfile dataclasses, the _impact/_profile builders, and validate_ops_profiles verbatim out of the 7.7k-line server_ops.py into a new pure-data leaf server_ops_profiles.py, re-imported at the original block position. Import-only refactor with object identity preserved across leaf/server_ops/server, so output is byte-identical by construction. Added the leaf to the mypy CLEAN_MODULES gate and bumped the gate-lint count 23->24 in lockstep. PR #321.

### Main Changes

- New leaf server_ops_profiles.py (791 lines) holds OPS_SCENARIO_PROFILES + its dataclasses/builders/validator; server_ops.py re-imports via an as-aliased one-way stub
- server_ops.py shrank 7862->7095 lines; six moved names kept in __all__ (rebound by the stub); server.py alias block and the three facades unchanged
- mypy CLEAN_MODULES gains the leaf (type-checks clean); test_mypy_gate_lint expected count 23->24; docs (CLAUDE.md, architecture.md, CHANGELOG, repomix map) updated


### Git Commits

| Hash | Message |
|------|---------|
| `2f4f12c` | refactor(server): extract ops scenario profiles into server_ops_profiles.py |

### Testing

- [OK] Full suite: 1746 passed, 2 skipped (env-gated smoke)
- [OK] Server-family + gate-lint tests: 152 passed (-n 0)
- [OK] mypy gate clean (24 modules); ruff clean; object identity preserved across leaf/server_ops/server

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 54: Wire approval-duplicate gate via pr_comment.sh (A-034)

**Date**: 2026-08-04
**Task**: Wire approval-duplicate gate via pr_comment.sh (A-034)
**Package**: amc
**Branch**: `sdelmas/wire-approval-duplicate-gate`

### Summary

Wired the previously-unused approval-duplicate gate (Option A-lite) into a live enforcement path: new tools/pr_comment.sh chains role-name + approval-duplicate gates then posts via gh pr comment. Retired audit A-034. Shipped through sd-review (gito clean; iterative fixes for word-splitting, flag-as-value, set -e/cd semantics, symlink resolution) and remote Copilot review (help exit-code + conflated line-count figures).

### Main Changes

- New tools/pr_comment.sh: canonical PR-comment poster chaining both body gates, redirecting the body into each gate independently; --dry-run and -- passthrough supported
- Retargeted CLAUDE.md manual && chains at the wrapper; added Comment pre-flight wrapper subsection; recorded convention in .trellis/spec/amc/backend/documentation-review.md
- Flipped audit A-034 to status: fixed
- Hardening from review: flag-shaped-value guard, set -e/cd resolution, POSIX symlink-chain resolution, -h/--help exit-0 path, corrected conflated ~690-line gate vs ~1,000-line test figures


### Git Commits

| Hash | Message |
|------|---------|
| `1e1b98a` | feat(tools): wire approval-duplicate gate via pr_comment.sh (A-034) |
| `8f2c771` | fix(tools): address review findings on pr_comment.sh wiring |
| `62a802d` | fix(tools): reject flag-shaped values for pr_comment.sh --pr/--body-file |
| `7c5ac63` | fix(tools): drop redundant `\|\| exit $?` and restore task.json newline convention |
| `9fd82ee` | fix(tools): derive pr_comment.sh prog tag from $0 and guard path-resolution cd |
| `31c68ee` | fix(tools): resolve pr_comment.sh path through symlink chain |
| `87cdf2f` | fix: address Copilot review comments on PR #322 |
| `4510a86` | chore(task): mark 07-17 acceptance criteria satisfied |

### Testing

- [OK] shellcheck tools/pr_comment.sh clean
- [OK] sd-review gito provider clean at head 31c68ee (prism 1 low nit, fixed)
- [OK] manual gate transcript: help exit 0/stdout, error exit 2/stderr, symlink invocation resolves repo root, flag-value guard rejects --pr --dry-run

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 55: Extract server_ops_parse.py (epic 07-06 step 2)

**Date**: 2026-08-04
**Task**: Extract server_ops_parse.py (epic 07-06 step 2)
**Package**: amc
**Branch**: `sdelmas/extract-server-ops-parse`

### Summary

Extracted the client-command parse cluster (ParsedCommand, flag/alias tables, parse_command with the _parse_kubectl/_parse_helm family, and the command_fingerprint/guess_intent/_redact_* helpers) from server_ops.py into a new stdlib-only leaf server_ops_parse.py, re-imported at the original position. Import-only verbatim move: render-oracle byte-identical over a 33-command corpus; server_ops.py 7095->6589 lines. Review loop (Gito/Prism + github-code-quality) trimmed 7 genuinely-dead re-imports and rebutted the remaining findings as cross-module false positives, repo-convention (task.json newline), generated-file misreads, or pre-existing verbatim-move behavior.

### Main Changes

- New server_ops_parse.py leaf (26-symbol parse cluster), server_ops.py re-imports 19 used names at ParsedCommand's original position (one-way import; leaf never imports server_ops)
- Review fix fc5bd3a: removed 7 dead re-imports (_VALUE_FLAGS, _REPEATABLE_VALUE_FLAGS, _BOOL_FLAGS, _EXPLAIN_GROUP_ALIASES, _store_flag_value, _split_explain_target, _normalize_explain_resource) and synced the module docstring
- Docs: CLAUDE.md server-module map, architecture.md, CHANGELOG.md, docs/repomix-map.md, check_mypy_gate.py gated list


### Git Commits

| Hash | Message |
|------|---------|
| `d607689` | refactor(server): extract command parse cluster into server_ops_parse.py |
| `fc5bd3a` | refactor(server): trim dead parse re-imports, sync module docstring |
| `f2f606b` | chore(task): archive 08-04-server-ops-parse-extract |

### Testing

- [OK] render-oracle diff IDENTICAL over 33-command corpus (before/after extraction and after review fix)
- [OK] server-family suite: 149 passed, 2 skipped (test_server/ops_fuzz/mcp/eval_mode)
- [OK] ruff F841 clean, mypy gate clean (25 files), import smoke + __all__ resolves

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 56: Fix stale security, reviewer, and reference docs (A-026..A-069)

**Date**: 2026-08-04
**Task**: Fix stale security, reviewer, and reference docs (A-026..A-069)
**Package**: amc
**Branch**: `sdelmas/audit-doc-accuracy-sweep`

### Summary

Doc-accuracy sweep: corrected SECURITY.md redaction posture, removed 5 phantom CLI flags from Copilot instructions + added a forbidden-needle contract lint, raised dependency floors to the cp314-resolved versions, and refreshed README/CLAUDE/pyproject reference surfaces. Closed audit ledger items A-026/027/028/029/030/046/064/069.

### Main Changes

- SECURITY.md: rewrote otel-activity.log redaction bullet to the shipped dual posture (request-side allowlist-of-sensitive, response-side mask-unless-known-safe, shared _mask_sensitive_value)
- Copilot instructions: dropped 5 removed flags, added canonical-CLI-surface anchor; new COPILOT_FORBIDDEN_NEEDLES + _require_absent() so a reintroduced phantom flag fails the contract lint
- pyproject: raised floors (numpy>=2.5.1, opentelemetry-proto>=1.44.0, protobuf>=7.35.1, pyyaml>=6.0.3) in all 3 sites; uv.lock metadata-only refresh (no resolved-version drift)
- README/CLAUDE/CHANGELOG: completed dev-extra list, uv sync --locked primary install, OTEL auth-scheme row, CI Result aggregate naming


### Git Commits

| Hash | Message |
|------|---------|
| `886faeb` | docs(audit): fix stale security, reviewer, and reference docs |

### Testing

- [OK] node review-preflight: 0 failures (after fixing 3 task-artifact blockers)
- [OK] sd-review scope=pr: ready, gito+prism clean
- [OK] full not-heavy suite (prior): 1703 passed, 2 skipped

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 57: Extract server_command_render leaf (epic 07-06 helm precursor)

**Date**: 2026-08-04
**Task**: Extract server_command_render leaf (epic 07-06 helm precursor)
**Package**: amc
**Branch**: `sdelmas/server-command-render-extract`

### Summary

Extracted CommandResult + _table/_is_dry_run/_unsupported/_exposed_active_scenarios verbatim out of server_ops.py into new pure leaf server_command_render.py, deduped _format_dt onto server_mutations, and closed the last render-primitive coupling blocking the parked server_helm_impl step-3 extraction. Verbatim move, one-way runtime import (SimulationState TYPE_CHECKING-only), render-oracle byte-identical over a 14-command corpus in normal + eval modes. PR #331.

### Main Changes

- New pure leaf server_command_render.py (90 lines): CommandResult dataclass + render/command primitives; server_ops re-imports every name at the CommandResult block position
- _format_dt deduped: leaf and server_ops both re-export the byte-identical server_mutations copy; duplicate server_ops body deleted (single source of truth)
- server_ops.py 5,590 to 5,540 lines; leaf added to mypy clean gate (29 modules); CLAUDE.md + architecture.md DAG updated; epic 07-06 implement.md step-3-precursor status recorded


### Git Commits

| Hash | Message |
|------|---------|
| `20c4ed2` | refactor(server): extract CommandResult + render primitives to server_command_render leaf |
| `2474c2b` | docs(task): record server_command_render precursor step status in epic 07-06 implement.md |
| `dbbe6b6` | chore(task): record branch for 08-04-server-ops-support-render-primitives |

### Testing

- [OK] render-oracle before/after byte-identical (cmp) over 14-command corpus, normal + --mcp-eval-mode
- [OK] one-way runtime import grep: sole from .server_ops is TYPE_CHECKING-guarded SimulationState
- [OK] server_ops.CommandResult is server_command_render.CommandResult identity holds; server + server_mcp import clean
- [OK] mypy clean gate exit 0 (29 modules); full suite 1746 passed / 2 skipped

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 58: Fix eval recipe trace-evidence loss (A-066)

**Date**: 2026-08-04
**Task**: Fix eval recipe trace-evidence loss (A-066)
**Package**: amc
**Branch**: `sdelmas/audit-eval-harness-trace-retrieval`

### Summary

serve_main now emits a wall-safe stderr warning when --mcp-eval-mode runs without --persist-command-db/--persist-command-log, since eval mode 404s the /v1/debug trace export and the in-memory ring dies with the process. README eval recipe + SECURITY.md document the persistence-based retrieval path (read offline via amc trace-bundle); ledger A-066 flipped to fixed. Copilot review: fixed a split README code span and named --persist-command-log as an equally sufficient remedy.

### Main Changes

- serve_main prints _EVAL_NO_PERSIST_WARNING to stderr when eval mode lacks command-trace persistence (module constant shared with the wiring test as single source of truth)
- README eval recipe gains --persist-command-db + rationale; SECURITY.md documents the sanctioned harness-side retrieval path; both name --persist-command-log as an alternative
- Audit ledger A-066 open -> fixed
- Parametrized wiring test covers 5 flag combos (eval-only warns; eval+db, eval+log, eval+both, non-eval stay silent)


### Git Commits

| Hash | Message |
|------|---------|
| `327e0e5` | fix(server): warn when eval mode runs without command-trace persistence (A-066) |
| `db62edc` | test(serve): cover eval mode with both persistence flags set |
| `e39deb1` | refactor(server): hoist eval-no-persistence warning to a module constant |
| `9b3b215` | docs(server): fix split README code span, name both persistence flags in remedy |

### Testing

- [OK] pytest tests/test_serve_main_wiring.py -> 14 passed
- [OK] full 'not heavy' suite -> 1707 passed, 2 skipped (pre-review)
- [OK] ruff + mypy gate clean; CI Result green on head 9b3b215
- [OK] Copilot review clean on final head (8/8 files, no new comments)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 59: Prune MCP tool scans and trace-store hot paths (audit A-039..A-042)

**Date**: 2026-08-04
**Task**: Prune MCP tool scans and trace-store hot paths (audit A-039..A-042)
**Package**: amc
**Branch**: `sdelmas/mcp-query-performance`

### Summary

Flattened amc serve MCP window tools and the command-trace store so narrow queries and debug-UI polls no longer scale with run data / trace history. All output-identical: no artifact bytes or locked hashes change.

### Main Changes

- A-039: lexicographic [lo,hi) window pre-filter before strptime in the three MCP window tools; break past hi only on the monotonic wide non-DST layout via _layout_allows_break.
- A-040: /v1/state count via unsupported_fingerprint_count() (COUNT(DISTINCT fingerprint)); full unsupported_summary memoized on a store generation (_sqlite_gen / _version).
- A-041: one long-lived SQLite connection + one long-lived JSONL append handle instead of reopening both per insert; close() releases both.
- A-042: hoisted per-component-invariant lists above the per-replica loop in resource_snapshot().
- Review fixes: _window_boundary_strings catches OverflowError and raises McpToolError (INVALID_PARAMS); _locked_conn re-reads _conn under _sqlite_lock to close a TOCTOU race with close().


### Git Commits

| Hash | Message |
|------|---------|
| `b282d55` | perf(server): flatten MCP query + trace-store hot paths (audit A-039..A-042) |
| `fdf3397` | fix(server): harden MCP window overflow + trace-store close race (PR #337 review) |

### Testing

- [OK] focused: tests/test_server_mcp.py tests/test_server.py -> 134 passed, 2 skipped
- [OK] full suite earlier: 1760 passed, 2 skipped
- [OK] tools/check_mypy_gate.py -> no issues in 30 files; pre-commit + review preflight clean

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 60: Serve error plane observable by default (PR A: A-071..A-074, A-076)

**Date**: 2026-08-05
**Task**: Serve error plane observable by default (PR A: A-071..A-074, A-076)
**Package**: amc
**Branch**: `sdelmas/serve-error-visibility-sinks`

### Summary

PR A of task 07-17-audit-serve-error-visibility: made the amc serve error plane observable by default. One operator error sink (structured log or stderr [serve-error] block) carrying exception type/message/capped traceback tail; client 500 bodies stay generic. Background continuous-generation/OTEL arms and the MCP internal-error path route through the sink. Mutating-method boundary gets a catch-all; a raising Kubernetes API mutation now also records its failed trace in the kubernetes-api debug ring. Real two-dimension /readyz (artifacts present AND generation-thread healthy; 503 names the failing dimension, eval-wall-safe). Review: fixed a _capture_traceback_tail off-by-one (strict line cap), added an _ErrorSink Protocol to type the sink across the one-way module DAG, recorded the failed-k8s-mutation trace, and switched Protocol stubs to explicit pass. Planning finalization: task stays open for PR B (A-075 refusal counters, A-077 per-request id).

### Main Changes

- server_ops.py: _record_server_error/_emit_error_record operator error sink; _capture_traceback_tail strict line cap; _ErrorSink Protocol typing the sink over the one-way DAG
- server.py: _handle_mutating_method catch-all returns generic 500 (Status/JSON) and records the failed k8s API mutation trace; two-dimension _readyz_check
- SimulationState.request_logger carries the sink to background threads; serve_main reordered so the logger exists first
- task PRD: added unchecked A-075/A-077 acceptance criteria so the completion boundary reflects PR B remaining


### Git Commits

| Hash | Message |
|------|---------|
| `d908588` | feat(server): make serve error plane observable by default (A-071..A-074, A-076) |
| `9ee939f` | fix(server): make _capture_traceback_tail a strict line cap |
| `2cddb11` | refactor(server): give the operator error sink a typed Protocol |
| `8f2c431` | fix(server): record failed k8s API mutation trace; pass-body Protocol stubs |
| `ce81628` | docs(task): add A-075/A-077 acceptance criteria as PR B follow-ups |

### Testing

- [OK] .venv/bin/pytest -k 'patch_kubernetes_api_unexpected_exception or capture_traceback_tail or readyz or record_server_error or mutating_kubernetes_api or body_limit_and_mutating' -n0 -> 14 passed
- [OK] ruff check src/ tests/ -> clean; tools/check_mypy_gate.py -> 30 files, no issues
- [OK] sd-review scope=pr -> ready (local prism/gito clean, Copilot 0 new comments, all threads resolved)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 61: Ship PR B of 07-17: DoS-refusal counters (A-075) + request-id join key (A-077)

**Date**: 2026-08-05
**Task**: Ship PR B of 07-17: DoS-refusal counters (A-075) + request-id join key (A-077)
**Package**: amc
**Branch**: `sdelmas/serve-error-refusal-counters`

### Summary

Implemented and shipped PR B (#339) of task 07-17-audit-serve-error-visibility: RefusalCounters shared with the bounded server (worker-cap 503 / both SSE 503s / rate-limit 429 each recorded, surfaced as /v1/state.refusals with a one-shot [serve-refusal] stderr line, outside the eval-mode wall), and a per-request uuid join key threaded from handle_one_request into structured records and CommandTrace (payload-only, rides payload_json). Review found only false positives (imports present at server_ops:16-17; _increment validated at its sole caller); extracted _increment to clarify lock scope. Copilot APPROVED the exact head; CI Result green. Task 07-17 archived with all seven ledger items (A-071..A-077) fixed across PR A + PR B.

### Main Changes

- server_ops.RefusalCounters: thread-safe worker_cap/sse/rate_limit tally, shared via SimulationState.refusals default_factory, surfaced in summary() as /v1/state.refusals with a one-per-kind first-trip stderr line (A-075)
- server.py: request_id=uuid4().hex[:12] minted in handle_one_request, added to structured base_record and threaded into run_command / record_kubernetes_api_call / MCP _record_mcp_trace via payload-only CommandTrace.request_id (A-077)
- Both SSE-503 refusal sites counted (_with_sse_slot + k8s watch); rate-limit 429 and worker-cap 503 counted at their sites
- Refactor: extracted RefusalCounters._increment so stderr IO stays off the lock and static-analyzer misreads of the cross-block local dissolve
- Docs/ledger: CLAUDE.md + operations-security-logging.md refusals & request_id paragraphs, CHANGELOG Unreleased bullet, ledger A-075/A-077 -> fixed; task 07-17 archived


### Git Commits

| Hash | Message |
|------|---------|
| `d3e8b31` | feat(server): count DoS-bound refusals and add per-request id join key (A-075, A-077) |
| `e5d4c8d` | refactor(server): extract RefusalCounters._increment for clearer lock scope |
| `79ffbff` | chore(task): finalize 07-17 — set branch, tick satisfied acceptance criteria |
| `4ecd6da` | chore(task): archive 07-17-audit-serve-error-visibility |

### Testing

- [OK] .venv/bin/pytest tests/test_server.py tests/test_server_eval_mode.py tests/test_server_mcp.py tests/test_server_ops_fuzz.py tests/test_serve_main_wiring.py -n 0 — 190 passed, 2 skipped
- [OK] .venv/bin/pytest (full) — 1779 passed, 2 skipped
- [OK] .venv/bin/pre-commit run --all-files — all hooks pass
- [OK] Copilot review APPROVED head e5d4c8d; CI Result pass

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 62: audit-sim-mutation-correctness: simulator clock + command-mutation correctness (A-012..A-017)

**Date**: 2026-08-05
**Task**: audit-sim-mutation-correctness: simulator clock + command-mutation correctness (A-012..A-017)
**Package**: amc
**Branch**: `sdelmas/sim-mutation-correctness`

### Summary

Closed audit A-012..A-017 in one PR (#340): SimulationClock.resume() no-op on a running clock; command-mode kubectl delete/scale/patch existence-check against the overlay-aware snapshot with kubectl-shaped NotFound + nonzero exit (parity with the REST facade), nameless scale = usage error; otel_status serialized under a lock with a snapshot copy; failed continuous-generation pass reloads published anomalies.csv from disk; _iter_component_rows guards zero-byte/blank-header CSVs; CommandTraceStore.list clamps negative/zero limit identically on both backends.

### Main Changes

- resume() paused-guard so a running clock is not rewound (A-012)
- _render_scale/_render_delete/_render_patch resolve the target via resource_snapshot() before any overlay write; _not_found on miss; nameless scale -> kubectl.scale.usage (A-013)
- otel_status_lock + otel_status_snapshot/update_otel_status/bump_otel_status; /v1/state copies under the lock (A-014)
- _record_continuous_generation_failure reloads on-disk anomalies.csv (A-015)
- csv_layout._iter_component_rows guards empty/blank header (A-016); CommandTraceStore.list clamps max(0, limit) before both backends (A-017)


### Git Commits

| Hash | Message |
|------|---------|
| `4cb9adc` | fix(server): simulator clock resume + command-mutation existence parity (A-012..A-017) |
| `96b68f6` | fix(server): address Copilot review on A-016 header guard + test except scope |
| `ad554a5` | docs(server): clarify otel_status thread-safety rests on the lock, not pre-seeding |
| `d5af17b` | docs(server): reword build_state otel_status comment to match the lock-safety framing |

### Testing

- [OK] tests/test_sim_mutation_correctness.py + test_gauges_file.py + test_combine.py: 72 passed
- [OK] deterministic sd-check: all rows passed (incl. knowledge.obsidian-kb)
- [OK] Copilot review of final head d5af17b: no new comments; all 4 review threads resolved
- [OK] CI Result SUCCESS on d5af17b; mergeState CLEAN

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 63: Extract server_k8s_api.py from server_ops.py (epic 07-06 step 5)

**Date**: 2026-08-05
**Task**: Extract server_k8s_api.py from server_ops.py (epic 07-06 step 5)
**Package**: amc
**Branch**: `sdelmas/server-k8s-api-extract`

### Summary

Role-swap extraction of the pure Kubernetes REST-facade builder/filter/format layer out of server_ops.py into the one-way leaf server_k8s_api.py, with the _api_* trace/redaction sink carved into server_k8s_api_trace.py for the 800-line cap. The resource_snapshot-bound dispatch spine stayed in server_ops.py (monkeypatch-pinned). _preview moved down to server_ops_support.py. Review-fix pass dropped 6 verified-dead re-exports and corrected four doc surfaces; 5 Copilot findings fixed, 5 rebutted as cross-module re-export/CodeQL-isolation false positives.

### Main Changes

- New leaf server_k8s_api.py (743 lines): KubernetesApiResponse + response builders, discovery/_k8s_api_resource_list data builders, structural OpenAPI helpers, selector/namespace filters, pure watch helpers, non-snapshot mutation-parse, request-body readers, render_kubeconfig
- New sink leaf server_k8s_api_trace.py (172 lines): _api_* fingerprint/redaction cluster, one-way trace -> api
- _preview relocated to server_ops_support.py; server_ops.py 5,440 -> 4,693 lines; server_ops.__all__ byte-unchanged (227 entries)
- Review-fix: removed 6 zero-consumer re-exports (DEFAULT_MAX_BODY_BYTES, _query_int, _query_str, _SENSITIVE_QUERY_KEYS, _WATCHABLE_LIST_RESOURCES, _watch_requested); corrected leaf docstring + CLAUDE.md + architecture.md + task.json (server_traces / spine-stayed accuracy)


### Git Commits

| Hash | Message |
|------|---------|
| `8374369` | refactor(server): extract k8s REST-facade pure layer into server_k8s_api leaf (epic step 5) |
| `ae25817` | refactor(server): drop dead k8s_api re-exports; fix extraction docs |
| `cc9d5a9` | docs(task): check off 08-05 acceptance criteria (all verified this cycle) |

### Testing

- [OK] import anomaly_metric_creator.server succeeds (re-import seam + server.py alias block resolve)
- [OK] mypy clean gate: 32 source files, no issues (both leaves gated)
- [OK] tests/test_server.py + fuzz + mcp + eval + watch: 186 passed, 2 skipped (-n 0)
- [OK] render-oracle byte-identical over 64-section k8s-API corpus; review preflight 0 failures

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 64: CLAUDE.md context-cost refactor: 3,106 lines to 259

**Date**: 2026-08-05
**Task**: CLAUDE.md context-cost refactor: 3,106 lines to 259
**Package**: amc
**Branch**: `main`

### Summary

Cut CLAUDE.md from 3,106 lines / 195 KB / ~77.9k tokens to 259 lines / 16 KB / ~4k tokens, reclaiming 26% of every context window. The file is loaded in full on every session start and compaction, and its own first paragraph already declared .trellis/spec/amc/backend/index.md canonical before restating ~3,000 lines of it.

Classified every line span in a 37-row disposition table reconciling exactly to COVERED 2,667 + MOVE 275 + RETIRE 162 + STAYS 2 = 3,106. The evidence rule that made this safe: a grep hit on a module or flag name is not coverage — name the contract the prose asserts and quote the destination sentence. That caught three contracts with zero coverage anywhere in the repo, which were relocated rather than deleted: the long-form RLIMIT_NOFILE / _ensure_long_form_fd_capacity preflight and assume_monotonic_wide_components (api-cli-server.md), and the two-posture header-redaction asymmetry (operations-security-logging.md). A keyword sweep would have lost all three.

RETIRE was sentence-level, not paragraph-level: past-tense project voice routinely wraps a live present-tense clause. Eight such anchors were preserved by relocation, including the schema reader still honoring topology_mode: independent and the apply_dtype_int_cast kwarg surviving for programmatic callers.

What survives in CLAUDE.md: module-ownership map, extraction / re-import invariant, determinism contract, pipeline order, working rules, review readiness, repository-lints table, and a routing table into the specs. The 15 pre-PR checklist headings now have one canonical home in testing-quality.md with all mirrors repointed. 13 stale citations fixed across 13 files.

Both reusable conventions were written into documentation-review.md so the next consolidation does not relearn them.

PR #342 merged at 90a1a21. CI Result pass on all lanes. Copilot raised 4 findings: one real (a circular Sources footer this PR caused, fixed in d92c67d), one factually wrong (rebutted with a quote from testing-quality.md:264-266), two correct in kind but out of scope (3 of 73 pre-existing CLAUDE.md Sources citations; fixing a sample would reduce consistency, and some footers stay correct because that content really does still live in CLAUDE.md).

Two things left open and stated rather than implied: pre-commit run --all-files is 12 of 13 hooks, the failure being a pre-existing workspace-journal gap in .trellis/workspace/sdelmas/index.md (sessions 20-63) that fails identically on clean main and does not run in CI; and no Trellis task was created for the 72-footer Sources sweep because that needs user consent, so it is logged in the archived implement.md instead.

### Main Changes

- Detailed change bullets were not supplied; see the summary above.

### Git Commits

| Hash | Message |
|------|---------|
| `be4d5bc` | (see git log) |
| `ae603aa` | (see git log) |
| `d8d9c19` | (see git log) |
| `6549a2b` | (see git log) |
| `8e65ed9` | (see git log) |
| `8e01b37` | (see git log) |
| `d92c67d` | (see git log) |
| `90a1a21` | (see git log) |

### Testing

- Validation was not recorded for this session.

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 65: Extract server_ops_explain and server_ops_payloads (epic step 6a)

**Date**: 2026-08-06
**Task**: Extract server_ops_explain and server_ops_payloads (epic step 6a)
**Package**: amc
**Branch**: `sdelmas/08-05-server-ops-explain-payload-extract`

### Summary

Epic 07-06 step 6a: moved the ten pure kubectl explain / OpenAPI schema formatters and the JSON Patch / manifest-reader clusters out of server_ops.py into two new one-way leaves, shrinking server_ops.py 4,687 to 4,414 lines with no behavior change. A read-only AST closure audit showed the originally planned step 6 render-dispatch split is not implementable as designed, so the blocked part was recorded as step 6b instead of silently substituting scope.

### Main Changes

- Added server_ops_explain.py (178 lines) — the epic's first leaf with no intra-package import at all — holding the ten explain/OpenAPI formatters moved verbatim from server_ops.py:1944-2101.
- Added server_ops_payloads.py (172 lines) with the RFC 6902 JSON Patch ops (RFC 6901 pointer paths) and the manifest document reader, importing only CommandResult from server_command_render.
- Cut the three ranges bottom-up and replaced each with a commented 'X as X' re-import stub, keeping server_ops.__all__ byte-identical at 227 entries.
- Recorded the falsifying closure audit for the planned step 6 in the epic tracker: all ten render-dispatch symbols reach resource_snapshot, whose closure touches the runtime dataclasses step 7 requires to stay, so the split is filed as step 6b blocked on a provider-seam decision.
- Added both leaves to the clean-module mypy gate (32 to 34) with the lockstep test update, and repaired three pre-existing review-preflight failures (archived-task citations, missing epic child entry, _example manifest scaffolds).
- Review round: rebutted three 'unused import' findings as deliberate re-export stubs required by the extraction invariant, and fixed the real RFC 6901/6902 conflation at five sites.


### Git Commits

| Hash | Message |
|------|---------|
| `df36daf` | refactor(server): extract server_ops_explain and server_ops_payloads |
| `4dc6633` | docs(spec): record step 6a leaves and the step 6b resource_snapshot seam |
| `cc58fbc` | chore(trellis): satisfy the review preflight for the step-6a extraction |
| `7c21817` | docs: name RFC 6901 and RFC 6902 correctly for the payload leaf |
| `202c4f4` | chore(task): record finalization branch for the step-6a extraction task |
| `9cdf3c0` | chore(task): tick step-6a acceptance criteria with measured evidence |

### Testing

- [OK] Frozen-clock render oracle byte-identical over a 72-record explain/patch/apply corpus in both normal and --mcp-eval-mode states
- [OK] server_ops.__all__ byte-identical (227 entries) via AST source-segment diff
- [OK] .venv/bin/pytest tests/test_server.py tests/test_server_ops_fuzz.py tests/test_server_mcp.py tests/test_server_eval_mode.py -n 0 — 178 passed, 2 skipped
- [OK] Full .venv/bin/pytest — 1797 passed, 2 skipped
- [OK] .venv/bin/pre-commit run --all-files — 13/13
- [OK] python tools/check_mypy_gate.py — Success: no issues found in 34 source files
- [OK] node scripts/sd-ai-command-pack-review-preflight.mjs — 0 failures
- [OK] CI on head 7c21817 — CI Result success, all lanes green or skipped

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 66: A-031: dedupe the SQLite trace INSERT across both CommandTraceStore write paths

**Date**: 2026-08-06
**Task**: A-031: dedupe the SQLite trace INSERT across both CommandTraceStore write paths
**Package**: amc
**Branch**: `sdelmas/08-06-trace-row-insert-dedupe`

### Summary

Extracted the duplicated 21-column command_traces INSERT and its FTS mirror from _insert_sqlite and _replace_sqlite_traces into one _insert_trace_row helper, then converged a 7-round local review that turned two prose contracts into mechanically enforced ones. server_traces.py 1,025 -> 998 lines.

### Main Changes

- Extracted _insert_trace_row(conn, trace, payload, *, delete_fts_first); payload stays a parameter so _insert_sqlite keeps serializing outside its _locked_conn()
- Derived delete_fts_first in _replace_sqlite_traces from whether its bulk FTS clear ran, instead of hard-coding False
- Added 8 tests: raw per-column assertions on both write paths, direct command_traces_fts queries, off-lock payload serialization, and why the bulk clear cannot be replaced by the per-row delete
- Recorded the row-writer contract and two trace-store test gotchas in the operations-security-logging spec; flipped audit A-031 to fixed with symbol-anchored evidence


### Git Commits

| Hash | Message |
|------|---------|
| `17c5e2f` | refactor(server-traces): extract shared _insert_trace_row (audit A-031) |
| `00bccc5` | chore(task): tick A-031 acceptance criteria with measured evidence |
| `2d90b9e` | docs(spec): record the trace-store row-writer contract and two test gotchas |
| `5a7596d` | test(server_traces): pin _insert_trace_row's two parameter contracts |
| `db93eb0` | docs(server_traces): move _insert_trace_row rationale to the spec |
| `3646758` | refactor(server_traces): derive delete_fts_first from the bulk clear |
| `5fc3fcb` | test(server_traces): pin why the FTS bulk clear is load-bearing |
| `06223c4` | docs(ledger): keep A-031 why: as the problem, not the fix |

### Testing

- [OK] Full suite: 1805 passed, 2 skipped in 229.97s
- [OK] pytest tests/test_server.py -k 'sqlite or fts' -n 0: 21 passed, 103 deselected
- [OK] pre-commit run --all-files: no failures
- [OK] Byte oracle over both write paths, pre- vs post-change: IDENTICAL (16 rows, fts=True)
- [OK] Mutation-checked 4 invariants: delete_fts_first inversion, to_dict under the lock, bulk-clear removal, per-row delete widened to full-table

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 67: Unblock server_traces.py for the mypy clean gate

**Date**: 2026-08-07
**Task**: Unblock server_traces.py for the mypy clean gate
**Package**: amc
**Branch**: `sdelmas/08-07-server-traces-mypy-gate`

### Summary

Renamed CommandTraceStore.list to list_traces, removing the class-body binding that shadowed the builtin for every annotation below it, and typed the trace payload with TracePayload / TraceListItem TypedDicts. server_traces.py joins the mypy clean gate (35 modules).

### Main Changes

- Renamed CommandTraceStore.list to list_traces. The old name bound list in the class body; mypy resolves annotations in class scope, so 10 of the module's 11 --strict errors were reported hundreds of lines from their cause. No compatibility alias: an alias re-creates the binding.
- Added module-level TracePayload and TraceListItem TypedDicts, with the required / NotRequired split derived from CommandTrace.from_dict: the 13 keys it subscripts are required, the 11 it defaults are NotRequired. Closed the 11th error, the no-any-return at _row_to_payload.
- One deliberate behavior change: _row_to_payload raises TypeError when a payload_json row decodes to a non-object, instead of returning it and failing downstream. Unreachable from any row this store wrote.
- server_traces.py entered tools/check_mypy_gate.py (CLEAN_MODULES 34 to 35) and its module-size ratchet ceiling rose 1013 to 1086 for the TypedDicts, a non-separable addition.
- Documented the payload contract, the two trust tiers at the trace boundary, and the PEP 563 trap (TracePayload.__optional_keys__ is empty at runtime) in operations-security-logging.md; added the never-bind-a-builtin-in-a-class-body rule to testing-quality.md.


### Git Commits

| Hash | Message |
|------|---------|
| `a84111a` | refactor(traces): rename CommandTraceStore.list and type the trace payload |
| `bad43ac` | docs(traces): record the payload-shape contract and the class-body shadow rule |
| `eb6fbc9` | test(traces): derive the payload key split from behavior, not from a list |
| `5a6de6d` | test(server): derive the optional-key list from the TracePayload split |

### Testing

- [OK] mypy --strict src/anomaly_metric_creator/server_traces.py: Success: no issues found in 1 source file
- [OK] tools/check_mypy_gate.py: Success: no issues found in 35 source files (exit 0)
- [OK] tools/check_module_size.py: exit 0
- [OK] Full suite: 1917 passed, 2 skipped
- [OK] pre-commit run --all-files: all hooks Passed; ruff check tests/: All checks passed!

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 68: Capture three review learnings from shipping PR #360

**Date**: 2026-08-07
**Task**: Capture three review learnings from shipping PR #360
**Package**: amc
**Branch**: `sdelmas/08-07-capture-ship-review-learnings`

### Summary

Docs-only maintenance branch. Wrote down three patterns from the PR #360 ship cycle that had cost a review round or a watch budget and were recorded nowhere: a recurring Copilot false positive about the module-size ratchet's line counting, the pull-request-side CI concurrency ordering hazard that leaves CANCELLED rollup rows a per-row eligibility probe counts as blocking, and the pre-archive gate deadlock caused by exclusive If X / If not X acceptance criteria. All three are prose-only by nature, so no tools/check_*.py lint was warranted.

### Main Changes

- testing-quality.md: sixth entry in the Known Copilot false positives catalogue, covering the ratchet line-count claim that recurs on every ceiling bump
- testing-quality.md: new paragraph on PR-side concurrency, documenting that ready_for_review / labeled / push each cancel the in-flight run and that the remedy is event order, not a workflow change
- documentation-review.md: rule and worked example for exclusive If X / If not X acceptance criteria, which always leave one box unchecked and block the pre-archive gate
- Applied both Copilot findings: the wc -l equivalence claim was imprecise against _count_lines, and the criteria example showed only one of the two branches


### Git Commits

| Hash | Message |
|------|---------|
| `f1f8f82` | docs(spec): capture three review learnings from shipping PR #360 |
| `2b8f031` | docs(spec): tighten the two claims Copilot flagged on the learnings capture |

### Testing

- [OK] pre-commit on both changed files: role-name leaks, CI/review cadence contract, Copilot instruction contract all Passed; every other hook reported no files to check
- [OK] tools/check_task_criteria_commands.py .trellis/tasks exit 0
- [OK] git diff --check exit 0
- [OK] PR #361 CI Result COMPLETED/SUCCESS; docs-only diff routed to the lightweight readiness lane as designed

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 69: SD AI command pack refresh to 0.71.2

**Date**: 2026-08-12
**Task**: SD AI command pack refresh to 0.71.2
**Package**: amc
**Branch**: `chore/sd-ai-command-pack-0.71.2`

### Summary

Refresh the vendored SD AI command pack from 0.71.1 to 0.71.2 for the claude, gemini, github, and opencode platforms, including the managed .obsidian-kb ignore block.

### Main Changes

- Installed pack 0.71.2 across claude, gemini, github, and opencode; audit passed 199 targets with matching provenance.
- Updated the managed .obsidian-kb generated-block marker in .gitignore to the 0.71.2 form.
- Regenerated docs/repomix-map.md as the manifest-ordered candidate preparation step.


### Git Commits

| Hash | Message |
|------|---------|
| `5dd64984d7201cc69e2a4e304dedc5fa6297b4a2` | chore(sd-ai-command-pack): refresh vendored pack to 0.71.2 |

### Testing

- [OK] python3 scripts/sd-ai-command-pack-check.py --json (passed: 6 checks passed, 0 failed, state guard clean)
- [OK] python3 tools/check_ci_review_contract.py and python3 tools/check_copilot_instruction_contract.py (both passed)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 70: sd-ai-command-pack 0.71.4 refresh

**Date**: 2026-08-13
**Task**: sd-ai-command-pack 0.71.4 refresh
**Package**: amc
**Branch**: `chore/sd-ai-command-pack-0.71.4`

### Summary

Refresh the vendored sd-ai-command-pack from 0.71.2 to the 0.71.4 corrective release as part of fleet campaign refresh-0.71.4-20260813T212139Z.

### Main Changes

- Installed pack 0.71.4 over 0.71.2, carrying four drifted installer targets forward with --force
- Regenerated docs/repomix-map.md with scripts/update_repomix


### Git Commits

| Hash | Message |
|------|---------|
| `3fc7020325bc3069d467e9a15018174da5cb90d6` | chore(sd-ai-command-pack): refresh vendored pack 0.71.2 -> 0.71.4 |

### Testing

- [OK] install audit: 199 targets checked, provenance 0.71.4, vouched file hashes match
- [OK] python3 tools/check_ci_review_contract.py: passed
- [OK] python3 tools/check_copilot_instruction_contract.py: passed
- [OK] sd-check: 6 passed, 1 skipped (obsidian-kb advisory), 0 failed

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 71: sd-ai-command-pack 0.71.5 refresh

**Date**: 2026-08-14
**Task**: sd-ai-command-pack 0.71.5 refresh
**Package**: amc
**Branch**: `chore/sd-ai-command-pack-0.71.5`

### Summary

Installed sd-ai-command-pack v0.71.5 over 0.71.4 as the final lane of fleet campaign refresh-0.71.5-20260814T113545Z. The changed always-files installed as updates with no conflict and no --force, against the corrected installer.

### Main Changes

- Installed the immutable v0.71.5 payload (source commit e115c70f, digest sha256:365af6fe); audit reports preserved=2, unchanged=197.
- Regenerated the deterministic repomix map through scripts/update_repomix.
- Left .prism/rules.json and .github/PULL_REQUEST_TEMPLATE.md preserved as locally owned.


### Git Commits

| Hash | Message |
|------|---------|
| `b0fb4435ce01261307e12874b47414853c79c42c` | chore: refresh sd-ai-command-pack to 0.71.5 |

### Testing

- [OK] install.py --check --audit: installed version 0.71.5, planned changes 0, audit passed
- [OK] python3 tools/check_ci_review_contract.py && python3 tools/check_copilot_instruction_contract.py: exit 0
- [OK] sd-check: 6 passed, 0 failed, 1 skipped (external-symlinked .obsidian-kb advisory)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 72: chore: refresh sd-ai-command-pack to 0.71.6

**Date**: 2026-08-14
**Task**: chore: refresh sd-ai-command-pack to 0.71.6
**Package**: amc
**Branch**: `chore/sd-ai-command-pack-0-71-6`

### Summary

Fleet campaign refresh-0.71.6-20260814T170234Z, final cohort (anomaly-metric-creator): install 0.71.6 over 0.71.5, regenerate the structural map, and archive the dedicated task inside the published head.

### Main Changes

- Installed sd-ai-command-pack 0.71.6 over 0.71.5 through the vouched-upgrade path
- Regenerated docs/repomix-map.md against the post-archive tree


### Git Commits

| Hash | Message |
|------|---------|
| `1fcac8fc5e55f1f0f6f7f6ecaa30bbb187a267f4` | chore: refresh sd-ai-command-pack to 0.71.6 |

### Testing

- [OK] install audit: 199 targets, provenance 0.71.6, vouched hashes match
- [OK] sd-check --json: passed (6 passed, 1 skipped, 0 failed)
- [OK] check_ci_review_contract.py and check_copilot_instruction_contract.py: exit 0

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 73: Trace-export hardening: CSV formula neutralization, wildcard-CORS gate, bundle version policy (A-018/A-019/A-070)

**Date**: 2026-08-15
**Task**: Trace-export hardening: CSV formula neutralization, wildcard-CORS gate, bundle version policy (A-018/A-019/A-070)
**Package**: amc
**Branch**: `fix/trace-export-hardening`

### Summary

Closed three export-surface audit items on one PR (#376). Neutralized spreadsheet formula triggers across every cell of the trace-bundle CSV export at the writer boundary rather than a named column subset; refused --cors-allow-origin '*' without --auth-token in serve_main (after the --config merge, so config cannot smuggle it) and in start_test_server for parity; and settled the trace-bundle schema-version policy as matching-version-only, documented in the error, a code comment, and the README. Copilot reviewed 18/18 files with zero comments.

### Main Changes

- trace_bundle.py: _neutralize_csv_cell applied to every emitted cell via a dict comprehension at the writer boundary; idempotent, non-strings pass through
- server.py: wildcard-CORS auth gates in serve_main (parser.error) and start_test_server (ValueError); ratchet ceiling 2190 -> 2208 for the two non-separable branches
- trace_bundle.py: version-mismatch error states the matching-version policy and remedy; comment assigns any future adapter decision to the version-bumping PR
- Docs: SECURITY.md cross-origin + CSV sections, README flag row and bundle policy, CHANGELOG behavior changes; specs updated in api-cli-server.md, operations-security-logging.md, testing-quality.md
- Ledger A-018/A-019/A-070 flipped to fixed; follow-up task 08-15-debug-ui-csv-formula-neutralization filed for the debug UI's unguarded client-side CSV


### Git Commits

| Hash | Message |
|------|---------|
| `b214f4c` | fix(trace-export): neutralize CSV formulas, gate wildcard CORS, document bundle version policy |
| `2d7a67c` | chore(spec): move the serve-reject test-timeout rule to its owning spec |
| `d33a3de` | chore(task): record the finalization branch and tick met acceptance criteria |

### Testing

- [OK] .venv/bin/pytest — 2033 passed, 2 skipped
- [OK] Negative verification: with src stashed, the new tests produce 85 failures, so they are not vacuous
- [OK] Manual injection export: every trigger-bearing cell apostrophe-prefixed, benign numeric cells untouched
- [OK] pre-commit run --all-files, ruff check tests/, git diff --check, tools/check_mypy_gate.py (35 files)
- [OK] CI Result SUCCESS on PR #376; Copilot reviewed 18/18 files, zero comments

### Status

[OK] **Completed**

### Next Steps

- None - task complete

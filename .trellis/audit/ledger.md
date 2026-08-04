# Audit ledger
Committed cross-session memory of repo-audit findings; managed by sd-audit-repo (full detail per finding in the dated audit reports beside this file).

## A-001 — Eval ground-truth wall has no structural expression at the MCP tool boundary
- status: fixed
- severity: P2 · effort: M · confidence: Plausible
- dimension: architecture
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-21 @ pending-pr
- evidence:
  - tests/test_server_eval_mode.py — every registered handler and its transitively called module-local helpers are AST-scanned for rubric-bearing state and file access, with only the two eval-gated log tools allowed to reach `metric_report.log`.
- why: fixed; a new direct or module-local-helper rubric read now fails the structural wall test.
- fix: narrowed investigation-view state for handlers, or a registry-level guard/lint over MCP_TOOLS.

## A-002 — Server consumes generator internals via untyped `state.legacy: Any` incl. leaf-resident helpers
- status: open
- severity: P2 · effort: M · confidence: Plausible
- dimension: architecture
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/server_ops.py:948 — `legacy: Any`; server_mcp.py:238/315/364/494 reach `_`-helpers with typed homes in timeutil/csv_layout/schema_impl
- why: mypy gate cannot see the seam; drift surfaces as runtime AttributeError in handlers.
- fix: import leaf helpers directly; typed Protocol for the genuinely-legacy surface.

## A-003 — DEFAULT_MAX_BODY_BYTES defined independently in server.py and server_ops.py
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: architecture
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/server.py:44 and server_ops.py:41 — identical separate constants
- why: dormant two-place lockstep drift with no lint.
- fix: delete one definition; alias from the other module.

## A-004 — Instance dataclass ↔ _INSTANCE_DIMENSION_COLUMNS lockstep unchecked post-extraction
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: architecture
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/legacy.py:257 vs csv_layout.py:32 — no fields()-vs-tuple assertion
- why: a new Instance field without the tuple is silently omitted from long-form artifacts.
- fix: import-time assertion that dataclass field order equals the tuple.

## A-005 — Anomaly/cascade specs are untyped dicts with runtime-stamped hidden keys
- status: open
- severity: P2 · effort: M · confidence: Plausible
- dimension: design
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/legacy.py:6730 — validator mutates input; :7521-7530 stamps `_scenario_id`/`_severity`/`_is_cascade`
- why: the most-touched extension point is the only untyped concept; contradictory mutation policies.
- fix: frozen AnomalySpec/CascadeSpec; validator becomes a pure parser (decomp step 9 vehicle).

## A-006 — Generator calling convention inferred from signature introspection
- status: open
- severity: P2 · effort: M · confidence: Plausible
- dimension: design
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/legacy.py:2121-2283 — arity metadata, dual dispatchers, traceback-depth heuristic
- why: a defaulted third param silently changes the call shape; heavy defensive machinery.
- fix: explicit opt-in (wrappers or generator_args); introspection as deprecation shim only.

## A-007 — Run config crosses CLI/server boundary as untyped Namespace with re-hardcoded defaults
- status: open
- severity: P2 · effort: M · confidence: Plausible
- dimension: design
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/server.py:1659 — getattr(...,"seed",42) duplicates DEFAULT_SEED; ~15 such sites
- why: parallel default definitions mask attribute drift with stale literals.
- fix: frozen RunConfig from _reconcile_cli_surface; defaults via DEFAULT_* constants.

## A-008 — stream_otel_signals endpoint/auth assembly duplicated in both callers
- status: open
- severity: P2 · effort: S · confidence: Plausible
- dimension: design
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/legacy.py:9104-9126 vs server.py:1716-1734 — near-identical ritual, subtly diverged
- why: next OTEL flag change must be made twice or drift.
- fix: shared builder in otel_stream consumed by both.

## A-009 — combine_logs / combine_logs_unified take the same leading params in opposite order
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: design
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/combine_impl.py:407 vs :56-58 — both facade-exported
- why: sibling swap mis-binds a path string into components.
- fix: keyword-only params on the unified form; consider demoting from __all__.

## A-010 — Cross-module leaf API spelled underscore-private
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: design
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - artifacts.py:27, timeutil.py:18, csv_layout.py:32/224/331 — load-bearing `_` names, no __all__
- why: real contracts advertise instability.
- fix: public aliases + __all__; underscore names stay compat bindings.

## A-011 — validate_output failure model is prose strings that consumers substring-parse
- status: fixed
- severity: P3 · effort: M · confidence: Plausible
- dimension: design
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-18 @ pending-pr
- evidence:
  - src/anomaly_metric_creator/validate_impl.py — `Violation` carries component/metric/kind/message and `validate_output` returns `list[Violation]`
  - tests/test_validate_output.py — structured violation coverage and string-compatible classifier path
- why: fixed; consumers can inspect fields while CLI/string output stays byte-compatible.
- fix: frozen `Violation` whose `__str__` reproduces prose byte-for-byte.
- notes: fixed by task 07-06-validate-impl-split-and-cleanup (2026-07-18)

## A-012 — SimulationClock.resume() on a running clock silently rewinds simulated time
- status: open
- severity: P2 · effort: S · confidence: Plausible
- dimension: correctness
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/server_ops.py:861-865 — no `if self._paused` guard (reviewer reproduced live)
- why: a retried resume discards elapsed sim time for every clock consumer.
- fix: guard so resume on a running clock is a no-op.

## A-013 — Command-mode kubectl delete/scale/patch succeed on nonexistent resources and pollute the overlay
- status: open
- severity: P2 · effort: M · confidence: Plausible
- dimension: correctness
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/server_ops.py:3320-3400 — no snapshot existence checks; scale defaults to apigateway (reproduced); API path 404s correctly
- why: two entry points to one simulated cluster disagree about existence.
- fix: snapshot check before mutation; NotFound CommandResult on miss; nameless scale = usage error.

## A-014 — state.otel_status unsynchronized dict serialized live by /v1/state
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: correctness
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/server_ops.py:959,986 — live ref in summary(); daemon threads insert keys (server.py:1700-1710)
- why: dict resize during json.dumps → transient 500 on the polled endpoint.
- fix: lock + copy in summary(), or pre-seed keys.

## A-015 — Failed continuous-generation pass leaves memory/disk split-brain
- status: open
- severity: P3 · effort: M · confidence: Plausible
- dimension: correctness
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/server.py:1664-1674 — failure path skips replace_generated_rows after partial atomic swaps
- why: /v1/anomalies serves old generation while MCP tools read new-seed files.
- fix: reload rows from disk on failure or surface the inconsistency.

## A-016 — _iter_component_rows PEP-479 RuntimeError on zero-byte CSV
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: correctness
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/csv_layout.py:82 — bare next(reader) in a generator (reproduced); siblings guard
- why: empty-file debris kills the whole merge with a raw traceback.
- fix: next(reader, None) + skip with warning.

## A-017 — Negative ?limit= inverts or unbounds trace listing
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: correctness
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/server_traces.py:174-175 slice inversion; :538-540 SQLite LIMIT -3 = unlimited
- why: limit means different things per backend; -1 dumps the table.
- fix: clamp in CommandTraceStore.list.

## A-018 — CSV formula injection in trace-bundle export-csv
- status: open
- severity: P2 · effort: S · confidence: Plausible
- dimension: security
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/trace_bundle.py:208-234 — free-text cells unneutralized; shlex.join passes `=1+1` through (server_ops.py:4804)
- why: attacker-recorded traces execute formulas in the operator's spreadsheet.
- fix: apostrophe-prefix leading `= + - @ \t \r` in free-text cells.

## A-019 — --cors-allow-origin '*' reflects to any origin on a no-auth bind
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: security
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/server.py:933-943 — unconditional `*` reflection
- why: any visited website can read rubric/debug surfaces cross-origin.
- fix: refuse/warn on `*` without auth, or exclude rubric//v1/debug surfaces.

## A-020 — serve_main composition (incl. --mcp-eval-mode → eval_mode wire) never executed by any test
- status: fixed
- severity: P1 · effort: M · confidence: Verified
- dimension: testing
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-21 @ pending-pr
- evidence:
  - tests/test_serve_main_wiring.py — runs the real `serve_main` composition past validation, asserts both eval-mode flag states reach `build_state`, and checks all eight `ServerSecurityConfig` fields plus worker/SSE passthrough and cleanup.
- why: fixed; removing the `eval_mode` kwarg or swapping two security mappings now fails a focused mutation-sensitive test while existing handler tests retain the live HTTP wall contract.
- fix: focused `serve_main` wiring tests with in-memory state/server doubles; `_generation_argv_without_otel` remains covered elsewhere.

## A-021 — Ground-truth-wall leak sweeps hand-enumerate MCP tools and lag the registry (3/15, 9/15)
- status: fixed
- severity: P1 · effort: M · confidence: Verified
- dimension: testing
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-21 @ pending-pr
- evidence:
  - tests/test_server_eval_mode.py — `_TOOL_MINIMAL_ARGS` is asserted equal to `MCP_TOOLS`; every tool call succeeds and is serialized in eval and non-eval modes, with the non-eval ConfigMap path proving the sweep observes active slugs.
- why: fixed; a new tool without schema-valid sweep arguments fails loudly, and every registered response participates in the live leak test.
- fix: registry-driven sweep with per-tool args table keyed equal to MCP_TOOLS, both modes.

## A-022 — Real kubectl/Helm 4 interop smokes permanently skipped; CI never runs them
- status: fixed
- severity: P2 · effort: M · confidence: Plausible
- dimension: testing
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-20 @ pending-pr
- evidence:
  - `.github/workflows/ci.yml`; `tests/test_server.py`; `tools/check_ci_review_contract.py` — the full light lane installs checksum-pinned kubectl and Helm binaries and runs both opt-in real-client smokes serially; the deterministic guard pins the workflow contract.
- why: fixed; the headline real-client guarantee is exercised by the full CI lane with reproducible official binaries.
- fix: full-lane CI step with pinned binaries running the two smokes.

## A-023 — Heavy-marker fixture-name registries unvalidated against real fixtures
- status: fixed
- severity: P3 · effort: S · confidence: Plausible
- dimension: testing
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-20 @ pending-pr
- evidence:
  - `tests/test_heavy_marker.py` resolves every declared heavy fixture name through pytest's fixture manager after full-suite collection.
- why: fixed; a stale registry name now fails before it can silently route a GB-scale fixture into the parallel lane.

## A-024 — Debug-UI tests assert substring presence only; JS never executed
- status: fixed
- severity: P3 · effort: M · confidence: Plausible
- dimension: testing
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-20 @ pending-pr
- evidence:
  - `tests/test_debug_ui_javascript.py` extracts every embedded script from `DEBUG_HTML` and runs `node --check`, with an explicit skip when Node is unavailable.
- why: fixed; JavaScript syntax errors fail locally and in CI environments that provide Node.

## A-025 — Gauge-stream wall-clock pacing assertion is a flake candidate under loaded xdist
- status: fixed
- severity: P3 · effort: S · confidence: Plausible
- dimension: testing
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-20 @ pending-pr
- evidence:
  - `tests/test_otel_gauges.py` captures requested pacing sleeps while retaining 24 real mock-collector HTTP round trips.
- why: fixed; the pacing contract is exact and independent of runner scheduling jitter.

## A-026 — SECURITY.md describes superseded redaction posture as current; cites completed task as pending
- status: open
- severity: P2 · effort: S · confidence: Plausible
- dimension: documentation
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - SECURITY.md:86-91 vs redaction.py:17-58 — shipped mask-unless-known-safe contradicts the doc
- why: trust-model doc makes a false credential-handling claim; contradicts CLAUDE.md.
- fix: rewrite to the shipped dual posture; drop the task pointer.

## A-027 — Copilot review instructions present five removed CLI flags and removed mode as current
- status: open
- severity: P2 · effort: S · confidence: Plausible
- dimension: documentation
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - .github/instructions/anomaly-metric-creator.instructions.md:121-187 — none of the named flags parse at HEAD
- why: automated reviews gate new flags against nonexistent ones (#44 class).
- fix: replace with canonical surface; add contract anchors.

## A-028 — pyproject dev-extra comments claim report-only mypy/coverage (three reviewer sightings)
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: documentation
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - pyproject.toml:55-60 vs ci.yml:346 (gate) and :398 (--cov-fail-under=85); archived task id cited
- why: pin rationale describes a superseded CI posture.
- fix: update both comments + task pointer.

## A-029 — CLAUDE.md names `test` as the branch-protection context, contradicting `CI Result`
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: documentation
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - CLAUDE.md:2453-2454 vs :2469-2470 and docs/DEVELOPMENT_CYCLE.md:134
- why: re-configuring protection per the stale line drops the Socket verdict.
- fix: reword to "application aggregate feeding the required CI Result".

## A-030 — README enumerates only half the dev extra
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: documentation
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - README.md:1177-1178 vs pyproject.toml:44-62 — omits mypy, pytest-cov, protobuf pair, pyyaml
- why: exhaustive-looking list is incomplete.
- fix: complete or reword.

## A-031 — SQLite trace INSERT block duplicated verbatim in two store methods
- status: open
- severity: P2 · effort: S · confidence: Plausible
- dimension: bloat
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/server_traces.py:442 vs :691 — identical ~55-line INSERT + FTS blocks
- why: schema changes must be edited twice; a miss breaks insert or import silently.
- fix: extract _insert_trace_row(conn, trace, *, delete_fts_first).

## A-032 — 22 inline copies of the OTLP capture-server harness in test_cli.py
- status: open
- severity: P2 · effort: M · confidence: Plausible
- dimension: bloat
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - tests/test_cli.py:362-1627 — 22 _Handler classes + identical scaffolds; test_otel_gauges.py:33 has the reusable model
- why: ~500 lines of boilerplate; fixes fan out ×22.
- fix: conftest capture_otlp_server fixture; keep divergent handlers as variants.

## A-033 — Topology test harness quadruplicated; hand-coded exclusion windows drifted
- status: open
- severity: P2 · effort: M · confidence: Plausible
- dimension: bloat
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - _column_values ×4 across test_topology_*.py; _EXCLUSION_WINDOWS lists differ while test_topology_llm derives from SCENARIOS
- why: scenario re-tunes silently rot the hard-coded lists.
- fix: helpers to conftest; generalize the catalog-derived windows.

## A-034 — 1,689-line approval-duplicate gate wired into nothing
- status: fixed
- severity: P2 · effort: S · confidence: Plausible
- dimension: bloat
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-08-04 @ pending-pr
- evidence:
  - tools/check_approval_duplicate.py + tests — referenced only by CHANGELOG/CLAUDE.md; absent from hooks/workflows/agent trees/spec
- why: fixed; wired (Option A-lite) via the canonical `tools/pr_comment.sh` wrapper (role-name → approval-duplicate → `gh pr comment`), pointed at from both CLAUDE.md chain snippets and recorded in `.trellis/spec/amc/backend/documentation-review.md`, so the gate now has a live enforcement path.
- fix: wire where comments are posted, or retire and record the decision.

## A-035 — classify_ci_changes.sh shim never executed but carried in three allowlists
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: bloat
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - scripts/classify_ci_changes.sh:1-5 — forward-only shim; full-check resolver never reaches it here
- why: zero-caller wrapper threaded through config regexes.
- fix: delete shim + references.

## A-036 — Dead temp_output_dir() + sole-purpose tempfile import in server.py
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: bloat
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/server.py:1790-1791 — only reference is the definition
- why: dead code in the facade suggests a live surface.
- fix: delete function + import.

## A-037 — Lint-tool boilerplate copy-pasted across check-script family and 15+ test files
- status: open
- severity: P3 · effort: M · confidence: Plausible
- dimension: bloat
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - _read/_require_contains identical in two contract checkers; ~6-line _run re-defined in 15+ lint tests
- why: fixes fan out to every copy; shapes already vary.
- fix: conftest run_tool() helper; optional shared lib for the contract siblings.

## A-038 — Dead RESOURCE_KINDS constant in debug-UI JS
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: bloat
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/server_debug_ui.py:483 — declared, never read
- why: dead snapshot of a server-side registry; drift bait if revived.
- fix: delete; fetch from /v1/debug/resources if needed.

## A-039 — MCP analysis tools re-scan and parse every CSV row per call; window never prunes
- status: open
- severity: P2 · effort: M · confidence: Plausible
- dimension: performance
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/server_mcp.py:252-262/392-400/496-504 — parse-before-filter, no break on sorted input; measured 0.137s/component regardless of window (~2s per timeline call)
- why: primary consumers pay full 700k-row parses for narrow queries; multiplies under concurrent agents.
- fix: lexicographic string window bounds before strptime + break past `to`; hoist column index.

## A-040 — unsupported_summary() deserializes full non-supported history per call; UI polls it 2×/1.5s
- status: open
- severity: P2 · effort: M · confidence: Plausible
- dimension: performance
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/server_traces.py:198-204 fetchall + per-row from_dict; server_ops.py:985 uses it for a len(); debug UI setInterval 1500
- why: with SQLite persistence the poll cost grows unbounded for the server's life.
- fix: SQL GROUP BY aggregation or cache keyed on store version.

## A-041 — record() rebuilds persistence resources per trace; JSONL write under the main lock
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: performance
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/server_traces.py:155-166/320-325/442-502 — per-insert connect + retention query + FTS rewrite
- why: every request pays connection setup; workers serialize on disk I/O.
- fix: long-lived connection + persistent handle; retention every N inserts.

## A-042 — resource_snapshot() recomputes component-invariant lists once per pod
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: performance
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - src/anomaly_metric_creator/server_ops.py:2022-2056 — per-replica calls to per-component helpers (lock each)
- why: ~280 redundant builds per snapshot per request at max fan-out.
- fix: hoist per-component values above the replica loop.

## A-043 — .opencode/package.json declares an unused, unpinned npm dependency with no lockfile
- status: open
- severity: P2 · effort: S · confidence: Plausible
- dimension: dependencies
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - .opencode/package.json:3 — `^1.14.39`, zero imports anywhere, no lockfile, dedicated Dependabot entry
- why: the only dependency resolving differently tomorrow; runtime auto-installs it; buys nothing.
- fix: remove (plus the Dependabot entry) or pin exactly + commit a lockfile.

## A-044 — mypy== and CI socketsecurity== pins have no automated update path
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: dependencies
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - pyproject.toml:58; ci.yml:483 — lockfile-only strategy cannot move manifest == pins
- why: both silently age with no signal.
- fix: periodic-bump checklist or Dependabot-visible pin location.

## A-045 — Vendored security-best-practices skill has no upstream provenance or refresh story
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: dependencies
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - five byte-identical copies; absent from any provenance manifest
- why: currency of vendored security guidance is unknowable.
- fix: record upstream URL + release; note the refresh procedure.

## A-046 — Declared version floors predate the py3.14 policy (numpy>=1.26 unsatisfiable on 3.14)
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: dependencies
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - pyproject.toml:13,22-24 vs uv.lock (numpy 2.5.1, protobuf 7.35.1)
- why: floors advertise support never tested or installable.
- fix: raise floors deliberately to the oldest exercised combination.

## A-047 — Non-full-ci labeled event lets auto-merge land on quick-lane evidence
- status: fixed
- severity: P2 · effort: S · confidence: Plausible
- dimension: tooling
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-20 @ pending-pr
- evidence:
  - `.github/workflows/ci.yml` — the labeled arm honors `PR_AUTO_MERGE` as well as the one-shot `full-ci` label.
  - `tools/check_ci_review_contract.py`; `tests/test_ci_review_contract.py` — a named anchor and mutation test fail if the armed-PR clause is removed.
- why: fixed; later label events on an armed PR keep the full-matrix gate.

## A-048 — CI mypy clean-module gate has no local counterpart
- status: fixed
- severity: P2 · effort: S · confidence: Plausible
- dimension: tooling
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-20 @ pending-pr
- evidence:
  - `tools/check_mypy_gate.py`; `.github/workflows/ci.yml`; `scripts/check-review-preflight.mjs` — one executable owner runs the 19 clean modules in CI and local preflight.
  - `tests/test_mypy_gate_lint.py` — pins the module set and rejects a second inline workflow list.
- why: fixed; clean-module type regressions now fail locally and remotely from one list.

## A-049 — Pack payload not absorbed into classifier/syntax gates (full-suite waste; toolchain.sh ungated)
- status: fixed
- severity: P2 · effort: S · confidence: Plausible
- dimension: tooling
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-20 @ pending-pr
- evidence:
  - `scripts/classify-ci-changes.sh`; `tests/test_ci_change_classifier.py` — `.sd-ai-command-pack/**` and `.trellis/audit/**` are lightweight, with dependency/workflow escalation preserved.
  - `.github/workflows/ci.yml`; `.pre-commit-config.yaml`; `tools/check_ci_review_contract.py` — both shell gates cover the toolchain and shared library, and Python syntax includes `scripts/*.py`.
- why: fixed; refresh/audit-only diffs stay cheap while command-pack entrypoints are parsed locally and remotely.

## A-050 — Local full-check's Python checks silently vanish without Node.js
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: tooling
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-20 @ 87dbd79
- evidence:
  - `scripts/sd-ai-command-pack-full-check.sh`; `.sd-ai-command-pack/provenance.json` — the affected wrapper is a pack-vouched installed target; a consumer-only edit would fail the install audit.
- why: remains open; implement in the upstream SD command pack and refresh the consumer through the installer so provenance stays truthful.

## A-051 — workflow_dispatch cannot force the full matrix it is documented to run
- status: fixed
- severity: P3 · effort: S · confidence: Plausible
- dimension: tooling
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-20 @ pending-pr
- evidence:
  - `.github/workflows/ci.yml` — manual dispatch appends `--force-app` to the shared classifier invocation.
  - `tools/check_ci_review_contract.py`; `tests/test_ci_review_contract.py` — the dispatch-specific force-app path is contract-pinned.
- why: fixed; manual dispatch makes the full application lane eligible even for a lightweight tip diff.

## A-052 — Role-name-leak CI mirror scans far less than the pre-commit hook
- status: fixed
- severity: P3 · effort: S · confidence: Plausible
- dimension: tooling
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-20 @ pending-pr
- evidence:
  - `.github/workflows/ci.yml`; `tests/test_role_name_leaks_lint.py` — CI and the live-tree regression scan cover `src/`, `scripts/`, `.agents/`, and `.trellis/`.
- why: fixed; those tracked trees are scanned before every application lane.

## A-053 — Lightweight lane runs repo lints under unpinned system Python
- status: fixed
- severity: P3 · effort: S · confidence: Plausible
- dimension: tooling
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-20 @ pending-pr
- evidence:
  - `.github/workflows/ci.yml` — lightweight guards install pinned `setup-uv` and invoke every Python guard through managed Python 3.14 with `--no-project`.
  - `tools/check_ci_review_contract.py`; `tests/test_ci_review_contract.py` — the managed-runtime command is contract-pinned and mutation-tested.
- why: fixed; the cheap lane now executes syntax and contract guards under the repository's declared Python version.

## A-054 — Five weeks of features + breaking requires-python raise unreleased under tagged 0.3.0
- status: fixed
- severity: P1 · effort: S · confidence: Verified
- dimension: release-hygiene
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-21 @ pending-pr
- evidence:
  - `pyproject.toml`; `uv.lock`; `CHANGELOG.md` — the release PR aligns version 0.4.0 across package metadata, the locked editable project, and a dated release section that names the Python 3.11→3.14 break.
  - `.trellis/tasks/07-17-audit-cut-release-0-4-0/implement.md` — the explicit post-merge tag, GitHub Release, and tag-install verification sequence is part of the release closeout.
- why: fixed; 0.4.0 is cut from the installable package tree with the breaking floor change named in release notes.
- fix: cut 0.4.0 now; bump version in the same PR as future floor/surface changes.

## A-055 — No documented versioning scheme or release-gate policy
- status: fixed
- severity: P2 · effort: S · confidence: Plausible
- dimension: release-hygiene
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-21 @ pending-pr
- evidence:
  - `docs/DEVELOPMENT_CYCLE.md` — documents the 0.x versioning policy and exact release/tag/install sequence.
  - `.trellis/spec/amc/backend/testing-quality.md`; `.github/PULL_REQUEST_TEMPLATE.md`; `CLAUDE.md`; `.github/instructions/anomaly-metric-creator.instructions.md` — the changelog/version-impact review heading is lockstep-guarded.
- why: fixed; release mechanics and version-impact review are now durable, task-loadable contracts.
- fix: Releasing section + checklist heading in three-way lockstep.

## A-056 — CHANGELOG Unreleased omits user-facing fixes incl. the redaction posture flip
- status: fixed
- severity: P2 · effort: S · confidence: Plausible
- dimension: release-hygiene
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-21 @ pending-pr
- evidence:
  - `CHANGELOG.md` — 0.4.0 carries Security/Fixed entries for #213 response-header redaction, #134 combined-artifact allowlisting, and #128 file-descriptor preflight.
- why: fixed; the upgrader-facing fixes are included in the promoted 0.4.0 notes.
- fix: backfill before/with the 0.4.0 cut.

## A-057 — No runtime version surface (--version / __version__)
- status: fixed
- severity: P3 · effort: S · confidence: Plausible
- dimension: release-hygiene
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-21 @ pending-pr
- evidence:
  - `src/anomaly_metric_creator/version.py`; `src/anomaly_metric_creator/cli_args.py`; `src/anomaly_metric_creator/__init__.py` — one metadata owner feeds `--version` and `__version__`.
  - `tests/test_cli.py`; `tests/test_version.py` — installed-version output and source-tree fallback behavior are covered.
- why: fixed; deployed installs expose their package version through CLI and Python APIs.
- fix: --version + __version__ from importlib.metadata.

## A-058 — Test-resource-cost rules remain prose despite the repo's lints-over-prose policy
- status: fixed
- severity: P2 · effort: M · confidence: Plausible
- dimension: improvements
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-20 @ pending-pr
- evidence:
  - `tools/check_test_resource_cost.py`; `.pre-commit-config.yaml`; `.github/workflows/ci.yml`; `tests/test_test_resource_cost_lint.py` — AST guard, reviewed exemptions, and local/CI wiring.
- why: fixed; executable whole-file reads now fail mechanically unless explicitly reviewed as bounded artifacts.

## A-059 — Canonical README scenario catalog has no sync check against SCENARIOS
- status: fixed
- severity: P2 · effort: S · confidence: Plausible
- dimension: improvements
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-20 @ pending-pr
- evidence:
  - `tests/test_readme_scenario_catalog.py` parses the canonical table and compares slug, severity, days, and ordered components bidirectionally with `SCENARIOS`.
- why: fixed; mechanically derivable catalog fields can no longer drift silently.

## A-060 — Three pre-commit lints have no CI mirror
- status: fixed
- severity: P3 · effort: S · confidence: Plausible
- dimension: improvements
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-20 @ pending-pr
- evidence:
  - `.github/workflows/ci.yml`; `tools/check_ci_review_contract.py` — the always-run changes job invokes role-name, AMC-module-load, and agent-hook-exception guards under managed Python.
- why: fixed; quick/full lane selection cannot bypass these sub-second guards.

## A-061 — Branch-name lint's refspec bypass closable in CI
- status: fixed
- severity: P3 · effort: S · confidence: Plausible
- dimension: improvements
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-20 @ pending-pr
- evidence:
  - `.github/workflows/ci.yml`; `tools/check_ci_review_contract.py` — the changes job passes `github.head_ref` to the existing branch checker and contract-pins the source.
- why: fixed; the published PR head ref is checked even when local refspec feedback is bypassed.

## A-062 — Role-name lint's commit-message surface unwired
- status: fixed
- severity: P3 · effort: S · confidence: Plausible
- dimension: improvements
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-20 @ pending-pr
- evidence:
  - `.pre-commit-config.yaml`; `README.md`; `docs/DEVELOPMENT_CYCLE.md` — the role-name checker runs at `commit-msg` and the one-time installation is documented.
- why: fixed; locally authored commit messages receive the same structural scan as tracked text.

## A-063 — sd-ai-command-pack refresh is a recurring manual chore (17/564 commits)
- status: fixed
- severity: P3 · effort: M · confidence: Plausible
- dimension: improvements
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-21 @ c38f0f7
- evidence:
  - `.github/workflows/sd-ai-command-pack-sync.yml`; `tools/check_ci_review_contract.py`; PRs #267, #268, and #269 — weekly/manual canonical refresh, no-diff PR suppression, the fixed automation branch, scoped-token writes, and normal gated auto-merge are shipped and exercised by genuine refreshes.
  - GitHub Actions runs `29785268662` and `29791043370` — both post-merge no-change dispatches succeeded without creating the automation branch or a pull request; the final 0.24.7 run reported `pull-request-operation = none`.
- why: fixed; real refreshes use reviewable pull requests and the shipped no-change path has twice demonstrated no branch or PR side effects.
- fix: scheduled sync workflow, PR-on-change, reuse the auto-merge gate.

## A-064 — Dev-install docs teach unlocked pip while CI enforces the uv lock
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: improvements
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - README.md:91-92,1181-1182 vs ci.yml:235 --locked
- why: local envs legitimately diverge from what CI tests.
- fix: uv sync --locked as the primary instruction.

## A-065 — Windows guard discipline paid for but never exercised
- status: fixed
- severity: P3 · effort: M · confidence: Plausible
- dimension: improvements
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-21 @ c38f0f7
- evidence:
  - `.github/workflows/ci.yml`; `tools/check_ci_review_contract.py`; GitHub Actions run `29790018855` — the locked Python 3.14 `windows-latest` collect-only job passed in 31 seconds on PR #269 and remains excluded from the required `test` and `CI Result` dependency lists.
- why: fixed; Windows collection now runs successfully on pull requests while remaining advisory and unable to make the aggregate required context red.
- fix: cheap windows-latest collect-only job.

## A-066 — Documented eval recipe loses the harness's trace evidence
- status: open
- severity: P2 · effort: S · confidence: Plausible
- dimension: consumer-impact
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - server.py:69,553-557,613-614 — /v1/debug/* (incl. export) 404 in eval mode; README eval recipe passes no persist flags
- why: a recipe-following harness discovers post-run its scoring evidence is unrecoverable.
- fix: add --persist-command-db to the recommended command; document persistence as the only trace path in eval mode.

## A-067 — K8s facade advertises v1.29.4, outside supported skew for mid-2026 clients
- status: fixed
- severity: P3 · effort: M · confidence: Plausible
- dimension: consumer-impact
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-20 @ pending-pr
- evidence:
  - `src/anomaly_metric_creator/server_ops.py`; `tests/test_server.py`; `.github/workflows/ci.yml`; `README.md` — all advertised version surfaces derive from v1.36.2, matching the pinned kubectl v1.36.2 smoke client; the smoke rejects version-skew warnings.
- why: fixed; one advertised-version source and a same-minor real-client smoke prevent silent skew drift.
- fix: single version constant, bump, re-run real-client smokes.

## A-068 — amc validate hard-fails on foreign files generation tolerates
- status: fixed
- severity: P3 · effort: S · confidence: Plausible
- dimension: consumer-impact
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-18 @ pending-pr
- evidence:
  - src/anomaly_metric_creator/validate_impl.py — `_validate_no_unknown_files` skips dot-prefixed sidecars but still flags undeclared non-dot files
  - tests/test_validate_output.py — `.DS_Store` passes; `apigateway.csv.tmp` still fails
- why: fixed; generation pre-clean and validator unknown-file policy now agree on dotfile sidecars.
- fix: dotfile exemption with hard-fail retained for non-dot unknown files.
- notes: fixed by task 07-06-validate-impl-split-and-cleanup (2026-07-18)

## A-069 — MEZMO_OTEL_STREAM_AUTH_SCHEME missing from the README env contract
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: consumer-impact
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - legacy.py:7990-7992 vs README's seven documented MEZMO_OTEL_* vars
- why: one member of an explicit compatibility family is invisible.
- fix: add the row to the OTEL table.

## A-070 — trace-bundle rejects non-current schema_version with no compat reader
- status: open
- severity: P3 · effort: S · confidence: Plausible
- dimension: consumer-impact
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - trace_bundle.py:57-67 — hard != rejection
- why: the first version bump orphans every archived bundle.
- fix: decide + document N-1 adapter or matching-version policy.

## A-071 — Default serve posture discards unhandled-500 detail irrecoverably; request-plane silent
- status: open
- severity: P2 · effort: S · confidence: Verified
- dimension: observability
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - server.py:535-536 log_message no-op; :1541-1543 logger None by default; empirically 500 detail reaches no sink, not even the trace ring
- why: an auth-enabled operator still can't see brute-force attempts or 500 causes. (Refuted from P1: escaped exceptions do traceback via socketserver handle_error; 401/429 silence requires opted-in flags; silence is documented design.)
- fix: stderr fallback for the error-record arm when no logger; nudge --structured-log with hardening flags.

## A-072 — Background-thread failures recorded only into /v1/state; invisible under eval mode
- status: open
- severity: P2 · effort: S · confidence: Plausible
- dimension: observability
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - server.py:1669-1674,1704-1707; server_ops.py:3874-3880 — str(exc) only; /v1/state rubric-hidden in eval mode
- why: a failing regen loop serves stale data forever unobservably; SystemExit(2)'s diagnostic is "2".
- fix: WARNING + traceback tail to stderr/structured log in both arms.

## A-073 — PUT/PATCH/DELETE dispatch has no catch-all boundary
- status: open
- severity: P2 · effort: S · confidence: Plausible
- dimension: observability
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - server.py:750-803 — narrow handlers only, unlike do_GET/do_POST
- why: mutating-facade bugs drop the connection (reset, status 0) with no 500 or error record.
- fix: same except-Exception boundary, Status-shaped for API paths.

## A-074 — /readyz hardcodes ready and verifies nothing
- status: open
- severity: P2 · effort: S · confidence: Plausible
- dimension: observability
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - server.py:550-551; load_anomaly_rows silently returns [] on missing artifacts
- why: harness scripts gating on readyz get a false green under misconfig or failed regen.
- fix: reflect artifact presence + generation-thread health; 503 naming the dimension.

## A-075 — DoS-bound refusals (worker-cap 503, SSE 503, 429) counted nowhere
- status: open
- severity: P2 · effort: M · confidence: Plausible
- dimension: observability
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - server.py:251-269 raw refusal before a handler exists; no counters in state.summary()
- why: saturation is indistinguishable from network trouble; no sizing signal.
- fix: refusal counters in summary() + first-trip log line per window.

## A-076 — No boundary captures a stack trace; error records are type+message only
- status: open
- severity: P2 · effort: S · confidence: Plausible
- dimension: observability
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - server.py:1030-1034; server_mcp.py:1243-1253; no `import traceback` package-wide
- why: diagnosing server_ops failures requires local repro instead of reading the record.
- fix: traceback.format_exc() into the structured record + trace stderr; client bodies unchanged.

## A-077 — Structured request records carry no request/trace id join key
- status: open
- severity: P3 · effort: M · confidence: Plausible
- dimension: observability
- first-seen: 2026-07-17 @ b0df00b
- last-seen: 2026-07-17 @ b0df00b
- evidence:
  - server.py:1049-1064 — no id field; CommandTrace ids never written into request records
- why: cross-sink incident reconstruction is timestamp guesswork.
- fix: per-request id minted in handle_one_request, threaded into trace recording.

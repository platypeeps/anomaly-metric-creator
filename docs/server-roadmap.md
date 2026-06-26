# Server Mode Roadmap And Handoff

This note captures the remaining work around the simulated SaaS server mode,
Kubernetes and Helm compatibility, continuous generation, mutable state, and
debugging UI. It is intended as a transfer note for a new agent session, not as
the authoritative architecture guide.

Authoritative implementation guidance now lives in
[.trellis/spec/amc/backend/index.md](../.trellis/spec/amc/backend/index.md),
especially [API, CLI, and Server](../.trellis/spec/amc/backend/api-cli-server.md)
and [Operations, Security, and Logging](../.trellis/spec/amc/backend/operations-security-logging.md).
[CLAUDE.md](../CLAUDE.md) remains expanded historical/source detail, and
user-facing usage remains in [README.md](../README.md).

## Current PR Snapshot

- PR: `https://github.com/platypeeps/anomaly-metric-creator/pull/137`
- Branch: `codex/complete-kubernetes-helm-ops`
- Latest local/pushed head at the time this note was written:
  `a3b0554c86c930253b3a3e13138418f42cdd0bdf`
- Local full validation after the last code change:
  `1305 passed, 2 skipped`
- The two skipped tests are real client smoke tests guarded by
  `AMC_RUN_REAL_CLIENT_SMOKE=1`.

The latest implemented PR-review fix sets `state.otel_status["thread"]` to
`"disabled"` when continuous generation is enabled but OTEL streaming is not.
That prevents `/v1/state` from reporting `not_started` for OTEL work that will
never run.

Before doing new feature work, refresh the live PR state:

```bash
git status -sb
gh pr view 137 --repo platypeeps/anomaly-metric-creator \
  --json number,title,url,headRefName,headRefOid,mergeStateStatus,reviewDecision,statusCheckRollup,latestReviews
python3 /Users/sven/.codex/plugins/cache/openai-curated-remote/github/0.1.5/skills/gh-address-comments/scripts/fetch_comments.py \
  --repo platypeeps/anomaly-metric-creator --pr 137
```

## Closeout Items For PR 137

1. Wait for CI and CodeQL to finish on the latest pushed head.
2. Review any new Copilot or code-quality comments that appear after
   `a3b0554`.
3. Several GitHub review threads are still unresolved in the UI even though
   code fixes and reply comments already landed in earlier commits. Treat these
   as UI housekeeping unless the latest thread state shows a new, non-outdated
   actionable comment.
4. Run the real-client smoke tests before merge if the environment has current
   `kubectl` and Helm available:

```bash
AMC_RUN_REAL_CLIENT_SMOKE=1 .venv/bin/pytest tests/test_server.py -q
```

5. Merge only after checks are green and there are no fresh actionable review
   findings.

Do not resolve GitHub review threads or post new review replies unless the user
explicitly asks for that write action.

## Implemented Baseline

The current branch already includes:

- `amc serve` with server-only flags and normal generator flag passthrough.
- Continuous generation with incrementing seeds, refreshed generated artifacts,
  refreshed anomaly rows, refreshed log-stream inputs, and serialized OTEL
  replay when OTEL streaming is enabled.
- Command API for simulated `kubectl` and Helm command responses.
- Kubernetes-compatible HTTP facade for real `kubectl` clients, including
  discovery, Table responses, core resources, selected workload APIs, metrics,
  authorization reviews, pod logs, and mutation status responses.
- Helm 4 compatibility through Helm-shaped release Secret objects.
- Scenario-specific Kubernetes and Helm behavior via `OPS_SCENARIO_PROFILES`.
- Mutable in-memory overlay for workload scale/restart/delete, generic
  resources, extra events, Helm revisions, Helm values, and reset.
- Command trace ring buffer, JSONL persistence, SQLite persistence, and search.
- Debug UI with command traces, unsupported backlog, resources, mutation state,
  generation/OTEL status, and scenario detail views.
- Security boundary for bearer auth, remote bind guardrails, body-size limits,
  and authenticated debug data requests.

## Roadmap: Compatibility Coverage

Add compatibility only when it can be backed by `resource_snapshot()` or the
`SimulationMutations` overlay. Avoid introducing a second Kubernetes state
model.

Good next command/API targets:

- `kubectl apply -f` for multi-document YAML or JSON payloads.
- `kubectl get --watch` and API watch semantics for a bounded simulated stream.
- Additional `kubectl logs` refinements if incident workflows need them, such
  as duration-based `--since`, timestamped output, and richer multi-container
  pod histories.
- `kubectl events` or richer event sorting/filtering if the installed client
  expects it.
- `kubectl rollout pause`, `resume`, and `undo`.
- More realistic `kubectl exec` command-specific outputs.
- More complete `kubectl port-forward` lifecycle behavior.
- Helm `lint`, `dependency`, `repo`, and chart metadata commands where they help
  common incident workflows.

Recently covered compatibility:

- `kubectl patch` in command mode for merge, strategic-merge, and focused JSON
  patch shapes backed by the simulator mutation overlay.
- `kubectl diff` and command-mode dry-run output for supported create/apply
  flows.
- Helm value layering for repeated `--set`, `--set-string`, and `--values`/`-f`
  inputs, plus simulated install/upgrade handling for `--atomic`, `--wait`,
  `--timeout`, `--reuse-values`, and `--reset-values`.
- `kubectl explain RESOURCE[.field]` for common simulator-backed resources and
  fields, plus minimal `/openapi/v2` and `/openapi/v3/...` schema endpoints for
  real `kubectl explain` clients.

Each new surface should add:

- parser coverage where needed,
- a supported or partial `CommandTrace`,
- scenario-appropriate stdout/stderr and exit code,
- real-client API behavior when applicable,
- unsupported-path trace coverage for nearby cases,
- focused tests in `tests/test_server.py`.

## Roadmap: Mutable State Semantics

The overlay now keeps in-memory mutation state across continuous generation
passes and exposes baseline-vs-overlay drift in `/v1/state` and the debug UI.
It also models Kubernetes-style `resourceVersion`, `generation`,
`observedGeneration`, deletion timestamps, namespace-scoped created/deleted
resource buckets, controller-style replacement pods, row-level selectors,
owner references, and repeated-event counts.

Remaining future work:

- Add optional persisted mutation state if workshops need restart continuity.
- Keep unsupported subresources rejected unless explicitly modeled.

## Roadmap: Debug UI And Analysis

The debug UI now includes the first analysis workflow pass:

- Command-trace JSON export and unsupported-backlog JSON/CSV export.
- A timeline view combining command traces, cluster/mutation events, generation
  passes, OTEL batches, and runtime refreshes.
- Baseline-vs-overlay resource diffs for workload overlays, deleted pods,
  replacement pods, Helm release state, and generic created/deleted resources.
- Copyable pytest snippets for unsupported command fingerprints.
- Global filters for scenario, resource kind, command family, support status,
  and time window across the main debug tables.
- Compact charts for generation count, anomaly count, OTEL batches, and command
  volume.
- A resource detail drawer that fetches the Kubernetes object payload returned
  to real clients when the selected resource has a fake API path.
- Visible live-runtime and cached-scenario-catalog status badges.

Remaining future work:

- Because the debug UI is inline HTML/CSS/JS in `server.py`, keep changes
  incremental and strongly tested through endpoint behavior. Consider
  extracting the debug shell only after PR 137 lands.

## Roadmap: Persistence And Search

SQLite trace persistence now records a schema version, uses FTS5-backed search
when available with a LIKE fallback, supports bounded retention via
`--persist-command-retention`, exports/imports trace histories as portable JSON,
has restart coverage for search/history continuity beyond the in-memory
ring size, and includes offline `amc trace-bundle` tooling for exported trace
bundles.

Completed follow-ups:

- `amc trace-bundle summary` reports support-status, command-family, scenario,
  and unsupported-fingerprint counts from a saved export.
- `amc trace-bundle search` reuses the server search filters against bundle
  JSON without a running simulator.
- `amc trace-bundle unsupported` groups partial/unsupported traces by
  fingerprint, and `amc trace-bundle export-csv` flattens traces for
  spreadsheets or workshop notes.

Remaining future work:

- No known Persistence/Search roadmap items remain beyond workshop-driven
  presentation polish.

## Roadmap: Security And Operations

Current security is suitable for local workshops and controlled demos. The
serve-mode security/ops hardening now covers:

- Reverse-proxy/TLS deployment guidance in the README while keeping the
  simulator bound to loopback.
- Explicit CORS via `--cors-allow-origin`, including unauthenticated preflight
  handling and access-control headers only for the configured origin or `*`.
- Trace redaction for bearer-token-like query params, passwords, secrets,
  client keys, and sensitive command flags before data reaches memory, JSONL,
  SQLite, or the debug UI.
- Optional per-client `--rate-limit-per-minute` enforcement for command and
  Kubernetes API endpoints, with JSON `429` app responses and Kubernetes
  `Status` API responses.
- Graceful shutdown signaling for continuous generation and long-lived SSE
  clients.
- Serve-mode JSON/YAML config files via `--config`, split into `server` and
  `generate` maps with explicit CLI flags overriding config defaults.
- Structured JSONL request/error logs via `--structured-log` and
  `--structured-log-file`, including redacted query values and auth
  present/absent status instead of bearer values.

Remaining follow-ups:

- No known Security/Operations roadmap items remain beyond workshop-driven
  operational polish.

## Roadmap: Architecture Cleanup

The behavior-preserving split is in place:

- `server_traces.py` owns `CommandTrace`, `CommandTraceStore`, JSONL/SQLite
  persistence, FTS/LIKE search, export/import payloads, and unsupported-summary
  grouping.
- `server_mutations.py` owns mutable overlay dataclasses, release overlay state,
  event coalescing, resource drift bookkeeping, and helper functions shared by
  snapshot rendering.
- `server_debug_ui.py` owns the inline debug shell HTML/CSS/JS. `server.py`
  still re-exports `DEBUG_HTML` for compatibility.
- `server_ops.py` owns scenario profiles, simulator state, command
  parsing/rendering, resource snapshots, Kubernetes-compatible API objects, and
  Helm release Secret encoding.
- `server_commands.py`, `server_kubernetes.py`, and `server_helm.py` expose
  focused command/API/Helm facets over the ops implementation so those roadmap
  boundaries stay explicit.
- `tests/test_server.py` includes an architecture-boundary regression test that
  pins the extracted modules behind the existing `server.py` facade.

Remaining extraction order:

- No known architecture cleanup extraction items remain. Keep future changes
  behavior-preserving and pinned by `tests/test_server.py`.

## Suggested Resume Prompt

Use this prompt when transferring to a new session:

```text
Continue work in /Users/sven/repos/personal/anomaly-metric-creator on PR 137.
Read AGENTS.md, .trellis/spec/amc/backend/index.md, the server-mode Trellis specs,
and docs/server-roadmap.md.
Check git status, PR 137 checks, and unresolved review threads. If CI is green,
close out review-thread housekeeping or run real-client smoke tests if asked.
For new roadmap work, keep server behavior backed by resource_snapshot() and
SimulationMutations, add focused tests in tests/test_server.py, then run the
server suite and full pytest before committing.
```

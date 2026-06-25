# Server Mode Roadmap And Handoff

This note captures the remaining work around the simulated SaaS server mode,
Kubernetes and Helm compatibility, continuous generation, mutable state, and
debugging UI. It is intended as a transfer note for a new agent session, not as
the authoritative architecture guide.

Authoritative implementation guidance remains in [CLAUDE.md](../CLAUDE.md),
especially the "Server mode and ops command simulation" section. User-facing
usage remains in [README.md](../README.md).

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

- `kubectl explain` for common resources and fields.
- `kubectl diff` and dry-run flows for supported generic resources.
- `kubectl patch` variants, including merge, strategic, and JSON patch shapes.
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
- Helm value layering for multiple `--set`, `--set-string`, and `--values`
  inputs.
- Helm `lint`, `dependency`, `repo`, and chart metadata commands where they help
  common incident workflows.
- Helm install/upgrade flags such as `--atomic`, `--wait`, `--timeout`,
  `--reuse-values`, and `--reset-values`.

Each new surface should add:

- parser coverage where needed,
- a supported or partial `CommandTrace`,
- scenario-appropriate stdout/stderr and exit code,
- real-client API behavior when applicable,
- unsupported-path trace coverage for nearby cases,
- focused tests in `tests/test_server.py`.

## Roadmap: Mutable State Semantics

The overlay works, but it is still intentionally lightweight. Improve it in
small steps:

- Preserve mutation overlays across continuous generation passes while clearly
  showing baseline-vs-overlay drift in `/v1/state` and the debug UI.
- Add optional persisted mutation state if workshops need restart continuity.
- Model Kubernetes `resourceVersion`, `generation`, `observedGeneration`, and
  deletion timestamps for mutated resources.
- Add namespace-aware overlay buckets instead of assuming a single namespace
  for all generic resources.
- Expand controller-style reconciliation for deleted pods and scaled workloads.
- Add owner references and selectors for created resources so `kubectl get all`
  and related list views feel more connected.
- Add event deduplication or event count semantics for repeated mutations.
- Keep unsupported subresources rejected unless explicitly modeled.

## Roadmap: Debug UI And Analysis

The debug UI is useful now, but the next high-value improvements are analysis
workflows:

- Export command traces and unsupported backlog results as JSON or CSV.
- Add a timeline view combining commands, mutation events, generation passes,
  OTEL batches, and log-stream refreshes.
- Add baseline-vs-overlay resource diffs for pods, deployments, Helm release
  history, and generic resources.
- Add "promote to test" affordances for unsupported command fingerprints,
  probably as copyable pytest snippets rather than automatic file writes.
- Add filters for scenario, resource kind, command family, support status, and
  time window across all debug tables.
- Add compact charts for generation count, anomaly count, OTEL batches, and
  command volume.
- Add a resource detail drawer that shows the exact Kubernetes object payload
  returned to real clients.
- Add clearer indication when the scenario catalog is cached and when runtime
  state is live.

Because the debug UI is inline HTML/CSS/JS in `server.py`, keep changes
incremental and strongly tested through endpoint behavior. Consider extracting
the debug shell only after PR 137 lands.

## Roadmap: Persistence And Search

SQLite search is in place. Possible follow-ups:

- Add FTS5-backed search when available, with a fallback to the current LIKE
  search.
- Add retention controls for persisted command traces.
- Add a schema/version migration path for the SQLite store.
- Add export/import of trace databases for offline debugging.
- Add tests that restart the server and verify search/history continuity over
  more than the in-memory ring size.

## Roadmap: Security And Operations

Current security is suitable for local workshops and controlled demos. Before
broader use:

- Document a reverse-proxy/TLS deployment recipe.
- Add explicit CORS behavior rather than relying on the default stdlib server
  behavior.
- Redact bearer tokens and sensitive values from traces and logs.
- Add optional rate limiting for command/API endpoints.
- Add graceful shutdown coverage for continuous generation and long-lived SSE
  clients.
- Add a config-file option if serve-mode command lines become unwieldy.
- Add structured server logs for request summaries and error paths.

## Roadmap: Architecture Cleanup

`server.py` is carrying a lot of responsibility. Avoid large refactors while PR
137 is under review. After merge, consider extracting modules in this order:

1. Command parsing and renderers.
2. Kubernetes API facade and object/table helpers.
3. Helm release/Secret encoding.
4. Mutable overlay state.
5. Trace persistence and search.
6. Debug UI shell.

Keep the public behavior pinned by `tests/test_server.py` before each
extraction. The goal is a behavior-preserving split, not a rewrite.

## Suggested Resume Prompt

Use this prompt when transferring to a new session:

```text
Continue work in /Users/sven/repos/personal/anomaly-metric-creator on PR 137.
Read AGENTS.md, CLAUDE.md server-mode guidance, and docs/server-roadmap.md.
Check git status, PR 137 checks, and unresolved review threads. If CI is green,
close out review-thread housekeeping or run real-client smoke tests if asked.
For new roadmap work, keep server behavior backed by resource_snapshot() and
SimulationMutations, add focused tests in tests/test_server.py, then run the
server suite and full pytest before committing.
```

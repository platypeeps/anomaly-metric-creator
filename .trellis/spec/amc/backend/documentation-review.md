# Documentation and Review

## Canonical Documentation Roles

Durable implementation and review conventions live in `.trellis/spec/`.
`AGENTS.md`, `CLAUDE.md`, GitHub/Copilot instructions, Claude/Codex/Gemini/
OpenCode files, and other platform entries are adapters or supporting source
documents. Sources: `AGENTS.md`; `CLAUDE.md`; `.trellis/workflow.md`;
`.github/instructions/anomaly-metric-creator.instructions.md`;
`.agents/`; `.codex/`; `.claude/`; `.gemini/`; `.opencode/`.

`README.md` and `docs/` own user-facing behavior: install, usage, CLI flags,
server endpoints, output files, topology prose, and application flow diagrams.
Keep them consistent with the implementation specs but do not turn them into a
duplicate agent rulebook. Sources: `README.md`;
`docs/application-flow.md`; `docs/topology.md`; `.trellis/tasks/`;
`.trellis/spec/amc/backend/`.

`CLAUDE.md` remains a valuable historical and expanded source document, but new
durable conventions should be added to Trellis first and only summarized or
linked from adapter docs when needed. Sources: `CLAUDE.md`; `AGENTS.md`;
`.trellis/spec/amc/backend/index.md`.

## Citation Rule

Every convention added to `.trellis/spec/` must cite supporting repo paths.
Prefer repo-relative paths; add line, symbol, or section detail when verified in
the current pass. Sources:
`.trellis/tasks/archive/2026-06/06-25-consolidate-agent-docs-trellis/prd.md`;
`.trellis/tasks/archive/2026-06/06-25-consolidate-agent-docs-trellis/design.md`;
`AGENTS.md`.

Generated/local runtime state such as `.trellis/.runtime/`, session journals,
and task archives should not be the only source for product conventions. Use
them as historical context, then verify against code, tests, docs, or active
specs before codifying a rule. Sources: `.trellis/workflow.md`;
`.trellis/workspace/`; `.trellis/tasks/`; `src/anomaly_metric_creator/`;
`tests/`; `README.md`.

## Docs Sync

Behavior changes must update every surface that describes the behavior:
docstrings, CLI help strings, README, `docs/*.md`, Trellis specs, and adapter
docs when those adapters mirror the changed convention. Sources: `CLAUDE.md`;
`README.md`; `docs/application-flow.md`; `docs/topology.md`;
`.trellis/spec/amc/backend/`; `.github/instructions/anomaly-metric-creator.instructions.md`.

When a default, precedence rule, count, edge list, dispatch order, artifact
name, flag, endpoint, or scenario changes, grep old and new wording across docs
and help text rather than relying on the touched file alone. Sources:
`CLAUDE.md`; `README.md`; `docs/application-flow.md`; `docs/topology.md`;
`src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/server.py`; `tests/`.

Magnitude and count prose must match executable data. Re-count scenario lists,
metric counts, flag counts, component sets, and workflow headings after adding
or removing entries. Sources: `CLAUDE.md`; `README.md`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_scenarios.py`;
`tests/test_registry.py`; `.github/PULL_REQUEST_TEMPLATE.md`.

## Backlog and Follow-Up Ownership

Trellis task records are the canonical home for planned implementation work,
backlog slices, and follow-up decisions. User-facing docs can describe current
capabilities and supported behavior, but they should not carry a parallel list
of future work once the item has been converted into `.trellis/tasks/`.
Sources: `.trellis/tasks/07-09-multi-instance-dst-splice-boundary/prd.md`;
`.trellis/tasks/06-29-server-watch-semantics/prd.md`;
`.trellis/tasks/06-29-helm-incident-command-coverage/prd.md`; `README.md`.

When consolidating older planning or handoff notes, map each still-relevant
item to an active or archived Trellis task, create a new task only for a
current-doc item that has no tracker, then remove stale file references and
future-work phrasing from docs, journals, and task context manifests. Do not
leave the same work item tracked in both a user-facing document and a Trellis
task. Sources: `.trellis/tasks/07-09-multi-instance-dst-splice-boundary/prd.md`;
`.trellis/tasks/archive/2026-06/06-25-consolidate-agent-docs-trellis/`;
`.trellis/tasks/archive/2026-06/06-26-server-compat-debug-polish/`;
`.trellis/workspace/sdelmas/journal-1.md`.

## Repository Map Artifact

`docs/repomix-map.md` is the generated Repomix repository map for quick human
or LLM orientation. Development agents should use it when it is present before
doing broad repo-shape searches, then verify details against source files,
tests, docs, and Trellis specs before making changes. Sources:
`docs/repomix-map.md`; `AGENTS.md`; `.trellis/spec/amc/backend/index.md`;
`.trellis/spec/guides/cross-layer-thinking-guide.md`.

Refresh the map with `scripts/update_repomix` whenever code, docs, tests,
scripts, or platform-adapter tree changes make the artifact stale. The script
is the canonical refresh command, writes `docs/repomix-map.md` in place, and
passes `--no-git-sort-by-changes` so identical repository contents retain
stable ordering instead of producing change-recency churn.
Sources: `scripts/update_repomix`; `README.md`; `docs/repomix-map.md`.

## PR and Review Surfaces

The PR template checklist mirrors the required review headings, including the
changelog/version-impact gate for user-visible or compatibility changes. If a
heading is renamed, added, or removed in the Trellis review spec, update
`.github/PULL_REQUEST_TEMPLATE.md` and Copilot instructions in the same diff.
Sources: `.trellis/spec/amc/backend/testing-quality.md`;
`.github/PULL_REQUEST_TEMPLATE.md`;
`.github/instructions/anomaly-metric-creator.instructions.md`; `CLAUDE.md`.

Copilot instructions should route reviewers to the relevant Trellis spec first,
then to source files/tests and supporting historical sections as needed. They
should not redefine project rules independently. Sources:
`.github/instructions/anomaly-metric-creator.instructions.md`;
`.trellis/spec/amc/backend/index.md`;
`tools/check_copilot_instruction_contract.py`; `CLAUDE.md`; `README.md`.

PR descriptions must name behavior changes, list the test plan, and walk the
review checklist before draft status is removed. Sources: `CLAUDE.md`;
`.github/PULL_REQUEST_TEMPLATE.md`;
`.trellis/spec/amc/backend/testing-quality.md`.

Behavior-changing diffs should use explicit scope sections in the PR body:
`Automation scope:`, `CI/review scope:`, `Tooling/generated scope:`,
`Docs/user-facing scope:`, or `Runtime/server scope:` as applicable.
`scripts/sd-ai-command-pack-pr-body-scope.py` enforces these sections with
repo-specific categories from `.sd-ai-command-pack/pr-body-scope.json` when a
PR body is supplied through `SD_AI_COMMAND_PACK_PR_BODY_SCOPE_PR_BODY`,
`SD_AI_COMMAND_PACK_SCOPE_PR_BODY`, or `--body-file`. Sources:
`scripts/sd-ai-command-pack-pr-body-scope.py`;
`.sd-ai-command-pack/pr-body-scope.json`; `tests/test_pr_body_scope_lint.py`;
`docs/DEVELOPMENT_CYCLE.md`.

The PR template should prompt for focused local checks, the local Trellis
full-check gate, and whether a remote `full-ci` label is needed. Review
guidance should prefer local evidence and the stable aggregate `test` context
before asking for repeated remote Copilot or Actions runs.
Sources: `.github/PULL_REQUEST_TEMPLATE.md`; `docs/DEVELOPMENT_CYCLE.md`;
`scripts/sd-ai-command-pack-full-check.sh`; `tools/check_ci_review_contract.py`;
`tools/check_copilot_instruction_contract.py`;
`scripts/sd-ai-command-pack-pr-body-scope.py`;
`scripts/sd-ai-command-pack-review-preflight.mjs`;
`scripts/check-review-preflight.mjs`;
`.github/copilot-instructions.md`;
`.github/instructions/anomaly-metric-creator.instructions.md`.

Recurring review lessons that are specific to AMC belong in
`docs/REVIEW_PATTERNS.md` or a mechanical `tools/check_*.py` guard with tests,
not only in PR comments. Sources: `docs/REVIEW_PATTERNS.md`;
`.trellis/spec/amc/backend/testing-quality.md`; `tools/`;
`tests/test_*_lint.py`.

Before opening housekeeping or finish-work PRs, fetch and compare against
`origin/main` so already-merged archive/journal commits do not become redundant
PRs. A publish flow should have a non-empty, non-duplicate branch diff before
creating a pull request. Sources: `.trellis/workflow.md`;
`.trellis/workspace/`; `.trellis/tasks/`; `CLAUDE.md`.

## Platform Adapter Policy

Retain existing Codex, Claude, GitHub/Copilot, Gemini, and OpenCode Trellis
files as platform adapters. They should teach each platform how to load
Trellis context, not carry separate project conventions. Sources: `.agents/`;
`.codex/`; `.claude/`; `.github/`; `.gemini/`; `.opencode/`;
`.trellis/workflow.md`; `.trellis/spec/amc/backend/index.md`.

Generated Trellis platform files may be updated by future `trellis update`
runs. Keep local project conventions in `.trellis/spec/` or a project-local
skill rather than patching every generated copy with durable project rules.
Sources: `.trellis/workflow.md`; `.agents/skills/trellis-meta/`;
`.claude/skills/trellis-meta/`; `.github/skills/trellis-meta/`;
`.opencode/skills/trellis-meta/`.

Python hook adapters must not catch `BaseException` or use bare `except`, and
intentional fail-open `except Exception: pass` handlers must include a short
comment explaining the suppression. Enforce this mechanically instead of
copying reviewer prose into each generated adapter. Sources:
`.codex/hooks/`; `.github/copilot/hooks/`; `.gemini/hooks/`;
`tools/check_agent_hook_exceptions.py`;
`tests/test_agent_hook_exception_lint.py`.

Codex inline mode skips sub-agent JSONL curation and loads task artifacts/specs
through `trellis-before-dev`; sub-agent-capable platform files still keep their
context-loading protocols. Sources: `.trellis/config.yaml`;
`.trellis/workflow.md`; `.agents/skills/trellis-before-dev/SKILL.md`;
`.codex/agents/trellis-implement.toml`; `.claude/agents/trellis-implement.md`;
`.gemini/agents/trellis-implement.md`; `.opencode/agents/trellis-implement.md`.

## Historical Notes

Completed Trellis tasks and workspace journals are useful evidence, but they
can become stale. Before treating older planning text as active work, verify it
against current source, tests, README, and open tasks. Sources:
`.trellis/tasks/`; `.trellis/workspace/`; `src/anomaly_metric_creator/`;
`tests/`; `README.md`.

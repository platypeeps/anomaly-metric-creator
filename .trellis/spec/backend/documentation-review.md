# Documentation and Review

## Canonical Documentation Roles

Durable implementation and review conventions live in `.trellis/spec/`.
`AGENTS.md`, `CLAUDE.md`, GitHub/Copilot instructions, Claude/Codex/Gemini/
OpenCode files, and other platform entries are adapters or supporting source
documents. Sources: `AGENTS.md`; `CLAUDE.md`; `.trellis/workflow.md`;
`.github/instructions/anomaly-metric-creator.instructions.md`;
`.agents/`; `.codex/`; `.claude/`; `.gemini/`; `.opencode/`.

`README.md` and `docs/` own user-facing behavior: install, usage, CLI flags,
server endpoints, output files, topology prose, application flow diagrams, and
roadmap/handoff notes. Keep them consistent with the implementation specs but
do not turn them into a duplicate agent rulebook. Sources: `README.md`;
`docs/application-flow.md`; `docs/topology.md`; `docs/server-roadmap.md`;
`.trellis/spec/backend/`.

`CLAUDE.md` remains a valuable historical and expanded source document, but new
durable conventions should be added to Trellis first and only summarized or
linked from adapter docs when needed. Sources: `CLAUDE.md`; `AGENTS.md`;
`.trellis/spec/backend/index.md`.

## Citation Rule

Every convention added to `.trellis/spec/` must cite supporting repo paths.
Prefer repo-relative paths; add line, symbol, or section detail when verified in
the current pass. Sources:
`.trellis/tasks/06-25-consolidate-agent-docs-trellis/prd.md`;
`.trellis/tasks/06-25-consolidate-agent-docs-trellis/design.md`;
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
`.trellis/spec/backend/`; `.github/instructions/anomaly-metric-creator.instructions.md`.

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

## PR and Review Surfaces

The PR template checklist mirrors the required review headings. If a heading is
renamed, added, or removed in the Trellis review spec, update
`.github/PULL_REQUEST_TEMPLATE.md` and Copilot instructions in the same diff.
Sources: `.trellis/spec/backend/testing-quality.md`;
`.github/PULL_REQUEST_TEMPLATE.md`;
`.github/instructions/anomaly-metric-creator.instructions.md`; `CLAUDE.md`.

Copilot instructions should route reviewers to the relevant Trellis spec first,
then to source files/tests and supporting historical sections as needed. They
should not redefine project rules independently. Sources:
`.github/instructions/anomaly-metric-creator.instructions.md`;
`.trellis/spec/backend/index.md`; `CLAUDE.md`; `README.md`.

PR descriptions must name behavior changes, list the test plan, and walk the
review checklist before draft status is removed. Sources: `CLAUDE.md`;
`.github/PULL_REQUEST_TEMPLATE.md`;
`.trellis/spec/backend/testing-quality.md`.

Before opening housekeeping or finish-work PRs, fetch and compare against
`origin/main` so already-merged archive/journal commits do not become redundant
PRs. A publish flow should have a non-empty, non-duplicate branch diff before
creating a pull request. Sources: `.trellis/workflow.md`;
`.trellis/workspace/`; `.trellis/tasks/`; `CLAUDE.md`.

The Trellis PR review loop is a project-local command surface. Keep the
canonical loop in `.agents/skills/trellis-review-pr/SKILL.md`; platform command
or prompt files should be thin entry points that load that skill rather than
duplicating the loop. Sources: `.agents/skills/trellis-review-pr/SKILL.md`;
`.gemini/commands/trellis/review-pr.toml`;
`.github/prompts/review-pr.prompt.md`;
`.opencode/commands/trellis/review-pr.md`.

## Platform Adapter Policy

Retain existing Codex, Claude, GitHub/Copilot, Gemini, and OpenCode Trellis
files as platform adapters. They should teach each platform how to load
Trellis context, not carry separate project conventions. Sources: `.agents/`;
`.codex/`; `.claude/`; `.github/`; `.gemini/`; `.opencode/`;
`.trellis/workflow.md`; `.trellis/spec/backend/index.md`.

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

## Historical and Roadmap Notes

Historical handoff files and completed Trellis tasks are useful evidence, but
they can become stale. Before treating roadmap text as active work, verify it
against current source, tests, README, and open tasks. Sources:
`docs/server-roadmap.md`; `.trellis/tasks/`; `.trellis/workspace/`;
`src/anomaly_metric_creator/`; `tests/`; `README.md`.

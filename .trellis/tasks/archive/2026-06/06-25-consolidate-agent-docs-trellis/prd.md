# Consolidate agent docs into Trellis specs

## Goal

Make `.trellis/spec/` the durable, cited source of truth for repository
development conventions, while keeping platform-specific agent files as thin
adapters or pointers where they are still useful. The consolidation must cover
architecture, design, coding standards, APIs, operational instructions,
documentation standards, testing standards, review/CI expectations, and
agent-workflow guidance.

## Background

- `AGENTS.md` currently points agents to `CLAUDE.md` as the authoritative guide
  and contains the managed Trellis block for `.trellis/` workflow/spec/task
  locations. Source paths: `AGENTS.md`, `CLAUDE.md`, `.trellis/workflow.md`.
- `CLAUDE.md` contains most of the current project-specific development
  guidance, including the canonical generator module, server module boundaries,
  SCENARIOS registry rules, pre-PR checklist, and validation expectations.
  Source path: `CLAUDE.md`.
- `README.md` and `docs/` contain user-facing usage, CLI/API behavior,
  topology, application flow, and server-mode material that must stay
  consistent with agent-facing implementation guidance. Source paths:
  `README.md`, `docs/application-flow.md`, `docs/topology.md`.
- GitHub and Copilot files define review, workflow, PR, and automation
  expectations that may overlap with Trellis guidance. Source paths:
  `.github/PULL_REQUEST_TEMPLATE.md`,
  `.github/instructions/anomaly-metric-creator.instructions.md`,
  `.github/workflows/ci.yml`, `.github/workflows/codeql.yml`,
  `.github/workflows/dependabot-auto-merge.yml`, `.github/workflows/socket.yml`,
  `.github/dependabot.yml`.
- Trellis has already been bootstrapped in the repository, with backend specs,
  guides, workflow instructions, agents, hooks, scripts, workspace notes, and
  this planning task. Source paths: `.trellis/spec/`, `.trellis/workflow.md`,
  `.trellis/agents/`, `.trellis/scripts/`, `.trellis/workspace/`,
  `.agents/skills/`, `.codex/`.
- Gemini and OpenCode files that already exist in the repository should be kept
  as thin Trellis adapters, not pruned during this task. Durable conventions
  should move into Trellis rather than being duplicated there. Source paths:
  `.gemini/`, `.opencode/`, `.trellis/spec/`.

## Requirements

1. Inventory all repository development-guidance files created for Codex,
   Claude, Copilot/GitHub, Trellis, Gemini, OpenCode, and related tooling.
   Source paths include: `AGENTS.md`, `CLAUDE.md`, `.agents/`, `.codex/`,
   `.claude/`, `.gemini/`, `.github/`, `.opencode/`, `.trellis/`.
2. Classify each discovered file as one of: durable project convention,
   platform-specific adapter, generated/local runtime state, historical
   planning/tracking material, or user-facing documentation. Source paths:
   `.trellis/workflow.md`, `.trellis/tasks/`, `.trellis/workspace/`,
   `.github/`, `.agents/`, `.codex/`, `.claude/`, `.gemini/`, `.opencode/`.
3. Consolidate durable conventions into `.trellis/spec/` in a structure that is
   easy for future agents to load before development. Source paths:
   `.trellis/spec/backend/index.md`, `.trellis/spec/guides/`,
   `.agents/skills/trellis-before-dev/SKILL.md`.
4. Every convention written into `.trellis/spec/` must cite the repository path
   that supports it. Prefer repo-relative paths, and add line, symbol, or
   section detail when it has been verified during the pass. Source paths:
   `AGENTS.md`, `CLAUDE.md`, `README.md`, `docs/`, `pyproject.toml`,
   `.pre-commit-config.yaml`, `.github/`, `tests/`, `src/`.
5. Remove or neutralize duplicated guidance by turning non-canonical
   platform-specific files into pointers/adapters where appropriate. Retain
   existing Gemini and OpenCode surfaces as thin Trellis adapters. Source
   paths: `AGENTS.md`, `.github/instructions/anomaly-metric-creator.instructions.md`,
   `.claude/`, `.gemini/`, `.opencode/`, `.codex/`.
6. Keep user-facing docs consistent with agent-facing specs, especially for CLI
   behavior, server/serve mode, topology, trace bundles, scenario semantics,
   validation commands, and output files. Source paths: `README.md`,
   `docs/application-flow.md`, `docs/topology.md`,
   `src/anomaly_metric_creator/`, `tests/`.
7. Preserve existing work and avoid product-runtime behavior changes unless a
   documentation inconsistency reveals a narrowly scoped fix that the task
   explicitly needs. Source paths: `AGENTS.md`, `.trellis/workflow.md`.
8. Record any intentionally retained platform-specific guidance as an adapter
   to Trellis rather than a second source of truth, including Gemini and
   OpenCode. Source paths: `AGENTS.md`,
   `.agents/skills/`, `.codex/`, `.claude/`, `.github/`, `.gemini/`,
   `.opencode/`.

## Out of Scope

- Rewriting generator, CLI, server, scenario, or trace-bundle runtime behavior
  unless a small documentation-supporting fix is required. Source paths:
  `src/anomaly_metric_creator/legacy.py`, `src/anomaly_metric_creator/cli.py`,
  `src/anomaly_metric_creator/server.py`, `src/anomaly_metric_creator/trace_bundle.py`.
- Replacing Trellis itself or changing generated Trellis platform runtime
  contracts. Source paths: `.trellis/`, `.agents/skills/`, `.codex/`,
  `.claude/`, `.github/`, `.gemini/`, `.opencode/`.
- Running the full test suite solely for docs-only edits unless the final diff
  changes executable code, hooks, scripts, packaging, or validation behavior.
  Source paths: `pyproject.toml`, `tests/`, `.github/workflows/ci.yml`.

## Acceptance Criteria

- [ ] `.trellis/spec/` contains complete, project-specific guidance for
      architecture/design, API/CLI/server surfaces, coding standards,
      operations/security/logging, documentation standards, testing standards,
      review/CI expectations, and agent workflow usage.
- [ ] Every convention in the updated `.trellis/spec/` cites supporting
      repository path(s), with line/symbol/section details when verified.
- [ ] `AGENTS.md`, `CLAUDE.md`, GitHub/Copilot instructions, and retained
      platform adapters, including Gemini and OpenCode, agree on Trellis as the
      canonical home for durable conventions.
- [ ] Generated/local runtime state and historical task artifacts are not
      promoted into permanent specs unless they encode a real project
      convention.
- [ ] Spec index links resolve, and no generated scaffold text remains in
      `.trellis/spec/` or this task's planning artifacts.
- [ ] The verification pass runs at minimum:
      `python3 ./.trellis/scripts/get_context.py`, a spec placeholder scan, a
      spec link check, and `git diff --check`.
- [ ] If executable files change, run the most targeted relevant tests or explain
      why they were not run.

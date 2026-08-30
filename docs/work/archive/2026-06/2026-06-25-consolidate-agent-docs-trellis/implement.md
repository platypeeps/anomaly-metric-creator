# Implementation Plan

## Phase 1: Inventory and Classification

1. Re-run a focused inventory of documentation, agent, workflow, task, hook, and
   CI files. Capture repo-relative paths for every source that contributes a
   convention. Source paths: `AGENTS.md`, `CLAUDE.md`, `README.md`, `docs/`,
   `.agents/`, `.codex/`, `.claude/`, `.gemini/`, `.github/`, `.opencode/`,
   `.trellis/`.
2. Classify files into durable convention, platform adapter, generated/local
   state, historical tracking, and user-facing documentation. Source paths:
   `.trellis/workflow.md`, `.trellis/tasks/`, `.trellis/workspace/`,
   `.github/`, `.agents/`, `.codex/`, `.claude/`, `.gemini/`, `.opencode/`.
3. Read source code and tests for any convention that needs executable
   confirmation, especially CLI/server/scenario/topology/trace-bundle claims.
   Source paths: `src/anomaly_metric_creator/`, `tests/`, `pyproject.toml`.

## Phase 2: Rewrite Trellis Specs

1. Update `.trellis/spec/backend/index.md` so it declares the final read order,
   scope, package ownership, and verification commands. Source paths:
   `.trellis/spec/backend/index.md`, `.agents/skills/trellis-before-dev/SKILL.md`,
   `.trellis/workflow.md`.
2. Rewrite or add backend spec files for architecture, APIs, scenarios/data,
   operations/security/logging, testing/quality, and documentation/review.
   Source paths: `CLAUDE.md`, `README.md`, `docs/`, `src/anomaly_metric_creator/`,
   `tests/`, `.github/`.
3. Ensure every convention has a `Sources:` line or equivalent inline citation
   that names the supporting repo path(s). Source paths: `AGENTS.md`,
   `CLAUDE.md`, `README.md`, `docs/`, `src/`, `tests/`, `.github/`,
   `pyproject.toml`, `.pre-commit-config.yaml`.
4. Remove placeholder, template, or stale bootstrap language from the final
   specs. Source paths: `.trellis/spec/`, `.trellis/tasks/`.

## Phase 3: Reconcile Adapters and User-Facing Docs

1. Update `AGENTS.md`, `CLAUDE.md`, GitHub/Copilot instructions, and retained
   platform files so they point to Trellis for durable conventions. Keep Gemini
   and OpenCode files as thin Trellis adapters. Source
   paths: `AGENTS.md`, `CLAUDE.md`,
   `.github/instructions/anomaly-metric-creator.instructions.md`, `.agents/`,
   `.codex/`, `.claude/`, `.gemini/`, `.opencode/`.
2. Keep user-facing docs focused on usage and behavior, and avoid turning them
   into a second implementation-rule source. Source paths: `README.md`,
   `docs/application-flow.md`, `docs/topology.md`, `.trellis/spec/`.
3. Do not change product runtime code unless a directly verified inconsistency
   requires a small supporting fix. Source paths: `src/anomaly_metric_creator/`,
   `tests/`, `README.md`, `docs/`.

## Phase 4: Verification

1. Run `python3 ./.trellis/scripts/get_context.py`.
2. Run a placeholder/template scan over `.trellis/spec/` and this task.
3. Run a Markdown link checker over `.trellis/spec/` and touched adapter docs.
4. Run `git diff --check`.
5. Run targeted tests if any executable code, hook, script, package metadata, or
   workflow behavior changes. Source paths: `pyproject.toml`, `tests/`,
   `.github/workflows/`, `.trellis/scripts/`, `.codex/hooks.json`,
   `.github/copilot/hooks.json`.

## Resolved Decisions

- Retain existing Gemini and OpenCode files as thin Trellis adapters, while
  moving durable conventions into Trellis. Source paths: `.gemini/`,
  `.opencode/`, `.trellis/spec/`.

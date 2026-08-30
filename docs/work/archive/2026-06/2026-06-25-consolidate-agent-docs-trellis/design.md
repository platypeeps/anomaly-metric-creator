# Design: Consolidated Trellis Specs

## Source-of-Truth Model

- `.trellis/spec/` becomes the canonical location for durable development
  conventions. Platform files may explain how a specific agent loads Trellis,
  but must not carry conflicting copies of project rules. Source paths:
  `AGENTS.md`, `.trellis/workflow.md`, `.agents/skills/trellis-before-dev/SKILL.md`.
- `CLAUDE.md` remains an important source document during consolidation because
  it currently contains the most complete project-specific architecture and
  validation guidance. After consolidation, it should either point to Trellis or
  stay synchronized as a curated adapter. Source paths: `AGENTS.md`, `CLAUDE.md`,
  `.trellis/spec/backend/`.
- User-facing runtime behavior belongs in `README.md` and `docs/`; agent-facing
  implementation rules belong in `.trellis/spec/`; overlapping statements must
  be reconciled rather than copied blindly. Source paths: `README.md`,
  `docs/application-flow.md`, `docs/topology.md`, `.trellis/spec/`.

## Proposed Spec Layout

- `.trellis/spec/backend/index.md`: package index, read order, validation
  commands, and map of conventions to detailed specs. Source paths:
  `.trellis/spec/backend/index.md`, `.agents/skills/trellis-before-dev/SKILL.md`.
- `.trellis/spec/backend/architecture.md`: canonical module boundaries,
  scenario registry ownership, server split, facade modules, and design
  constraints. Source paths: `CLAUDE.md`, `src/anomaly_metric_creator/`,
  `docs/application-flow.md`.
- `.trellis/spec/backend/api-cli-server.md`: CLI entry points, serve mode,
  request/mutation/debug endpoints, trace bundles, schema validation, and output
  contracts. Source paths: `README.md`, `CLAUDE.md`,
  `src/anomaly_metric_creator/cli.py`, `src/anomaly_metric_creator/server.py`,
  `src/anomaly_metric_creator/server_traces.py`,
  `src/anomaly_metric_creator/server_mutations.py`,
  `src/anomaly_metric_creator/trace_bundle.py`, `tests/`.
- `.trellis/spec/backend/scenarios-and-data.md`: `Scenario` dataclass,
  SCENARIOS registry invariants, `days_required` semantics, topology, anomaly
  catalog, and metric/component lockstep updates. Source paths: `CLAUDE.md`,
  `README.md`, `docs/topology.md`, `src/anomaly_metric_creator/scenarios.py`,
  `src/anomaly_metric_creator/legacy.py`, `tests/`.
- `.trellis/spec/backend/operations-security-logging.md`: serve-mode config,
  structured request/error logs, command simulator, Kubernetes/Helm facades,
  security boundaries, and operational diagnostics. Source paths: `CLAUDE.md`,
  `src/anomaly_metric_creator/server_ops.py`,
  `src/anomaly_metric_creator/server_commands.py`,
  `src/anomaly_metric_creator/server_kubernetes.py`,
  `src/anomaly_metric_creator/server_helm.py`,
  `src/anomaly_metric_creator/server_debug_ui.py`, `tests/`.
- `.trellis/spec/backend/testing-quality.md`: pytest/xdist defaults,
  deterministic test expectations, resource-cost guards, structural lint tests,
  ruff/codespell expectations, and validation matrix. Source paths:
  `CLAUDE.md`, `pyproject.toml`, `tests/conftest.py`, `tests/`,
  `.pre-commit-config.yaml`, `.github/workflows/ci.yml`.
- `.trellis/spec/backend/documentation-review.md`: documentation update
  standards, PR checklist expectations, GitHub/Copilot review surfaces, and CI
  workflow documentation. Source paths: `CLAUDE.md`, `README.md`,
  `.github/PULL_REQUEST_TEMPLATE.md`,
  `.github/instructions/anomaly-metric-creator.instructions.md`,
  `.github/workflows/`.
- `.trellis/spec/guides/`: shared Trellis process guidance remains separate
  from backend-specific project conventions. Source paths:
  `.trellis/spec/guides/`, `.trellis/workflow.md`.
- Existing Gemini and OpenCode files remain in the repository as platform
  adapters that point back to Trellis for durable conventions. Source paths:
  `.gemini/`, `.opencode/`, `.trellis/spec/`.

Existing backend spec files can either be rewritten in place or replaced by the
layout above, provided `.trellis/spec/backend/index.md` links to the final set
and no stale files keep contradictory guidance. Source paths:
`.trellis/spec/backend/`, `.trellis/spec/backend/index.md`.

## Citation Format

- Use repo-relative path citations inside each convention block, for example:
  `Sources: CLAUDE.md; src/anomaly_metric_creator/server.py; tests/test_server.py`.
  When a line, symbol, heading, or command was directly verified, include it
  after the path. Source paths: `AGENTS.md`, `CLAUDE.md`, `README.md`, `tests/`.
- Do not cite generated/local state as the only source for product conventions;
  generated files may support adapter behavior but not runtime architecture.
  Source paths: `.trellis/workspace/`, `.trellis/tasks/`, `.codex/`,
  `.claude/`, `.gemini/`, `.opencode/`.

## Consolidation Policy

- Durable conventions are copied or rewritten into Trellis once, with citations.
  Noncanonical files should link to Trellis or summarize only how to invoke the
  local platform. Source paths: `AGENTS.md`, `CLAUDE.md`, `.github/`,
  `.agents/skills/`, `.codex/`, `.claude/`, `.gemini/`, `.opencode/`.
- Gemini and OpenCode are retained as thin Trellis adapters rather than removed
  in this pass. Source paths: `.gemini/`, `.opencode/`, `.trellis/spec/`.
- If two sources conflict, prefer executable code and tests first, then current
  user-facing docs, then agent guides, then historical planning notes. Source
  paths: `src/anomaly_metric_creator/`, `tests/`, `README.md`, `docs/`,
  `CLAUDE.md`, `.trellis/tasks/`, `.trellis/workspace/`.
- Historical planning or task text should become a follow-up only when it still
  matches current code and docs. Source paths: `.trellis/tasks/`,
  `.trellis/workspace/`, `src/anomaly_metric_creator/`, `tests/`.

## Verification Design

- Run Trellis context loading after spec edits so future sessions can discover
  the consolidated guidance. Source path: `.trellis/scripts/get_context.py`.
- Run a placeholder/template scan over `.trellis/spec/` and task planning files.
  Source paths: `.trellis/spec/`,
  `.trellis/tasks/06-25-consolidate-agent-docs-trellis/`.
- Run a Markdown link check over `.trellis/spec/`, `AGENTS.md`, and any adapter
  files touched in the pass. Source paths: `.trellis/spec/`, `AGENTS.md`,
  `.github/`, `.agents/`, `.codex/`, `.claude/`, `.gemini/`, `.opencode/`.
- Run `git diff --check`; run targeted tests only if executable behavior,
  scripts, packaging, or hooks change. Source paths: `pyproject.toml`,
  `.github/workflows/ci.yml`, `tests/`.

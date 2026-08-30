# Backend Development Guidelines

This directory is the canonical, task-loadable source of repository development
conventions. `CLAUDE.md`, GitHub/Copilot instructions, and platform files are
adapters or supporting source documents; durable rules belong here with source
path citations. Sources: `AGENTS.md`; `CLAUDE.md`; `docs/work/`.

## Pre-Development Checklist

Before editing runtime code, tests, docs, CI, hooks, or platform adapters:

1. Read this index, then read every focused spec that matches the surface you
   are touching. Sources: `AGENTS.md`; `docs/spec/`.
2. For generation, registry, module-boundary, or topology changes, read
   [Architecture](./architecture.md) and
   [Scenarios and Data](./scenarios-and-data.md). Sources:
   `src/anomaly_metric_creator/legacy.py`; `tests/test_scenarios.py`;
   `docs/topology.md`.
3. For CLI, server, API, schema, validation, or trace-bundle changes, read
   [API, CLI, and Server](./api-cli-server.md). Sources: `README.md`;
   `src/anomaly_metric_creator/cli.py`;
   `src/anomaly_metric_creator/server.py`;
   `src/anomaly_metric_creator/trace_bundle.py`.
4. For command traces, SQLite persistence, request logs, auth, CORS, rate
   limits, Kubernetes/Helm facades, or debug UI work, read
   [Operations, Security, and Logging](./operations-security-logging.md).
   Sources: `README.md`; `docs/work/`;
   `src/anomaly_metric_creator/server_traces.py`;
   `src/anomaly_metric_creator/server_ops.py`;
   `src/anomaly_metric_creator/server_debug_ui.py`.
5. For tests, validators, deterministic output, CI, dependencies, or review
   readiness, read [Testing and Quality](./testing-quality.md). Sources:
   `README.md`; `pyproject.toml`;
   `.pre-commit-config.yaml`; `.github/workflows/ci.yml`;
   `tests/conftest.py`.
6. For documentation, PR descriptions, GitHub/Copilot review guidance, or
   agent-platform files, read [Documentation and Review](./documentation-review.md).
   Sources: `AGENTS.md`; `README.md`;
   `.github/PULL_REQUEST_TEMPLATE.md`;
   `.github/instructions/anomaly-metric-creator.instructions.md`;
   `.agents/`; `.codex/`; `.claude/`; `.gemini/`; `.opencode/`.
7. Always read the shared thinking guides when a change touches repeated
   patterns, config constants, payload contracts, JSONL/API formats, or
   cross-surface behavior. Sources: `docs/spec/guides/index.md`;
   `docs/spec/guides/code-reuse-thinking-guide.md`;
   `docs/spec/guides/cross-layer-thinking-guide.md`.

## Spec Map

This map defines which spec file owns each class of durable convention.
Sources: `docs/spec/amc/backend/architecture.md`;
`docs/spec/amc/backend/api-cli-server.md`;
`docs/spec/amc/backend/scenarios-and-data.md`;
`docs/spec/amc/backend/operations-security-logging.md`;
`docs/spec/amc/backend/testing-quality.md`;
`docs/spec/amc/backend/documentation-review.md`.

| Guide | Owns |
| --- | --- |
| [Architecture](./architecture.md) | Package layout, module boundaries, generation/server split, mutable state boundaries |
| [API, CLI, and Server](./api-cli-server.md) | Console entry points, CLI/subcommands, schema/validate, serve mode, HTTP/Kubernetes/Helm API, trace bundles |
| [Scenarios and Data](./scenarios-and-data.md) | `Scenario`, `SCENARIOS`, topology, metrics/components, output schema data contracts |
| [Operations, Security, and Logging](./operations-security-logging.md) | Trace persistence/search, structured logs, redaction, auth/CORS/rate limits, debug UI |
| [Testing and Quality](./testing-quality.md) | Determinism, validation strategy, pytest/xdist, fixtures, pre-commit, CI, review checklist |
| [Documentation and Review](./documentation-review.md) | Documentation sync, PR template lockstep, Copilot guidance, platform adapter policy |

Legacy spec filenames remain as compatibility pointers only. Do not add new
rules to `directory-structure.md`, `database-guidelines.md`,
`error-handling.md`, `logging-guidelines.md`, or `quality-guidelines.md`;
put durable conventions in the focused guides above. Sources:
`docs/spec/amc/backend/directory-structure.md`;
`docs/spec/amc/backend/database-guidelines.md`;
`docs/spec/amc/backend/error-handling.md`;
`docs/spec/amc/backend/logging-guidelines.md`;
`docs/spec/amc/backend/quality-guidelines.md`.

## Source Precedence

When sources disagree, prefer executable code and tests first, current
user-facing docs second, the specs under `docs/spec/` third, adapter docs
fourth, and archived or historical planning notes last. Once the correct rule is known, update this
spec directory so future sessions load the reconciled version. Sources:
`src/anomaly_metric_creator/`; `tests/`; `README.md`; `docs/`; `CLAUDE.md`;
`.github/`; `docs/work/`.

`README.md` and `docs/` should explain user-facing behavior; `docs/spec/`
should explain implementation and review conventions; platform directories
should explain how that platform reaches those specs. Avoid copying the same durable
rule into multiple places. Sources: `README.md`; `docs/application-flow.md`;
`docs/topology.md`; `docs/work/`; `docs/spec/`; `.agents/`;
`.codex/`; `.claude/`; `.gemini/`; `.github/`; `.opencode/`.

## Quality Check

For docs-only spec consolidation, run at minimum:

```bash
node scripts/check-review-preflight.mjs
git diff --check
```

Also run a placeholder scan and Markdown link check over `docs/spec/` and
any adapter docs touched. If executable code, hooks, package metadata, or CI
workflow behavior changes, run the narrowest relevant test or lint command and
explain anything skipped. Sources: `scripts/check-review-preflight.mjs`;
`docs/spec/`; `.github/workflows/ci.yml`; `pyproject.toml`; `tests/`;
`.pre-commit-config.yaml`.

## Language

Write project documentation in English, use repo-relative paths in source
citations, and add line, symbol, or section detail when it has been verified
during the current pass. Sources: `AGENTS.md`;
`docs/work/archive/2026-06/2026-06-25-consolidate-agent-docs-trellis/prd.md`;
`docs/work/archive/2026-06/2026-06-25-consolidate-agent-docs-trellis/design.md`.

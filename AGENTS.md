# AGENTS.md

This repository's authoritative development conventions live in
[docs/spec/amc/backend/index.md](docs/spec/amc/backend/index.md). Read the
Trellis backend specs before editing code, docs, tests, CI, or platform
adapters. Sources: `docs/spec/amc/backend/index.md`, `.trellis/workflow.md`.

[CLAUDE.md](CLAUDE.md) is the Claude Code adapter: orientation and routing for
the SCENARIOS-based architecture, the `Scenario` dataclass, per-scenario
`days_required` semantics, the import-time `_validate_scenarios_registry()`
invariants, and the lockstep checklist for adding metrics, components, or
scenarios. If guidance conflicts, reconcile it into Trellis rather than adding
another copy here. Sources: `CLAUDE.md`, `docs/spec/amc/backend/`.

User-facing usage, install, CLI reference, output files, and the anomaly catalog
live in [README.md](README.md).

If [docs/repomix-map.md](docs/repomix-map.md) is present, use it for quick
repository orientation before broad searches. When code, docs, tests, scripts,
or platform-adapter tree changes make the map stale, refresh it with
`scripts/update_repomix`. Sources: `docs/repomix-map.md`,
`scripts/update_repomix`, `docs/spec/amc/backend/documentation-review.md`.

This file used to duplicate the agent guide and drifted from the runtime module
after the SCENARIOS migration. To prevent that recurring, durable conventions
now live in Trellis specs; update the focused spec first and keep this file as
a short entry point. Sources: `AGENTS.md`, `docs/spec/amc/backend/index.md`.

## Quick start

```bash
# Install (editable, with dev dependencies)
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'

# Generate default output (50,000 rows at 60s interval)
.venv/bin/amc

# Run tests (parallel, ~5-8 min on 16 GB RAM)
.venv/bin/pytest

# Lint tests for unused imports
.venv/bin/ruff check tests/
```

## Key files

| Path | Role |
|------|------|
| `src/anomaly_metric_creator/legacy.py` | Canonical implementation (large; being decomposed into focused modules — see `docs/repomix-map.md` for current sizes) |
| `src/anomaly_metric_creator/cli.py` | Package entrypoint (thin loader) |
| `anomaly-metric-creator.py` | Top-level compatibility shim |
| `tests/conftest.py` | Session-scoped fixtures, `run_capture` helper |
| `docs/repomix-map.md` | Generated Repomix repository map |
| `scripts/update_repomix` | Refreshes `docs/repomix-map.md` |
| `docs/spec/amc/backend/index.md` | Canonical development conventions |
| `CLAUDE.md` | Claude Code adapter: module-ownership map, extraction and determinism invariants, spec routing |
| `README.md` | User-facing docs, CLI reference, scenario catalog |

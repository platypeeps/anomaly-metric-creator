# AGENTS.md

This repository's authoritative agent guide is [CLAUDE.md](CLAUDE.md). Read it for
the SCENARIOS-based architecture, the `Scenario` dataclass, per-scenario
`days_required` semantics, the import-time `_validate_scenarios_registry()`
invariants, and the lockstep checklist for adding metrics, components, or
scenarios.

User-facing usage, install, CLI reference, output files, and the anomaly catalog
live in [README.md](README.md).

This file used to duplicate the agent guide and drifted from the runtime module
after the SCENARIOS migration. To prevent that recurring, the guide now lives in
a single place; please update [CLAUDE.md](CLAUDE.md) directly rather than
reintroducing parallel content here.

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
| `src/anomaly_metric_creator/legacy.py` | Canonical implementation (~12,800 lines) |
| `src/anomaly_metric_creator/cli.py` | Package entrypoint (thin loader) |
| `anomaly-metric-creator.py` | Top-level compatibility shim |
| `tests/conftest.py` | Session-scoped fixtures, `run_capture` helper |
| `CLAUDE.md` | Architecture guide, invariants, pre-PR checklist |
| `README.md` | User-facing docs, CLI reference, scenario catalog |

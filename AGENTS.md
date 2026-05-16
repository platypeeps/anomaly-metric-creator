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

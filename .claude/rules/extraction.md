---
paths:
  - "src/anomaly_metric_creator/**"
  - "anomaly-metric-creator.py"
  - "tests/conftest.py"
  - "tests/test_correctness.py"
  - "tests/test_determinism.py"
  - "tools/check_module_size.py"
---

# Extraction and re-import invariant

Code moving out of `legacy.py` follows one fixed pattern.
Canonical detail: [architecture.md](../../docs/spec/amc/backend/architecture.md) § Module Boundaries.

- Move code verbatim and re-import every moved name in `legacy.py` at the same place; the `legacy.<name>` surface (shim, facades, tests, server `state.legacy` lookups) must not change.
- New modules never import `legacy`; the dependency direction is one-way.
- A leaf that reads a registry `legacy.py` still owns reads it through a named, weak-referenceable live callback that `legacy.py` configures; an isolated `legacy.py` test load must stay garbage-collectable.
- Never snapshot a registry at import time and never add a reverse import; both break the one-way direction.
- Move callers with the code, and patch the name in the module that calls it (`anomaly_metric_creator.combine_impl.<name>`, not the `legacy` re-import); an intra-module call resolves in its own namespace.
- Keep import-time validation at its `legacy.py` call site when the validator moves; validation order must not change.
- After any extraction, grep the moved range for `^from \.` re-imports and confirm each still resolves; a line-range cut can overlap a prior extraction's re-import stub.
- Keep behavior modules under 800 lines; `tools/check_module_size.py` enforces the cap, and its `RATCHET` is the over-cap inventory, not prose.
- `scenario_catalog.py` is the one permanent exception; it is an ordered declarative registry and must not acquire validation or runtime orchestration.
- Extract a separable addition to an enrolled module; raise its ceiling in the same diff only for an inseparable one (an import, a widened annotation, one branch), because the ratchet forbids unreviewed growth, not growth.
- Load `legacy` with package context (a real submodule import or a dotted spec name) in `tests/conftest.py::_load_amc` and the fresh-copy loaders; a package-less `spec_from_file_location` copy cannot resolve the re-import seams.

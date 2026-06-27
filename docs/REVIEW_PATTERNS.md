# Review patterns

Use this as a compact checklist for local Prism/Copilot review and human PR
review. The goal is to catch recurring AMC issues before they consume another
remote review loop.

## Server Compatibility

- Manifest, trace bundle, and imported JSON/JSONL readers must validate
  read-back data even when AMC usually writes the file locally. Check decode
  failures, top-level shape, discriminator fields, and partial-error behavior.
- Kubernetes command support should stay backed by `resource_snapshot()` and
  `SimulationMutations`; do not add a second browser-only or command-only state
  model.
- New `kubectl` or Helm command rendering needs parser coverage, stable
  `matched_rule_id` values, trace visibility, unsupported/partial branches,
  and focused `tests/test_server.py` coverage.
- Debug UI polish should consume `/v1/state` and `/v1/debug/resources`; avoid
  inventing state that cannot be reproduced through server APIs.

## Determinism And Test Cost

- Tests must pass under xdist loadfile distribution and remain file-isolated.
  Use `tmp_path`, explicit seeds, and `monkeypatch` for environment changes.
- Heavy fixture use should come from `tests/conftest.py`; do not create
  duplicate module-scoped 1-day, 7-day, or N-instance datasets.
- Stream large CSVs in tests. Avoid `read_bytes()`, `readlines()`, or
  `read_text().splitlines()` on multi-hundred-MB generated files.
- Time-window tests around accelerated server clocks should freeze or bound the
  clock before constructing assertions.

## Workflow And Review Tooling

- Keep `pyproject.toml`'s `ruff==` pin and `.pre-commit-config.yaml`'s
  `astral-sh/ruff-pre-commit` rev in lockstep.
- Direct third-party workflow installs must use `python -m pip install` or
  `uv pip install` with exact `==` pins.
- CI cadence changes should update `scripts/classify_ci_changes.sh`,
  `.github/workflows/ci.yml`, `docs/DEVELOPMENT_CYCLE.md`, and the Trellis
  testing-quality spec together.
- Review-pack adapters should point to Trellis specs and scripts rather than
  copying project conventions into every platform-specific prompt.

## Documentation Drift

- README and `docs/server-roadmap.md` must move completed server-mode work out
  of future/planned language in the same PR as the implementation.
- PR descriptions should name every behavior change and explain whether the
  quick local gate, full local gate, remote full CI, or all of them were run.
- When Copilot flags a valid repeat issue, prefer a mechanical lint/test if the
  pattern is greppable. Use prose only for product judgment or review context
  that cannot be enforced locally.

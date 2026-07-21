# Split legacy dispatch root below 800 lines

## Goal

Finish epic `07-02-legacy-monolith-decomposition` without a line-count waiver:
reduce `src/anomaly_metric_creator/legacy.py` from 1,587 physical lines to
fewer than 800 by extracting its run-level orchestration while preserving the
historic `anomaly_metric_creator.legacy` API and byte-for-byte generation
behavior.

## Requirements

- Keep `legacy.py` as the compatibility and runtime-wiring facade. Existing
  public and test-visible names must remain available from that module.
- Move `main()` and the cohesive run-level artifact lifecycle helpers it uses
  to a focused implementation module. The implementation module must not
  import `legacy.py`.
- Preserve live `legacy` monkeypatch behavior at each `main()` call, including
  fresh modules loaded with `importlib.util.spec_from_file_location()` under a
  package-qualified name. Use a named, weak-referenceable namespace callback
  keyed by the isolated module's `__name__`; do not retain isolated module
  copies or snapshot registries at import time.
- Move `RunContext` to the existing model implementation owner and re-export
  the exact same class object through both `legacy.py` and `models.py`.
- Keep every new behavior module below 800 physical lines. `legacy.py` itself
  must also finish below 800 physical lines; no waiver is permitted.
- Preserve all generation order, RNG draw order/count, anomaly ordering,
  output filenames, output bytes, subcommand dispatch, stderr/stdout text,
  return values, missing-NumPy guidance, import-time validator ordering, and
  atomic publication semantics.
- Preserve the existing patch-visible runtime view of `COMPONENTS`,
  `INSTANCES`, `SCENARIOS`, `DERIVATIONS`, topology registries, helper
  functions, writers, and subcommand functions.
- Consolidate redundant historical relocation comments and excess vertical
  spacing in `legacy.py` only after behavior has moved. Retain concise module
  ownership and compatibility-seam documentation in the Trellis architecture
  spec and `CLAUDE.md`.
- Refresh `docs/repomix-map.md` after the source/test/docs tree changes.

## Acceptance Criteria

- [x] `legacy.py` contains fewer than 800 physical lines, verified by
      `wc -l`; every new behavior module also contains fewer than 800 lines.
- [x] `legacy.main()` remains the package entrypoint behavior and delegates to
      the extracted pipeline with the current legacy runtime namespace.
- [x] Fresh isolated legacy copies can patch `_apply_scenarios` and call
      `main()` without leaking or retaining runtime state.
- [x] `models.RunContext is legacy.RunContext`; existing defaults and direct
      construction continue to work.
- [x] All existing locked SHA-256 golden hashes and deterministic outputs are
      unchanged under the full test suite.
- [x] Focused entrypoint, facade, artifact hygiene, reporting, schema, OTEL,
      combine, topology, multi-instance, and missing-dependency tests pass.
- [x] `python anomaly-metric-creator.py --help`, the installed package import,
      Ruff, pre-commit, and the repository full-check gate pass.
- [x] `.trellis/spec/amc/backend/architecture.md`, `CLAUDE.md`, the parent epic
      artifacts, and `docs/repomix-map.md` describe the final ownership map.
- [x] PR review and required CI checks settle green before merge; the child and
      parent Trellis tasks are finished/archived through the normal SD flow.

## Notes

- This child exists because the maintainer rejected the epic's proposed
  dispatch-root waiver and selected the split alternative on 2026-07-21.
- This is a structural refactor only. Any output or CLI behavior delta is a
  regression, not an intended change.
- PR #291 merged on 2026-07-21 at commit
  `1a19bdc296a990b791c48d20aa5c3a131f943f65` after the final-head Python
  3.14 heavy/light matrix, combined coverage gate, CodeQL, and all review
  conversations settled green.

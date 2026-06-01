---
applyTo: '**'
---

# Copilot review instructions for anomaly-metric-creator

This is a single-file Python project (`anomaly-metric-creator.py`) plus a tests
suite in `tests/`. `CLAUDE.md` is the authoritative architecture and review
guide; `README.md` documents the user-facing surface. Read the relevant
sections from both before reviewing a change — do not produce overview-only
or generic Python feedback. If a change touches behavior that `CLAUDE.md`
already specifies, the review should be grounded in those specifics.

## Where to look first by diff shape

- **Anomaly / scenario change** (`SCENARIOS`, `register_cascade`, anomaly
  generators, `--scenarios` / `--exclude-scenarios` / `--anomaly-count`) →
  `CLAUDE.md` *Anomaly injection schema*, *Scenario registry*, *Adding a new
  scenario*, *Scenario selector test layout*. The dispatch rule for
  generator arity (2-arg / step-3 / span-5, with `*args` rules) is the
  single most error-prone surface — review against the exact rule, not by
  intuition.
- **Topology / coupling / saturation** (`TOPOLOGY`, `Edge`,
  `SaturationParams`, `_compose_topology_*`, `_apply_saturation`,
  `--topology-mode`) → `CLAUDE.md` *Topology graph*, *Saturation feedback*,
  *LLM token-throttle*, *Per-instance topology (phase 8)*. The
  realistic-mode default and the `independent` deprecation alias have
  different output bytes; locked SHA-256 hashes pin the realistic baseline.
- **Multi-instance / dimensions** (`Instance`, `INSTANCES`,
  `--instances-per-component`, `--instance-config`,
  `_INSTANCE_DIMENSION_COLUMNS`) → `CLAUDE.md` *Multi-instance fan-out*,
  *Per-instance topology*, *OTEL dimension attributes*. The
  single-anonymous-`Instance()` default keeps byte-identical wide output;
  any named instance or `N > 1` switches per-component CSVs, `gauges.csv`,
  and `combined_metrics_unified.csv` into long-form layouts.
- **Output files** (`schema.json`, `gauges.csv`,
  `combined_metrics_unified.csv`, `anomalies.csv`, OTEL streaming) →
  `CLAUDE.md` *Output directory hygiene*, *Combine step*, *Output schema
  document*, *Gauge metric file*, *OTEL dimension attributes*. The
  pre-clean / summary / writer / validator views must stay aligned; they
  all derive from `_EMIT_ARTIFACT_FILES`.
- **Validator** (`--validate-output`, `--validate-warn`,
  `_validate_*` helpers, `_RECOMPUTERS`, `DERIVATIONS`) → `CLAUDE.md`
  *Output validator*, *MetricSpec schema metadata*, *Derived metrics*. The
  per-component / per-metric dispatch tables must raise on unknown keys;
  silent fall-through is the canonical bug class.
- **CLI / parse_args** → `CLAUDE.md` *Output directory hygiene*,
  *Multi-instance fan-out*, *Mode / flag combinations* checklist heading.
  `README.md` *CLI flags* lists the user-facing surface; every new flag
  needs at least one test exercising it in isolation.
- **Tests** (anything in `tests/`) → `CLAUDE.md` *Tests*, *Parallel
  execution*, *Test hygiene*, *Test resource cost*, *Cross-platform test
  guards*, *Scenario selector test layout*.

## Hard invariants — flag any diff that breaks these

- **Byte-deterministic output.** Locked SHA-256 golden hashes live in
  `tests/test_correctness.py`, `tests/test_schema_file.py`,
  `tests/test_gauges_file.py`, `tests/test_combine.py`,
  `tests/test_instances_per_component.py`, and
  `tests/test_topology_*.py`. A diff that shifts RNG draw order,
  reorders `COMPONENTS` / `SCENARIOS` / `MetricSpec` columns within a
  component's default zone, or changes generation order without
  re-locking the matching hashes is a regression — call it out.
- **RNG ordering.** All RNG flows through `RunContext.rng`
  (`np.random.RandomState(seed)`). No `np.random.*` module-level calls,
  no per-test `np.random.seed()`. Stable `sorted()` on
  `(row_idx, metric_name)` decides override order — same-cell collisions
  let the last writer win, so reordering colliding specs changes bytes.
- **Dispatch tables raise.** `_RECOMPUTERS[component]` not
  `_RECOMPUTERS.get(component)`. Dispatcher functions raise `KeyError` on
  unknown metric / component; never return `None`, an empty string, or
  a "soft violation" sentinel. Silent fall-through at the bottom of an
  `if/elif` chain is a bug.
- **Validators reject the full non-canonical input set.** For every
  field a new validator inspects, both branches of every discriminator
  must reject `None`, `NaN`, `±inf`, negative, `bool` (subclass of
  `int`), empty string, wrong container type. Callable *and* constant
  `Edge.weight`; cascade *and* primary specs; step *and* span paths;
  `*args` *and* fixed-arity callables.
- **Single source of truth.** No hand-rolled emit→filename,
  metric→component, or component→derivation maps alongside
  `_EMIT_ARTIFACT_FILES`, `COMPONENTS`, `DERIVATIONS`. The pre-clean,
  end-of-run summary, validator-required-files, and writer paths all
  read the same registry.
- **No module-level mutable state.** `anomalies`, `cascading_anomalies`,
  module-level RNG, module-level scenario lists were removed — keep
  per-run state on `RunContext`.
- **Mode / flag combinations.** Any new flag must be gated against every
  interacting flag (`--combine-only`, `--validate-output`,
  `--emit-selection` tokens, `--inject-dst-artifact-day`,
  `--topology-mode`, `--instances-per-component`, `--instance-config`)
  with a clear `parse_args` error or an explicit test pair.
- **Action order in `Done -` summary.** The end-of-run summary only
  names artifacts that were actually written, and prints only after
  every named writer has succeeded.
- **Derived metrics overwrite scenario overrides.** Derived columns
  (e.g. `cacheservice.hit_ratio = 100 * cache_hits / (cache_hits +
  cache_misses)`) are recomputed inside `generate_component()` after
  the anomaly-override pass. A scenario spec that writes the derived
  column directly (e.g. an anomaly on `hit_ratio`) is silently
  overwritten by the recomputation. Anomalies that want to influence a
  derived metric must drive its source columns instead — flag any
  scenario diff that targets a derived metric directly.
- **pytest-xdist test isolation.** The suite runs under
  `-n 4 --dist loadfile` by default. Tests must remain
  order-independent and file-isolated: every test writes only into
  `tmp_path`, and every `main()` invocation passes an explicit
  `--seed`. Do not introduce cross-file shared mutable state —
  module-level caches, file system fixtures outside `tmp_path`, or
  environment variables set without `monkeypatch` — because xdist
  distributes those tests to different workers and the failure mode
  is a non-reproducible flake. Session-scoped fixtures in
  `tests/conftest.py` are instantiated per worker; a `module`-scoped
  duplicate of a session-scoped fixture multiplies suite wall-time
  and peak RSS.

## Pre-PR checklist headings (canonical in CLAUDE.md)

PR descriptions in this repo carry a 13-heading checklist copied from
`CLAUDE.md` *Pre-PR checklist*. When reviewing, walk the diff against
each heading and call out any item that the PR description marked
confirmed but the diff does not support:

1. **Scope & description** — every behavior change in the diff is
   named in the PR description.
2. **Validators and schema checks** — non-canonical inputs enumerated;
   every discriminator branch validated; dispatch tables strict.
3. **Doc / docstring sync** — changed docstrings updated; changed
   symbol names grepped against `CLAUDE.md` and `README.md`.
4. **Single source of truth** — no parallel registries.
5. **Completeness** — fix is applied to every instance the title
   implies, not just one.
6. **Mode / flag combinations** — interacting flags gated or tested.
7. **Test path determinism** — new code paths covered by tests with
   explicit inputs; registry-derived `expected` guarded by
   `assert expected` *before* the membership check (the vacuous-test
   class from PR #50).
8. **Performance in hot paths** — no per-row re-parsing, no broad
   `try/except` around RNG-bearing code.
9. **Action order in user-facing output** — `Done -` line names only
   what was written.
10. **Test hygiene** — no unused imports / helpers; no
    `importlib.util.spec_from_file_location("amc", …)` re-load
    (route through `conftest._load_amc()` or annotate
    `# noqa: amc-load`).
11. **Test resource cost** — reuse session-scoped fixtures from
    `tests/conftest.py`; no `Path.read_bytes()` on multi-hundred-MB
    CSVs (chunked SHA-256 streaming); no `f.readlines()` /
    `splitlines()` just for a row count.
12. **Cross-platform test guards** — `import resource` / `pwd` / `grp`
    / `fcntl` / `termios` / `tty` guarded with
    `pytest.importorskip(...)` or
    `pytest.skip(..., allow_module_level=True)`; POSIX-only attributes
    on cross-platform modules (`select.epoll`, `signal.SIGSTOP`, …)
    guarded with `pytest.skipif(not hasattr(...))` or a module-top
    skip.
13. **Default-behavior changes** — any default parameter value or
    fallback path change is named in the PR description and tested
    on both old and new caller shapes.

## What not to spend review time on

- Generic Python style nits that ruff/black would catch (ruff runs in
  `.pre-commit-config.yaml`).
- Suggesting `__init__.py` / package layout changes — the script is
  intentionally a single file imported via `importlib`.
- Suggesting that `anomalies` / `cascading_anomalies` move back to
  module level — they were intentionally removed in favour of
  `RunContext`.
- Asking for comments that explain *what* the code does — the project
  convention is to comment only when the *why* is non-obvious.

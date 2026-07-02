---
applyTo: '**'
---

# Copilot review instructions for anomaly-metric-creator

This is a packaged Python project whose canonical implementation lives in
`src/anomaly_metric_creator/legacy.py`. The top-level
`anomaly-metric-creator.py` file is a thin compatibility shim, and the
installed `amc` / `anomaly-metric-creator` console scripts dispatch through
`anomaly_metric_creator.cli`. The authoritative development conventions live in
`.trellis/spec/amc/backend/index.md`; `CLAUDE.md` is an expanded historical/source
guide, and `README.md` documents the user-facing surface. Read the relevant
Trellis spec plus the supporting source docs before reviewing a change — do not
produce overview-only or generic Python feedback. If a change touches behavior
that Trellis already specifies, the review should be grounded in those
specifics.

## Local-first review cadence

Prefer local evidence before asking for another remote review or broad GitHub
Actions run. The repo's stable branch-protection check is the aggregate `test`
job; `.github/workflows/ci.yml` chooses a lightweight, quick, or full lane via
`scripts/classify-ci-changes.sh`. `tools/check_ci_review_contract.py` guards
the workflow/script/doc anchors that keep that cadence from drifting. CodeQL is
advisory on PRs and not a required branch-protection context (`test` and
`socket` are the required checks): opened/reopened/ready_for_review PRs and
`full-ci`-labeled updates are analyzed, plain synchronize events report a
skipped analysis, and merged code is always analyzed by the push-to-main run.
CodeQL keeps its `synchronize` trigger so pushes after the `full-ci` label is
applied re-analyze automatically. A skipped analysis produces no code-scanning
summary check, so `CodeQL` must not be re-added as a required context while
this gating is in place.

- For docs/spec/agent/review-tooling-only diffs, expect the lightweight lane
  and avoid requesting the full test lane unless the content changes a
  behavior contract.
- For routine app-path PR updates, the quick lane runs install smoke, ruff,
  review-churn lint tests, and focused server compatibility tests.
- For app-required opened/reopened/ready PRs, the `full-ci` label,
  workflow/dependency changes, workflow dispatch, and `main` pushes, expect the
  py3.12 test lane and heavy/non-heavy pytest split (Python 3.12 is the only
  CI-tested version).
- Before a final remote Copilot pass, prefer a local
  `bash scripts/sd-ai-command-pack-full-check.sh` run. During iteration, use
  `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=0 SD_AI_COMMAND_PACK_FULL_CHECK_GITO=0 bash scripts/sd-ai-command-pack-full-check.sh`
  to skip optional AI review while keeping the deterministic local guards.

## Review-cycle reduction

Before posting a repeated inline comment, check whether newer commits or tests
in the same PR already address the finding. Prefer one grouped comment per
helper, script, or contract when several sibling edge cases share the same root
cause.

For new parsers, validators, shell guards, generated-artifact checks, and
review-tooling scripts, review against the recurring edge-case matrix before
asking for another remote pass: duplicate entries, missing counterpart entries,
index-only/file-only rows, invalid encoding, empty values, flag-looking values,
wildcard namespaces, invalid owner/repo slugs, missing paths, and unintended
whole-repo scans.

For docs, skills, prompts, CI, and Trellis changes, check lockstep across
`.trellis/spec`, `.agents/skills`, `.github/prompts`,
`.github/instructions`, `.pre-commit-config.yaml`,
`scripts/classify-ci-changes.sh`, `tools/check_ci_review_contract.py`,
`tools/check_copilot_instruction_contract.py`,
`scripts/sd-ai-command-pack-install-audit.py`,
`scripts/sd-ai-command-pack-pr-body-scope.py`,
`.sd-ai-command-pack/pr-body-scope.json`, and focused tests. When the PR
description is incomplete, leave one top-level scope comment naming the exact
changed paths or behaviors that must be added instead of separate inline
comments for each omitted artifact. Match the PR-body scope checker by asking
for the relevant `Automation scope:`, `CI/review scope:`,
`Tooling/generated scope:`, `Docs/user-facing scope:`, or
`Runtime/server scope:` section.

## Generated and copied adapter files

Treat files copied in by Trellis or by `platypeeps/sd-ai-command-pack` as
generated or adapter content. Do not spend review comments on line-level
wording, duplicated project conventions, or broad refactors inside those files
when a PR is only syncing them into the repo. Review the canonical source,
local wiring, and executable integration instead.

Trellis-copied GitHub adapters include `.github/agents/trellis-*.agent.md`,
`.github/skills/trellis-*/**`, `.github/copilot/hooks.json`,
`.github/copilot/hooks/**`, `.github/hooks/trellis.json`, and Trellis command
or prompt entry points under `.github/prompts/`. SD command-pack copies include
`.agents/skills/sd-*/**`, `.github/prompts/sd-*.prompt.md`,
`.gemini/commands/sd/**`, `.opencode/commands/sd-*.md`,
`.sd-ai-command-pack/installed-targets.txt`, `docs/SD_AI_COMMAND_PACK.md`,
`scripts/sd-ai-command-pack-full-check.sh`,
`scripts/sd-ai-command-pack-housekeeping.sh`,
`scripts/sd-ai-command-pack-install-audit.py`,
`scripts/sd-ai-command-pack-pr-body-scope.py`,
`scripts/sd-ai-command-pack-review-learnings.py`,
`scripts/sd-ai-command-pack-review-local.sh`,
`scripts/sd-ai-command-pack-review-scope.sh`, and
`scripts/sd-ai-command-pack-update-spec-kb.py`.

Only comment on those copied files when the PR intentionally changes the
generator/source pack contract, the local adapter wiring is broken, a copied
script fails its repo tests or shell syntax checks, or the copied content
contradicts the canonical Trellis specs. In those cases, point the fix at the
source convention, source pack, or local integration point rather than asking
for project-specific rules to be hand-edited into each copied adapter.

## Where to look first by diff shape

- **Anomaly / scenario change** (`SCENARIOS`, `register_cascade`, anomaly
  generators, `--scenarios` / `--exclude-scenarios` / `--anomaly-count`) →
  `.trellis/spec/amc/backend/scenarios-and-data.md`, with `CLAUDE.md` as expanded
  source detail. The dispatch rule for
  generator arity (2-arg / step-3 / span-5, with `*args` rules) is the
  single most error-prone surface — review against the exact rule, not by
  intuition.
- **Topology / coupling / saturation** (`TOPOLOGY`, `Edge`,
  `SaturationParams`, `_compose_topology_*`, `_apply_saturation`,
  `--topology-mode`) → `.trellis/spec/amc/backend/architecture.md`,
  `.trellis/spec/amc/backend/scenarios-and-data.md`, and `docs/topology.md`. The
  realistic-mode default and the `independent` deprecation alias have
  different output bytes; locked SHA-256 hashes pin the realistic baseline.
- **Multi-instance / dimensions** (`Instance`, `INSTANCES`,
  `--instances-per-component`, `--instance-config`,
  `_INSTANCE_DIMENSION_COLUMNS`) → `.trellis/spec/amc/backend/architecture.md`,
  `.trellis/spec/amc/backend/api-cli-server.md`, and `README.md`. The
  single-anonymous-`Instance()` default keeps byte-identical wide output;
  any named instance or `N > 1` switches per-component CSVs, `gauges.csv`,
  and `combined_metrics_unified.csv` into long-form layouts.
- **Output files** (`schema.json`, `gauges.csv`,
  `combined_metrics_unified.csv`, `anomalies.csv`, OTEL streaming) →
  `.trellis/spec/amc/backend/api-cli-server.md` and `README.md`. The
  pre-clean / summary / writer / validator views must stay aligned; they
  all derive from `_EMIT_ARTIFACT_FILES`.
- **Validator** (`--validate-output`, `--validate-warn`,
  `_validate_*` helpers, `_RECOMPUTERS`, `DERIVATIONS`) →
  `.trellis/spec/amc/backend/api-cli-server.md` and
  `.trellis/spec/amc/backend/testing-quality.md`. The
  per-component / per-metric dispatch tables must raise on unknown keys;
  silent fall-through is the canonical bug class.
- **CLI / parse_args** → `.trellis/spec/amc/backend/api-cli-server.md` and
  `.trellis/spec/amc/backend/testing-quality.md`.
  `README.md` *CLI flags* lists the user-facing surface; every new flag
  needs at least one test exercising it in isolation.
- **Tests** (anything in `tests/`) → `.trellis/spec/amc/backend/testing-quality.md`,
  with `CLAUDE.md` as expanded historical/source detail.

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

## Pre-PR checklist headings (canonical in Trellis)

PR descriptions in this repo carry a 14-heading checklist mirrored from
`.trellis/spec/amc/backend/testing-quality.md` and
`.trellis/spec/amc/backend/documentation-review.md`. When reviewing, walk the diff against
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
    `# amc-load: allow`).
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
14. **CI / workflow / dependency hygiene** — workflow YAML, dependency pins,
    Dependabot behavior, and generated review instructions stay in lockstep
    with Trellis, `pyproject.toml`, pre-commit, and CI.

## What not to spend review time on

- Generic Python style nits that ruff/black would catch (ruff runs in
  `.pre-commit-config.yaml`).
- Asking for a package layout from scratch — the package already exists.
  Useful follow-up feedback should name a concrete behavior split or facade
  that preserves shim / console-script parity, not broad reshuffling for its
  own sake.
- Suggesting that `anomalies` / `cascading_anomalies` move back to
  module level — they were intentionally removed in favor of
  `RunContext`.
- Asking for comments that explain *what* the code does — the project
  convention is to comment only when the *why* is non-obvious.

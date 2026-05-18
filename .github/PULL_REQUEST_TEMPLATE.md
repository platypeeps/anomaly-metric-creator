## What this PR does

<!-- One paragraph. Name every behavior change in the diff: new CLI flags, state-model changes, default-output byte changes, public-helper signature changes, doc surface changes. If anything in the diff is broader than the description, either split the PR or update this section. -->

## Pre-PR checklist

Work through each item before marking the PR ready for review. Tick the box or write "N/A — _reason_".

### Scope & description
- [ ] PR description names every behavior change (RNG, registries, module-level state, output bytes, public API, CLI/env semantics, doc surface).
- [ ] If the diff touches RNG, registries, or any module-level state, the description calls it out explicitly and the test plan covers determinism.

### Validators and schema checks
- [ ] For every field a new validator inspects, I enumerated non-canonical inputs: `None`, `NaN`, `±inf`, negative, bool-as-number, empty string, unhashable, wrong container type. Each is tested or explicitly documented as out-of-scope.
- [ ] Every *branch* of a discriminator is validated (callable **and** constant weights; cascade **and** primary specs; step **and** span paths; `*args` **and** fixed-arity callables).
- [ ] Dispatch tables (`_RECOMPUTERS`, `DERIVATIONS`, `COMPONENTS`, etc.) raise on unknown keys instead of returning `None` or falling through silently.

### Doc / docstring sync
- [ ] Every changed function with a docstring has its docstring updated in this diff.
- [ ] I grepped every changed symbol name against `CLAUDE.md` and `README.md` and updated any prose that describes it.
- [ ] If a public helper was removed or repurposed (e.g. `register_cascade`, `_EMIT_ARTIFACT_FILES`), CLAUDE.md prose is updated in this diff.

### Single source of truth
- [ ] No hand-rolled emit→filename, metric→component, or component→derivation maps added alongside a canonical registry. Every consumer reads from the canonical source.
- [ ] The `_COMBINE_OUTPUT_FILENAME` constant (or equivalent) is used by the actual writer, not just the cleanup/summary path.

### Completeness
- [ ] The PR title implies a class of fix (e.g. "add clip_min to non-negative metrics"). I grepped for all instances and confirmed coverage.

### Mode / flag combinations
- [ ] I listed every other CLI flag, env var, and `--emit-selection` token that interacts with the new flag. Each combination is either gated in `parse_args` with a clear error message, or has a test.
- [ ] Any new `parse_args` check does not spuriously reject `--combine-only` or non-default `--emit-selection` invocations.

### Test path determinism
- [ ] Every new code path has a test whose input deterministically exercises that path (no reliance on "the default seed happens to do X").
- [ ] For new CLI flags, each single-value option is covered in isolation, not only in the most-permissive bundle.

### Performance in hot paths
- [ ] No per-row work that re-parses strings or re-computes constants that could be hoisted above the loop.
- [ ] No `try/except` in a per-row loop where the exception class is broad enough to catch real errors and the body has side effects (e.g. RNG draws).

### Action order in user-facing output
- [ ] "Done — wrote X" lines appear only *after* the writer for X completes successfully.

### Test hygiene
- [ ] New test files have no unused imports or unused helpers (confirmed with `ruff check tests/`).

### Default-behavior changes
- [ ] If a default parameter value or fallback path changes (new unseeded `RandomState`, required arg where optional was accepted, etc.), the PR description names it and tests cover both old and new caller shapes.

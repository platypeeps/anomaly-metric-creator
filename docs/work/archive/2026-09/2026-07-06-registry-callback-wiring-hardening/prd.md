---
title: Harden or document the schema/validate registry-callback singleton
status: planning
parked: 2026-09-01 age-sweep
created: 2026-07-06
---
# Harden or document the schema/validate registry-callback singleton

## Review context

- **Source:** deep-dive architecture review, 2026-07-06.
- **Confidence:** CONFIRMED mechanism; the hazard is latent (benign
  today), so this is a decide/verify task.
- **Severity:** LOW-MEDIUM — exactly the "leaked global state becomes an
  order-dependent xdist flake" class the repo's own test rules warn
  about.
- **Category:** architecture / test isolation.

## Goal

Make the registry-callback wiring between `legacy.py` and
`schema_impl.py`/`validate_impl.py` either robust to multiple legacy
module instances or explicitly documented as a single-instance constraint
with a guard.

## Problem (verified 2026-07-06)

`schema_impl` and `validate_impl` access live topology registries through
module-global callables configured by legacy at import
([schema_impl.py:20](src/anomaly_metric_creator/schema_impl.py:20)-35,
[validate_impl.py:38](src/anomaly_metric_creator/validate_impl.py:38)-61;
configured at [legacy.py:8555](src/anomaly_metric_creator/legacy.py:8555)
and :8594-8597 with lambdas closing over legacy's globals). The design is
sound and one-way with fail-fast `RuntimeError` accessors — but it is a
**last-writer-wins singleton**: the fresh-copy legacy loaders in
`tests/test_correctness.py` / `tests/test_determinism.py` (documented in
CLAUDE.md) re-execute the configure calls and re-point the single shared
`schema_impl`/`validate_impl` instance at the fresh copy's registries for
*all* consumers. A later `amc.TOPOLOGY` monkeypatch on the original
module object would then be invisible to `validate_output` — an
order-dependent hazard under the default xdist parallelism. Benign today
only because the fresh copies' registries are identical unless patched.

## Requirements

- Decide the posture:
  - **Document + guard (lowest effort):** state the single-instance
    constraint in both modules' docstrings and CLAUDE.md, and add a
    focused test that demonstrates the last-writer-wins behavior so a
    future refactor that depends on per-instance wiring fails loudly.
  - **Harden:** make the wiring instance-keyed (e.g. the configure call
    returns a handle the caller passes to `write_schema_json` /
    `validate_output`, with the module-global as the default), so a
    second legacy instance cannot silently re-point the first's
    consumers.
- Either way, `--dist loadfile` assumptions and the memoized
  `conftest._load_amc()` path must keep working unchanged.
- No behavior change for the single-instance production path; golden
  hashes unchanged.

## Acceptance Criteria

- [ ] The chosen posture is implemented and recorded (module docstrings +
      CLAUDE.md callback-wiring paragraph).
- [ ] A test pins the behavior (either the documented singleton semantics
      or the hardened instance-keyed semantics).
- [ ] Full suite green under default xdist; golden hashes unchanged.

## Notes

- Surfaced while verifying the decomposition's callback pattern; the
  pattern itself was a good call — this task is about its one sharp edge.

## Decision (2026-07-17, sdelmas)

**Chosen posture: document + guard with a test** (not instance-keyed
hardening). Keep the single last-writer-wins callback singleton; make its
single-instance constraint explicit and pin the behavior so a future
per-instance-dependent refactor fails loudly.

Rationale: the hazard is latent (it only bites if a test monkeypatches
`amc.TOPOLOGY` on the original module *after* a fresh-copy loader
re-points the singleton, and today the fresh copies' registries are
identical, so nothing diverges). The instance-keyed fix would thread a
handle through `write_schema_json` / `validate_output`'s public
signatures — the exact surface `07-17-audit-typed-boundaries` (A-002) is
already reshaping — so hardening now means editing those signatures twice.
Flip to instance-keying only if a future test/feature genuinely needs two
live legacy instances with *different* registries.

Execution now reduces to the document-and-guard arm:
- State the single-instance constraint in the `schema_impl` /
  `validate_impl` module docstrings and the CLAUDE.md callback-wiring
  paragraph.
- Add one focused test that demonstrates the last-writer-wins re-point
  (fresh-copy loader re-points the shared instance) so a refactor
  assuming per-instance wiring trips it.
- `--dist loadfile` + memoized `conftest._load_amc()` unchanged; golden
  hashes unchanged. The instance-keyed acceptance bullet is now N/A.

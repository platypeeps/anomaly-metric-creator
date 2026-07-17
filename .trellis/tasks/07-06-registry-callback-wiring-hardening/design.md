# Registry-callback singleton: document + guard — Design (SD Work Designs, 2026-07-17)

## Overview

The posture decision is recorded in the PRD (2026-07-17, sdelmas):
**document + guard with a test**, not instance-keyed hardening. The
callback wiring stays a last-writer-wins singleton; this task makes the
single-instance constraint explicit and pins the behavior so a refactor
that assumes per-instance wiring fails loudly.

## Proposal

1. **Docstrings:** `schema_impl.py` and `validate_impl.py` module
   docstrings (beside the existing configure/accessor block,
   schema_impl.py:20-35 / validate_impl.py:38-61) gain the constraint
   paragraph: the configure calls are a process-wide last-writer-wins
   singleton; a second legacy module instance (fresh-copy test loaders)
   re-points the shared instance for ALL consumers; patch-after-repoint
   on the original module object is invisible to `validate_output` /
   `write_schema_json`. Single-instance is the supported production
   shape.
2. **CLAUDE.md:** extend the existing callback-wiring paragraph with
   two sentences (constraint + where the pin test lives).
3. **The guard test** (`tests/test_registry_callback_singleton.py`):
   using the session `amc` fixture plus `conftest._load_amc()`'s
   fresh-copy mechanics (the documented pattern from
   test_correctness/test_determinism):
   - load a fresh legacy copy (re-executing the configure calls),
   - monkeypatch a registry on the fresh copy and observe
     `validate_impl`'s accessor reflect it (last-writer-wins proven),
   - monkeypatch the *original* module's registry and observe the
     accessor does NOT reflect it (the hazard, pinned as documented
     semantics).
   Cleanup is the critical part: `finally`-restore by re-running the
   original module's configure calls so the shared singleton points
   back at the session fixture's registries — otherwise this test
   *creates* the exact cross-test leak it documents. Keep the whole
   test in one file (own worker under `--dist loadfile`), and restore
   even on assertion failure.

## Boundaries And Non-Goals

- No signature changes, no instance-keying (recorded N/A; revisit
  trigger: a real two-instances-different-registries need, and then
  inside typed-boundaries' signature work).
- No change to `conftest._load_amc()` memoization or xdist config.

## Affected Files

`src/anomaly_metric_creator/schema_impl.py`,
`src/anomaly_metric_creator/validate_impl.py` (docstrings), CLAUDE.md,
new `tests/test_registry_callback_singleton.py`.

## Risks And Edge Cases

- The restore step must re-point via the original module's own
  configure entrypoints (not hand-built lambdas) so the restored state
  is exactly the import-time wiring.
- The fresh-copy load must go through the sanctioned loader
  (`conftest._load_amc()` or the documented spec-name pattern) — the
  `amc-no-direct-spec-load` hook forbids ad-hoc spec loads.
- Memory cost: a fresh legacy copy re-runs import-time validation
  (~cheap, no generation) — fine for one test.

## Validation

- New test green serially AND under default xdist (`-n 4`), run twice
  in a row to prove no leak (`pytest <file> <file>`).
- Full suite green; hashes untouched (docs + tests only).

# Verify topology coupling math against zero-denominator inputs

## Audit context

- **Source:** first-time staff-engineer audit, 2026-07-02.
- **Confidence:** SUSPICION (not read end to end; verification task).
- **Severity:** UNKNOWN until verified — potential correctness (NaN/inf in CSV).
- **Category:** correctness / determinism.

## Goal

Confirm — or fix — that the realistic-topology coupling and saturation math
cannot emit `NaN`/`±inf` into a metric column under any legal configuration,
and lock the guarantee with a test. This is a **verify-then-decide** task: it may
close as "no change needed, test added".

## Problem (why it needs checking)

The topology composition divides by upstream magnitudes:

- `_compose_topology_coupled_specs`
  ([legacy.py:3504](src/anomaly_metric_creator/legacy.py:3504)) computes
  `(upstream / upstream_base) * downstream_base * w_norm` and the callable-edge
  miss-ratio signal `cache_misses / (cache_hits + cache_misses)`
  (`_cache_miss_ratio_signal`, [legacy.py:3059](src/anomaly_metric_creator/legacy.py:3059)).
- `_apply_saturation` ([legacy.py:3699](src/anomaly_metric_creator/legacy.py:3699))
  divides `upstream_load / sat.midpoint`.

CLAUDE.md asserts guards exist (zero-denominator → 0 for the cache ratio;
utilization clamp for saturation; `_TOPOLOGY_COUPLE_NOISE_STD` floor), and the
import-time validators reject degenerate `SaturationParams`. But the audit did
**not** read these paths end to end, and the interaction with
`--metrics-per-component` trims (a required upstream column removed →
`signal` returns `None`) and with `dtype="int"` rounding was not verified.

The stakes: a single `NaN`/`inf` cell silently defeats downstream validation
(`np.std` → `NaN`, every comparison `False`) and breaks the byte-identical
determinism contract.

## Requirements

- Read `_compose_topology_coupled_specs`, `_cache_miss_ratio_signal`,
  `_apply_saturation`, `_per_instance_upstream_view`
  ([legacy.py:4054](src/anomaly_metric_creator/legacy.py:4054)), and
  `_compute_topology_arrays_per_instance`
  ([legacy.py:4133](src/anomaly_metric_creator/legacy.py:4133)) end to end.
- Enumerate the zero/degenerate denominators and confirm each is guarded:
  `upstream_base == 0`; `cache_hits + cache_misses == 0`; `sat.midpoint`
  (validated positive at import — confirm the validator actually runs on every
  edge); an upstream column trimmed to absent by `--metrics-per-component`;
  a downstream with zero matched instances under per-instance routing
  (`_matched_cardinality`, [legacy.py:4035](src/anomaly_metric_creator/legacy.py:4035)).
- Add explicit finite-value assertions where a guarantee is implicit.
- Add a focused regression test that runs the topology path under the
  denominator-stress configs and asserts every emitted cell is finite (and that
  the locked golden hashes are unchanged for the default config).

## Acceptance criteria

- [ ] Each division site above is confirmed guarded (documented in this task's
      notes with the guard location), or a fix is added.
- [ ] A test drives at least: default config, a `--metrics-per-component` trim
      that removes a coupled upstream column, and a near-zero-load config; it
      asserts no `NaN`/`inf` in any component CSV.
- [ ] The non-empty-`expected` guard convention is honored (the test can't pass
      vacuously if a filter excludes all candidates).
- [ ] Locked default / N=3 / 7-day golden hashes are unchanged.

## Notes

- Likely outcome is "guards already present" — but the audit rule is verify by
  reading the full path, and this one was left as a suspicion. Closing it either
  hardens the code or converts a suspicion into a documented guarantee + test.
- If confirmed clean, downgrade/close with the test as the deliverable.

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
  ([legacy.py:3497](src/anomaly_metric_creator/legacy.py:3497)) computes
  `(upstream / upstream_base) * downstream_base * w_norm` and the callable-edge
  miss-ratio signal `cache_misses / (cache_hits + cache_misses)`
  (`_cache_miss_ratio_signal`, [legacy.py:3052](src/anomaly_metric_creator/legacy.py:3052)).
- `_apply_saturation` ([legacy.py:3692](src/anomaly_metric_creator/legacy.py:3692))
  divides `upstream_load / sat.midpoint`.

> **Line refs updated 2026-07-06** (all six symbols are still in
> `legacy.py`; the topology cluster extracts in decomposition step 10).

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
  ([legacy.py:4047](src/anomaly_metric_creator/legacy.py:4047)), and
  `_compute_topology_arrays_per_instance`
  ([legacy.py:4126](src/anomaly_metric_creator/legacy.py:4126)) end to end.
- Enumerate the zero/degenerate denominators and confirm each is guarded:
  `upstream_base == 0`; `cache_hits + cache_misses == 0`; `sat.midpoint`
  (validated positive at import — confirm the validator actually runs on every
  edge); an upstream column trimmed to absent by `--metrics-per-component`;
  a downstream with zero matched instances under per-instance routing
  (`_matched_cardinality`, [legacy.py:4028](src/anomaly_metric_creator/legacy.py:4028)).
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

## Pre-read findings (2026-07-06 review — narrows the verify scope)

A read-through during the 2026-07-06 architecture/code review confirmed the
primary guards, so the remaining work is the regression test plus a decision
on two residual holes:

- **Guards confirmed:** `ups_arr / ups_base` gated by `if ups_base > 0`
  ([legacy.py:3647](src/anomaly_metric_creator/legacy.py:3647), and
  [legacy.py:4310](src/anomaly_metric_creator/legacy.py:4310) on the
  per-instance path); `downstream_base <= 0` skips
  ([legacy.py:3618](src/anomaly_metric_creator/legacy.py:3618)); cache ratio
  uses `np.divide(..., out=zeros, where=total > 0)` — zero- AND NaN-safe
  ([legacy.py:3069](src/anomaly_metric_creator/legacy.py:3069));
  `sat.midpoint` re-validated at call time
  ([legacy.py:3719](src/anomaly_metric_creator/legacy.py:3719));
  per-instance mean guarded by `if not arrays: continue`
  ([legacy.py:4110](src/anomaly_metric_creator/legacy.py:4110)).
- **Residual hole 1:** `w / sum_w` ([legacy.py:3665](src/anomaly_metric_creator/legacy.py:3665)
  and [legacy.py:4322](src/anomaly_metric_creator/legacy.py:4322)) relies
  entirely on import-time weight validation; a monkeypatched/programmatic
  `TOPOLOGY` whose active constant weights sum to 0 reaches a
  ZeroDivisionError (or inf with numpy operands). Decide: runtime guard or
  documented precondition.
- **Residual hole 2:** `_apply_saturation` never checks `upstream_load`
  itself for NaN/inf — `np.maximum(NaN, 0.0)` is NaN and propagates through
  the logistic. Unreachable from generated captures (finite by
  construction); reachable by direct callers. Decide: assert-finite or
  documented precondition.

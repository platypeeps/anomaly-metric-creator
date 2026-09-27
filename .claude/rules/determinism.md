---
paths:
  - "src/anomaly_metric_creator/**"
  - "tests/**"
---

# Determinism contract and pipeline order

For a fixed `--seed` and configuration, output bytes are a contract locked by SHA-256 golden hashes across `tests/`.
Canonical detail: [testing-quality.md](../../docs/spec/amc/backend/testing-quality.md) § Determinism and Single Sources of Truth.

## Determinism

- Create one `np.random.RandomState(seed)` in `main()`, carry it on `RunContext.rng`, and pass it explicitly through `generate_component()`, `_natural_column()` and the anomaly override path; a module-level RNG or mutable anomaly state breaks reproducibility.
- `generate_component()` sorts override specs by the stable key `(row_idx, metric_name)`; specs that collide on one pair resolve last-writer-wins in declaration order, so keep scenario declaration order unless you verified no collisions.
- Collisions occur when two cascades round to one row at a coarse `--interval-seconds`, or a cascade lands inside a shaped primary span.
- Preserve the `COMPONENTS` dict order and the `SCENARIOS` insertion order; `--anomaly-count` sampling depends on both.
- Sort any `set` iterated to build ordered output; set order is not stable across runs.
- Never fall back to an unseeded `RandomState` when `rng` is omitted; it makes output nondeterministic.
- Do not use `id()` for spec identity; it varies between runs.
- Use integer `timedelta` math, not float `datetime.timestamp() * 1e9`; floats lose nanoseconds.
- Keep `generate_component()` vectorized (one numpy op per column, masked anomaly writes, `np.char.add` CSV assembly); the suite runs full 1-day and 7-day runs through `main()`.

## Pipeline order

`generate_component()` runs one fixed sequence per component, so a change to it is a behavior change:

```
natural column draw → anomaly overrides → dtype="int" cast →
derivations → topology_capture snapshot → round → drop → CSV format
```

- The `int` cast runs before derivations and the capture, so derived columns and coupling signals see the integers the CSV records.
- Derived columns are recomputed after every override, so drive `cache_hits` / `cache_misses`, not `cacheservice.hit_ratio`; a direct override on a derived column is overwritten.
- Under realistic topology, each downstream is generated after its upstreams in `_topology_generation_order`, and cascades apply after saturation composition, so a cascade still pins its own cell.

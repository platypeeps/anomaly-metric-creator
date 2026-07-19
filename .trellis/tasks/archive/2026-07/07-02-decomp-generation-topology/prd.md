# Extract generation.py + topology_impl.py from legacy.py (decomposition final step)

## Goal

The RNG-order-critical core: generate_component/_natural_column/anomaly dispatch to generation.py; TOPOLOGY constants, coupling/saturation composition, and per-instance topology to topology_impl.py. Only after steps 2-9 have all landed with unchanged hashes. Every locked SHA-256 golden hash must be byte-identical; RNG draw order/count must not change; monkeypatched names (TOPOLOGY, _TOPOLOGY_LOAD_METRICS, _TOPOLOGY_SATURATION_TARGETS, _format_fixed3, DERIVATIONS) follow design.md's move-with-callers rule.

## Requirements (filled 2026-07-06 from the epic design + review)

- `generation.py` — `_natural_column`
  ([legacy.py:1036](src/anomaly_metric_creator/legacy.py:1036)),
  `generate_component`
  ([legacy.py:1164](src/anomaly_metric_creator/legacy.py:1164)), and the
  anomaly dispatch / row-format helpers (cluster ~1036–2336, ~1,290 lines).
- `topology_impl.py` — `Edge` / `SaturationParams` models, `TOPOLOGY`
  ([legacy.py:3076](src/anomaly_metric_creator/legacy.py:3076)), the
  import-time topology validators, coupling/saturation composition
  (~2954–3996), and the per-instance topology path (~3997–4458).
- Verbatim moves only; no refactoring in the extraction PR. RNG draw order
  and count must be provably unchanged (full golden-hash suite is the
  gate).
- Move-with-callers for every monkeypatched name listed in the Goal;
  `_format_fixed3` and `DERIVATIONS` stay wherever their patch-visibility
  requires per design.md's monkeypatch inventory.
- Import-time validator execution order preserved (topology registries
  validate after `_TOPOLOGY_SATURATION_TARGETS`).

## Acceptance Criteria

- [ ] Steps 2–9 (incl. catalog-data) are landed before this starts.
- [ ] All locked SHA-256 golden hashes byte-identical (default, N=3, 7-day,
      gauges, schema, combine) — full suite under `full-ci`.
- [ ] Facade/legacy identity tests pass; `state.legacy.<name>` lookups from
      the server modules still resolve.
- [ ] CLAUDE.md module map updated in the same PR.
- [ ] Follow-up recorded (not done in the move PR): deduplicate the
      near-verbatim coupling loops between `_compose_topology_coupled_specs`
      ([legacy.py:3591](src/anomaly_metric_creator/legacy.py:3591)–3682) and
      `_compute_topology_arrays_per_instance`
      ([legacy.py:4264](src/anomaly_metric_creator/legacy.py:4264)–4343) once
      both live in `topology_impl.py` — a hash-guarded refactor in its own
      PR after the extraction settles.

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
- 2026-07-06: `base_branch` in task.json corrected from the merged/deleted
  `refactor/extract-redaction` stacking branch to `main`.

# Extract generation.py + topology_impl.py from legacy.py (decomposition final step)

## Goal

The RNG-order-critical core: generate_component/_natural_column/anomaly dispatch to generation.py; TOPOLOGY constants, coupling/saturation composition, and per-instance topology to topology_impl.py. Only after steps 2-9 have all landed with unchanged hashes. Every locked SHA-256 golden hash must be byte-identical; RNG draw order/count must not change; monkeypatched names (TOPOLOGY, _TOPOLOGY_LOAD_METRICS, _TOPOLOGY_SATURATION_TARGETS, _format_fixed3, DERIVATIONS) follow design.md's move-with-callers rule.

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.

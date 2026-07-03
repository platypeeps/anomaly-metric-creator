# Extract combine_impl.py from legacy.py (decomposition step 5)

## Goal

Move combine_logs/combine_logs_unified, the wide/long writers, and the monotonic scan to combine_impl.py; re-point the combine.py facade. Caveat from design.md: _wide_component_rows_are_monotonic is monkeypatched by tests, so it and its intra-module callers move together in one PR. Combine golden hashes must be unchanged.

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.

# Extract cli_args.py from legacy.py (decomposition step 8)

## Goal

Move parse_args, _reconcile_cli_surface, _ADVANCED_DESTS, and the subcommand parsers to cli_args.py; main() stays in legacy.py. tests/test_cli_surface.py and the two-tier help contract must be unchanged.

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.

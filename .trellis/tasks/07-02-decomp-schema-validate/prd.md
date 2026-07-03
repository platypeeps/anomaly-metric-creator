# Extract schema_impl.py + validate_impl.py from legacy.py (decomposition step 6)

## Goal

Move write_schema_json + serializers to schema_impl.py and the validate-subcommand checks to validate_impl.py (writer first, then reader); re-point the schema.py facade. Schema golden hashes must be unchanged.

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.

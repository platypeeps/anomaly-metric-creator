# Extract gauges_impl.py + csv_layout.py from legacy.py (decomposition step 3)

## Goal

Move write_gauges_csv, _iter_component_rows, and the instance-block scan helpers to gauges_impl.py; move the shared header helpers (_scan_component_csv_headers, _classify_component_csv_header) to a csv_layout.py leaf in the same PR (shared with combine and the MCP tools). Verbatim move + legacy re-import per design.md; gauges golden hashes must be unchanged.

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.

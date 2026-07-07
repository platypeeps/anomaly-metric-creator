# Extract timeutil.py + otlp.py from legacy.py (decomposition step 2)

## Goal

Move the shared time helpers (_parse_csv_timestamp, _UNIX_EPOCH_UTC, _dt_to_unix_nanos, _to_unix_nanos) to timeutil.py and the eight _build_otlp_* payload builders plus _anomaly_event_id to otlp.py (imports timeutil). Verbatim move + legacy re-import per the epic design.md; timeutil is a leaf shared by combine/gauges/OTLP/server_mcp so it cannot live in any one consumer without cycles.

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.

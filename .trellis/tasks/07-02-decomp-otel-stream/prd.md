# Extract otel_stream.py from legacy.py (decomposition step 7)

## Goal

Move stream_otel_signals, stream_otel_gauges, and the transport/retry/activity-log helpers to otel_stream.py (uses redaction, otlp, timeutil); re-point the otel.py facade.

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.

# Extract otel_stream.py from legacy.py (decomposition step 7)

## Goal

Move stream_otel_signals, stream_otel_gauges, and the transport/retry/activity-log helpers to otel_stream.py (uses redaction, otlp, timeutil); re-point the otel.py facade.

## Requirements

- Move `stream_otel_signals`, `stream_otel_gauges`, `_write_activity`,
  `_verbose_body_repr`, and `_http_error_activity_fields` out of
  `src/anomaly_metric_creator/legacy.py` into a focused
  `src/anomaly_metric_creator/otel_stream.py` module.
- Keep the move behavior-preserving: `legacy.py` must re-import the moved names
  and no new module may import `legacy.py`.
- Re-point `src/anomaly_metric_creator/otel.py` at the focused streamer module
  while preserving its existing public `__all__` surface.
- Preserve the existing OTLP, redaction, CSV-layout, and timestamp helper
  dependencies by importing their focused modules directly from
  `otel_stream.py`.
- Update lightweight project guidance so future decomposition work knows this
  boundary has moved.

## Acceptance Criteria

- [x] `anomaly_metric_creator.otel.stream_otel_signals` and
  `anomaly_metric_creator.legacy.stream_otel_signals` are the same object.
- [x] `anomaly_metric_creator.otel.stream_otel_gauges` and
  `anomaly_metric_creator.legacy.stream_otel_gauges` are the same object.
- [x] The OTEL activity-log helpers still resolve from `legacy.py` for tests and
  compatibility shims.
- [x] Focused OTEL facade, redaction, and gauge-stream tests pass.
- [x] The generated repository map is refreshed after adding `otel_stream.py`.

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.

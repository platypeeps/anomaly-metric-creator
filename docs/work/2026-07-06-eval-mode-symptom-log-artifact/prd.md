---
title: Design a symptom-level log artifact servable in eval mode
status: planning
created: 2026-07-06
---
# Design a symptom-level log artifact servable in eval mode

## Review context

- **Source:** follow-up recorded in completed task
  `07-02-mcp-eval-mode-hardening` (2026-07-02 notes), promoted to a
  tracked task by the 2026-07-06 review (it previously lived only in the
  closed task's task.json notes).
- **Confidence:** design task — the gap is confirmed, the artifact is to
  be designed.
- **Severity:** enhancement — eval richness, not a defect.
- **Category:** feature / eval mode.

## Goal

Give eval-mode agents a log surface that carries only observable
*symptoms* (elevated latencies, error spikes, restarts) — never the
anomaly manifest — so log-based investigation is possible inside the
ground-truth wall instead of being refused outright.

## Problem

The eval-mode audit found that `metric_report.log` is a **verbatim
rendering of the anomaly manifest** (identical descriptions and
`event_id`s to `anomalies.csv`, including `Cascading:` labels), so it is
rubric-bearing by construction: the MCP `get_logs` /
`deduplicate_logs` tools refuse in eval mode (`_EVAL_MODE_LOG_NOTE`) and
`/v1/logs/stream` is hidden. That is correct — but it means an agent
under evaluation has *no* log modality at all, which both narrows what
evals can measure and makes eval mode easier to fingerprint (a
production-shaped system would have logs). The closed task's notes
explicitly flagged: "a distinct symptom-level observable log artifact
that eval mode could serve (the current report log cannot be, by
construction)."

## Requirements

- Write a short `design.md` first. Key design questions:
  - **Content:** what a symptom line carries (timestamp, component,
    observable metric behavior — e.g. threshold crossings derived from
    the generated CSVs) and, critically, what it must NOT carry (scenario
    slugs, anomaly descriptions, `event_id`s, `Cascading:` labels,
    anything from `anomalies.csv` / `SCENARIOS`).
  - **Derivation:** derive from the emitted per-component CSVs (the same
    ground the MCP analysis tools stand on), not from the manifest —
    that makes rubric-cleanliness structural rather than filtered.
  - **Surface:** a new `--emit` token vs serve-time synthesis;
    whether `/v1/logs/stream` and the MCP log tools serve it in eval mode
    while non-eval mode keeps the richer report log.
  - **Determinism:** byte-deterministic for a given seed (the repo's
    standing contract), with locked hashes if it becomes an artifact.
- Respect the ground-truth wall test from
  `07-06-eval-mode-ground-truth-wall-completeness`: the new artifact must
  pass the no-active-slug response-body sweep.
- Registry hygiene: if it is a new artifact file, it must join
  `_EMIT_ARTIFACT_FILES` / `_known_artifact_filenames()`, the atomic
  writer path, `_pre_clean_output_dir`, the end-of-run summary, and
  `schema.json` `files` — the single-source registries CLAUDE.md
  documents.

## Acceptance Criteria

- [ ] design.md answers the four design questions with a maintainer
      decision recorded.
- [ ] If implemented: eval mode serves the symptom log through the
      existing log surfaces (tools + SSE) instead of refusing; non-eval
      behavior unchanged; the artifact passes the eval leak-sweep test.
- [ ] If implemented as an emitted artifact: registry + atomic-writer +
      schema integration complete, with tests.
- [ ] If declined after design: the decision and rationale are recorded
      here and in the eval-mode docs.

## Notes

- Sequence AFTER `07-06-eval-mode-ground-truth-wall-completeness` — the
  wall must be leak-free before adding a new surface inside it.

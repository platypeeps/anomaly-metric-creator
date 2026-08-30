---
title: Decide or support multi-instance DST splice behavior
status: done
created: 2026-07-09
branch: docs/dst-multi-instance-boundary-posture
---
# Decide or support multi-instance DST splice behavior

## Backlog consolidation context

- **Source:** backlog consolidation sweep, 2026-07-09.
- **Historical source:** the removed server-mode handoff note as last present
  at `e52ee6a^`; that live file was intentionally removed in `e52ee6a`.
- **Current source:** `README.md` and `CLAUDE.md` still document one remaining
  multi-instance boundary: `--inject-dst-artifact-day > 0` remains
  incompatible with `--instances-per-component > 1` and `--instance-config`.
- **Consolidation result:** the old server-mode follow-ups are already
  represented by active or archived Trellis tasks. This task tracks the only
  remaining current-doc item found without an active Trellis task.

## Goal

Decide whether the intentional `--inject-dst-artifact-day` boundary for
multi-instance generation should stay documented as unsupported or be
implemented with a safe non-monotonic timestamp model.

## Existing follow-up-to-task mapping

- `kubectl get --watch` and API watch semantics:
  `06-29-server-watch-semantics`.
- Additional `kubectl logs` refinements:
  `06-29-kubectl-logs-refinements`.
- `kubectl events` richer sorting/filtering:
  `06-29-kubectl-events-compatibility`.
- More realistic `kubectl exec` outputs:
  `06-29-kubectl-exec-outputs`.
- More complete `kubectl port-forward` lifecycle:
  `06-29-kubectl-port-forward-lifecycle`.
- Helm `lint`, `dependency`, `repo`, and chart metadata commands:
  `06-29-helm-incident-command-coverage`.
- Optional persisted mutation state and explicit unsupported subresource
  handling: `06-29-persisted-server-mutation-state`.
- Debug UI shell extraction: archived as completed-by-prior-work in
  `archive/2026-07/06-29-debug-ui-shell-extraction`.
- Persistence/search, security/operations, and architecture cleanup sections in
  the final historical handoff said no known follow-ups remain beyond
  workshop-driven polish, so no new task was created for those sections.

## Requirements

- Make an explicit maintainer decision:
  - **Keep unsupported:** retain the current parser/helper guards, make the
    intentional boundary easy to find in docs/specs, and add or refresh tests
    only if the guard is under-covered.
  - **Implement support:** design a non-monotonic timestamp model that works
    for wide CSVs, long-form CSVs, gauges, schema validation, combine, and OTEL
    gauge streaming without silently corrupting ordering assumptions.
- Preserve the default single-instance and non-DST behaviors byte-for-byte
  unless the implementation path deliberately changes documented output.
- Keep any implementation behavior explicit at parse/validation time; do not
  make a formerly rejected combination partially work in only one artifact
  family.
- Update `README.md`, `CLAUDE.md`, and relevant Trellis specs so the chosen
  posture is discoverable from the canonical guidance, not just this task.

## Acceptance Criteria

- [x] The supported-vs-unsupported decision is recorded with rationale in the
      PRD or a follow-on design note before implementation starts.
      (See ## Decision (2026-08-26, sdelmas).)
- [x] The obligation the chosen outcome carries is met. Kept unsupported was
      chosen, so its obligation applies: user-facing and agent-facing docs name
      the `--inject-dst-artifact-day` plus multi-instance incompatibility as an
      intentional design boundary — `README.md` (the
      `--instances-per-component` and `--instance-config` rows, and the gauge
      streaming section), `CLAUDE.md` working rules, and `api-cli-server.md`
      § CLI Surface.

      The two outcomes were mutually exclusive, so this is one criterion, not
      two. Implementing it would have obliged the opposite work — tests across
      per-component CSVs, long-form/gauges, schema/validate, combine, and OTEL
      gauge streaming — and none of it was done, because it was the rejected
      option. It is recorded as rejected in the Decision section below rather
      than carried here as a criterion no outcome of this task could satisfy.
- [x] Error messages for rejected combinations remain clear and point to the
      supported alternative. Verified against the real parser:
      `--inject-dst-artifact-day 1 --instances-per-component 2` exits with
      "…by design (per-instance DST splicing produces non-monotonic timestamps
      inside each long-form row block, which downstream long-form merges in
      gauges.csv / combined_metrics_unified.csv cannot resolve); pass
      --inject-dst-artifact-day 0 or use the default single-instance mode".
- [x] No duplicate Trellis task is created for the same boundary.
      (`ls .trellis/tasks/ | grep -i 'dst\|splice'` returns only this one.)

## Decision (2026-08-26, sdelmas)

**Chosen posture: keep unsupported.** The `--inject-dst-artifact-day > 0`
plus multi-instance combination (`--instances-per-component > 1` or
`--instance-config`) stays rejected at parse time. It is an **intentional
design boundary**, not a gap awaiting implementation, and the docs say so in
those words.

Rationale: the DST splice duplicates the 02:00-02:59 wall-clock hour, so the
per-component CSV is deliberately non-monotonic. The multi-instance row
builder emits one block per instance, which is *already* non-monotonic across
block boundaries. Supporting both at once means defining a total order over a
timestamp column that is non-monotonic along two independent axes, and every
downstream consumer that currently relies on monotonicity would need its own
resolution rule -- the long-form writers, `gauges.csv`, the schema validator,
combine, the OTEL gauge streamer's chronological merge, and the MCP
`_layout_allows_break` fast path. That is a non-monotonic *timestamp model*,
not a flag fix, and no workshop or incident workflow requires it.

The guards and their coverage already exist and are unchanged by this
decision: parse-time rejection at `cli_args.py` (both multi-instance flag
paths, via the shared `_multi_instance` predicate), the separate gauge-path
gate, defense-in-depth for direct callers in `generation.py`, and tests in
`tests/test_instances_per_component.py` and `tests/test_args.py`. The work
this task carries is therefore a documentation sweep only, with no production
change.

Revisit trigger: a concrete workshop or incident workflow that needs DST
artifacts *and* multi-instance fan-out in the same run. Per `implement.md`,
an override turns this into a full non-monotonic-model design effort that
needs its own `design.md` before any implementation.

## Notes

- This is probably a decision task first. If support is chosen, add `design.md`
  before starting implementation because the timestamp model touches multiple
  artifact families and ordering assumptions.
- Current docs use both "intentional boundary" and "only remaining gate"
  language; settle on one clear posture when the task is executed.

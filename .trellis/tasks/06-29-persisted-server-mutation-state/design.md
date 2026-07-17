# Persisted server mutation state — Design (SD Work Designs, 2026-07-17)

## Overview

Opt-in restart continuity for the mutation overlay. The PRD's hard
constraints: default stays in-memory; persist only the modeled overlay
(no second Kubernetes state model); reset must discard predictably;
unsupported subresources stay rejected.

## Proposal

- **Flag:** `--persist-mutations PATH` (serve-only, default off —
  parallel in shape to `--persist-command-db`). JSON file.
- **Payload:** a versioned envelope
  `{"schema_version": 1, "mutations": {…}}` serializing exactly the
  `SimulationMutations` dataclass fields (workload overlays,
  created/deleted resources, extra events, Helm release overlays,
  deleted pods). Version-checked on load; unknown version → refuse with
  a clear error at startup (matching the trace store's posture; the
  loading code documents that a future field change bumps the version).
- **Write path:** re-serialize after every successful mutation commit,
  under the overlay's existing lock, via the atomic tmp+`os.replace`
  pattern (same guarantee as artifact writers: a reader/restart never
  sees a torn file). The overlay is small (KBs) — per-mutation writes
  are cheap; no debounce machinery.
- **Load path:** in `build_state`, when the flag is set and the file
  exists, hydrate `SimulationMutations` before the server starts;
  hydrated resources are validated against the *current* snapshot shape
  (a persisted overlay referencing a component absent from this run's
  `--components` is dropped with a stderr WARNING naming it — restart
  continuity assumes a compatible run, and silent ghosts would violate
  the sim-mutation-correctness parity rule).
- **Reset:** `/v1/mutations/reset` also truncates the persisted file
  (writes the empty envelope) — reset means baseline, in memory AND on
  disk (extends the reset task's contract; coordinate wording with
  `06-29-quick-simulator-environment-reset`'s docs).
- Subresource rejection paths are untouched (they never reach the
  overlay, so nothing persists).

## Boundaries And Non-Goals

- No cross-version migration beyond the version check; no SQLite (JSON
  suffices at this size); no persistence of traces/clock/generation
  state (other tasks / other stores own those).

## Affected Files

`src/anomaly_metric_creator/server.py` (flag + load wiring),
`src/anomaly_metric_creator/server_mutations.py` (serialize/hydrate +
write hook), `tests/test_server.py` or a focused
`test_server_mutation_persistence.py` (new), README serve docs,
`.trellis/spec/amc/backend/operations-security-logging.md`.

## Risks And Edge Cases

- Concurrency: the write happens under the mutation lock — verify no
  code path commits overlay changes outside that lock (audit first; any
  such path is a pre-existing bug worth flagging).
- Corrupt/truncated file at load (crash mid-first-write before atomic
  pattern existed, hand-edited): refuse with a clear error naming the
  file — never half-hydrate.
- Path collision with `--output-dir` artifacts: the file lives wherever
  the operator points it; document keeping it outside `--output-dir`
  (the pre-clean registry must not know it, and validate's
  unknown-file check would flag it).

## Validation

- Restart-continuity test: mutate (scale + helm rollback + delete pod) →
  serialize → new `build_state` from the file → snapshot renders equal
  the pre-restart overlay state.
- Reset test: reset clears memory and disk (file is empty envelope).
- Refusal tests: unknown version, corrupt JSON, stale component WARNING.
- Full suite (default path untouched — flag-off behavior byte-identical).

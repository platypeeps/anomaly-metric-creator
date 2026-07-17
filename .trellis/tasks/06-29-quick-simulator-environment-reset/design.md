# Quick simulator environment reset — Design (SD Work Designs, 2026-07-17)

## Overview

Verified state: `POST /v1/mutations/reset` exists (server.py:715, listed in
the endpoint registry at :83) and clears only the `SimulationMutations`
overlay; the debug UI already has a Reset button wired to it
(server_debug_ui.py:301, :1149). What the PRD actually still needs is the
**scope decision** (artifacts/traces/clock: reset or not), the explicit
documentation of that decision, and contract tests proving
baseline restoration across every overlay family.

## Proposal

**Decision: overlay-only reset is the contract.** `/v1/mutations/reset`
stays the one quick-reset surface; artifacts, command traces, and the
simulated clock are intentionally **not** reset:

- *Artifacts* are the baseline — reset restores the view *to* them;
  regenerating is `--continuous-generate`'s job or a server restart.
- *Traces* are debug history; wiping them destroys the operator's record of
  what was done before the reset (and the eval harness's scoring data).
  Clearing traces, if ever wanted, is a separate explicit endpoint.
- *Clock* is monotonic simulated time; rewinding it would corrupt trace
  ordering and SSE event sequencing.

Work items:

1. **Contract tests** (the bulk of the task): for each overlay family,
   mutate → reset → assert the observable surface equals its pre-mutation
   baseline rendering:
   - workload scale/restart/delete (`kubectl get deployments` render +
     `_k8s_objects_for_resource` object list),
   - created/deleted generic resources,
   - extra events (`kubectl get events` / events API),
   - Helm release overlay (`helm list`/`helm history` renders + release
     Secret payload),
   - deleted-pod filtering back to baseline pods.
   Compare full rendered stdout before mutation vs after reset (byte
   equality — the snapshot renderers are deterministic), plus
   `/v1/state`'s overlay summary returning to empty.
2. **Not-reset assertions:** command traces recorded before reset are
   still present after; `/v1/state` generation counters and clock
   unchanged by reset.
3. **Docs:** README serve section + debug-UI paragraph get an explicit
   "what reset does / does not do" list (the PRD's documentation
   acceptance bullet); the reset response body should state scope too —
   extend the JSON response with `{"scope": "mutation-overlay"}` if it
   does not already say so (compatible additive field only).
4. **Discoverability:** the serve banner reset hint is owned by the
   launcher task; this task adds the curl one-liner to README next to the
   scope list.

## Boundaries And Non-Goals

- No new endpoint, no trace-clearing, no artifact regeneration, no clock
  manipulation. If a "full environment reset" is ever wanted, it is a new
  task with its own consent gates.
- No debug-UI redesign — the existing button already posts to the right
  place.

## Affected Files

- `tests/test_server.py` (or a focused `tests/test_server_reset.py` —
  prefer the focused file; reuse `start_test_server`),
- `src/anomaly_metric_creator/server.py` (only the additive `scope` field
  in the reset response, if absent),
- `README.md`,
  `.trellis/spec/amc/backend/operations-security-logging.md` if it
  documents the mutation overlay.

## Risks And Edge Cases

- Byte-equality of before/after renders requires the simulated clock not
  to advance the rendered output between the two calls — if any renderer
  embeds "age"-style fields derived from the clock, compare
  normalized output or freeze the clock accessor for the test (check
  `resource_snapshot()` age fields at implementation time; the fuzz corpus
  will show whether ages render).
- Reset under concurrent SSE/debug polling must stay thread-safe — the
  overlay object already guards with locks; the test should hit reset
  while a debug poll loop runs once to smoke the interaction.

## Validation

- `pytest tests/test_server_reset.py -n 0` then full suite.
- Manual: mutate via kubectl scale + helm rollback in a live serve, click
  the debug-UI Reset button, re-run the inspection commands.

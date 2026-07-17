# Eval recipe trace-evidence loss — Design (SD Work Designs, 2026-07-17)

## Overview

In eval mode every trace-read surface is rubric-404'd (`/v1/debug/*`
including `commands/export`) and the in-memory ring dies with the process
— by design. The defect is the *recipe*: README's eval invocation passes
no persistence flag, so a harness following it verbatim ends the run with
zero retrievable agent-activity evidence (A-066).

## Proposal

1. **README eval recipe:** add `--persist-command-db eval-traces.sqlite`
   to the recommended command, with two sentences of rationale: the debug
   surfaces are intentionally hidden from the agent, so on-disk
   persistence is the harness's **only** trace-retrieval path in eval
   mode; `--debug-ring-size` is irrelevant to post-run retrieval there.
   Point at `amc trace-bundle` as the offline reader for the persisted
   store's export (and note `--persist-command-log` JSONL as the
   lighter-weight alternative).
2. **Serve-time warning (adopt the PRD's "consider" — yes):** in
   `serve_main`, when `mcp_eval_mode` is set and neither persistence flag
   is, print one stderr WARNING: eval traces will be unrecoverable after
   shutdown; name both flags. Operator stdout/stderr is harness-side, not
   agent-reachable — wall-safe.
3. **Docs cross-link:** the eval-mode section of CLAUDE.md/SECURITY.md
   already explains the wall; add one sentence noting persistence is the
   sanctioned harness-side evidence path (keep it out of any
   agent-visible surface).

## Boundaries And Non-Goals

- No new endpoints, no wall changes, no default-flag changes (persistence
  stays opt-in; only the *recommended recipe* and a warning change).
- The symptom-log artifact idea stays in
  `07-06-eval-mode-symptom-log-artifact` (related-but-distinct per PRD).

## Affected Files

`README.md`, `src/anomaly_metric_creator/server.py` (warning in
`serve_main`), `tests/` (warning presence/absence), CLAUDE.md or
SECURITY.md sentence, `.trellis/audit/ledger.md` (A-066 flip).

## Risks And Edge Cases

- The warning must fire for eval-without-persistence only — four flag
  combinations, all four asserted.
- Wording must not itself look like an error to harness log-scrapers —
  prefix `WARNING:` per the repo's existing stderr convention.

## Validation

- serve_main wiring-test pattern (see
  `07-17-audit-serve-main-wiring-tests`): capsys assertions over the four
  combinations.
- README snippet manually executed once end-to-end: run eval serve with
  the recipe, stop, `amc trace-bundle summary` the persisted export path.

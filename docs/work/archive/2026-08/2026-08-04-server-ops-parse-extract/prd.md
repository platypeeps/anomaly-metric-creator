---
title: Extract server_ops_parse.py (step 2)
status: done
created: 2026-08-04
branch: sdelmas/extract-server-ops-parse
---
# Extract server_ops_parse.py (step 2)

## Parent

Step 2 of epic `07-06-server-ops-decomposition`. Follows step 1
(`08-04-server-ops-profiles-extract`, PR #321, merged). The epic's
`design.md` fixes the boundaries and the per-step process; this task
executes one extraction PR against them.

## Goal

Extract the command parser cluster out of the 7,095-line
`src/anomaly_metric_creator/server_ops.py` into a new stdlib-only leaf
`src/anomaly_metric_creator/server_ops_parse.py`, changing **zero**
HTTP/command/MCP behavior. `server_ops.py` re-imports every moved name at
the same conceptual position so the compatibility surface
(`server.py`'s alias block, the `server_commands.py` facade,
`server_mcp.py` imports) needs no edits.

## Scope (moved cluster)

Per the epic design's step-2 boundary, plus the closure-forced additions
the one-way-import rule requires (a moved function may not call a symbol
that stays in `server_ops`):

- Named seed: `parse_command`, `_split_flags`, the flag tables
  (`_VALUE_FLAGS`, `_REPEATABLE_VALUE_FLAGS`, `_BOOL_FLAGS`,
  `_SENSITIVE_FLAG_TOKENS`, `_MODELED_FLAGS`), `command_fingerprint`,
  `_redact_parsed_flags`, `guess_intent`.
- Closure-forced: `ParsedCommand` (the dataclass `parse_command`
  returns), the family sub-parsers `_parse_kubectl` / `_parse_helm`, and
  the flag/redaction helpers the cluster calls
  (`_store_flag_value`, `_flag_values`, `_first_flag_value`,
  `_is_sensitive_flag_name`, `_redact_command_for_trace`,
  `_redact_argv`). The exact final set is pinned by the closure audit in
  `design.md`.

## Non-goals

- No renderer, snapshot, state, Kubernetes-API, or Helm code moves (later
  epic steps).
- No behavior change, no output-byte change, no new dependency.
- No edits to the three facades, `server.py`'s alias block, or
  `server_mcp.py` imports.

## Acceptance Criteria

- [x] New `server_ops_parse.py` is stdlib-only (plus already-imported
      lower leaves); it never imports `server_ops`.
- [x] `server_ops.py` re-imports every moved name at its original
      conceptual position; no caller (facades, `server.py` alias block,
      `server_mcp.py`) is edited.
- [x] The moved module is < 800 lines (the epic's per-module cap).
- [x] `parse_command` / `command_fingerprint` / `guess_intent` /
      `_redact_parsed_flags` render-oracle output is byte-identical
      before and after the move (fixed 33-command corpus).
- [x] Server-family tests green:
      `tests/test_server.py tests/test_server_ops_fuzz.py
      tests/test_server_mcp.py tests/test_server_eval_mode.py`, then the
      full suite.
- [x] Splice-hazard grep of the deleted ranges finds no orphaned
      `from .` re-import stub swept into the cut.
- [x] CLAUDE.md server-module map and
      `.trellis/spec/amc/backend/architecture.md` updated to list
      `server_ops_parse.py`; the epic's step tracker records step 2 done
      and the measured `server_ops.py` size.

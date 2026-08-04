# server_ops_parse.py extraction — Design (epic step 2)

Executes step 2 of `07-06-server-ops-decomposition` under that epic's
`design.md` rules (verbatim move, one-way import, re-import stub at the
conceptual position, splice-hazard grep, render-oracle diff). This file
records the closure audit that fixes the exact move set.

## Closure audit (verified against server_ops.py @ 7095 lines)

An independent transitive-closure pass over the parse/fingerprint/redact
cluster, then spot-verified by reading `_parse_kubectl`/`_parse_helm`/the
fingerprint+redact bodies directly. **Zero CONFLICTs** — every symbol the
cluster references is stdlib, the single lower-leaf import
`DEFAULT_NAMESPACE` (from `.server_mutations`), or itself a move-set
symbol. The strict one-way dependency (leaf never imports `server_ops`)
holds.

### MOVE set (26 symbols → `server_ops_parse.py`)

Dataclass:
- `ParsedCommand` (@118 incl. `@dataclass(frozen=True)`) — parse cluster's
  return type. Used as a param/return annotation by ~40 staying renderers;
  they resolve it through the re-import.

Flag/alias data constants:
- `_VALUE_FLAGS` (323), `_REPEATABLE_VALUE_FLAGS` (331), `_BOOL_FLAGS`
  (334), `_SENSITIVE_FLAG_TOKENS` (340), `_MODELED_FLAGS` (356),
  `_KIND_ALIASES` (372), `_EXPLAIN_RESOURCE_TARGETS` (471),
  `_EXPLAIN_GROUP_ALIASES` (494).

Parse functions:
- `parse_command` (607), `_split_flags` (654), `_store_flag_value` (727),
  `_flag_values` (740), `_first_flag_value` (751), `_parse_kubectl` (765),
  `_parse_helm` (869), `_split_resource_token` (900), `_normalize_kind`
  (910), `_split_explain_target` (914), `_normalize_explain_resource`
  (926).

Fingerprint/redact functions:
- `command_fingerprint` (4036), `guess_intent` (4053),
  `_redact_command_for_trace` (4070), `_redact_argv` (4076),
  `_redact_parsed_flags` (4099), `_is_sensitive_flag_name` (4106).

### STAY — interleaved among the move set, must NOT be swept

- `CommandResult` (133), `KubernetesApiResponse` — render return types.
- `_SENSITIVE_QUERY_KEYS` (341-355), `_redact_query`, `_is_sensitive_query_key`
  (4893) — HTTP-query redaction. `_is_sensitive_query_key` *reads* the
  moved `_SENSITIVE_FLAG_TOKENS`, so it becomes a re-import consumer.
- snapshot-kind constants (`_SNAPSHOT_KINDS` 432, `_MUTATION_SNAPSHOT_KINDS`
  454, `_CLUSTER_SCOPED_SNAPSHOT_KINDS` 468).
- `_EXPLAIN_RESOURCE_DESCRIPTIONS` (510) — only staying
  `_explain_schema_for_kind` uses it; do not pull it with the two explain
  constants that move.
- `_is_dry_run` (756) — only staying renderers call it; `ParsedCommand`
  param type resolves via re-import.
- `_preview` (4063) — physically inside the fingerprint block but only
  `run_command` uses it; leave in place.

Because MOVE and STAY symbols interleave, the extraction is **symbol-precise
(AST-driven span extraction)**, not a contiguous line-range cut.

### STAY-BUT-CALLS-MOVED (re-import must expose these names)

`run_command` (parse_command, command_fingerprint, the three redactors,
guess_intent), `render_command`/~40 `_render_*`/`_logs_*`/`_rollout_*`/
`_patch_*`/`_helm_*` (ParsedCommand type), `_with_flag_support`
(`_MODELED_FLAGS`), `_normalized_resource_prefix`/`_mutation_snapshot_kind`
(`_normalize_kind`), patch/apply paths (`_KIND_ALIASES`, `_flag_values`,
`_first_flag_value`), `_explain_schema_for_kind`/`_openapi_*`
(`_EXPLAIN_RESOURCE_TARGETS`), `_is_sensitive_query_key`
(`_SENSITIVE_FLAG_TOKENS`). All resolve through the single re-import block;
no caller is edited. `__all__` (@6867) lists the public moved names
(`ParsedCommand`, `parse_command`, `_split_flags`, `_parse_kubectl`,
`_parse_helm`, `_split_resource_token`, `_normalize_kind`,
`command_fingerprint`, `guess_intent`, `_redact_command_for_trace`,
`_redact_argv`, `_redact_parsed_flags`, `_is_sensitive_flag_name`) — the
re-import must keep them defined so `import *` and the public surface are
unchanged.

## Leaf module shape

`server_ops_parse.py`:
- docstring, `from __future__ import annotations`
- stdlib: `import shlex`, `from pathlib import Path` (parse_command uses
  `Path(argv[0]).name`), `from dataclasses import dataclass`,
  `from typing import Any`
- lower leaf: `from .server_mutations import DEFAULT_NAMESPACE`
  (parse_command's default arg)
- the 26 moved blocks, source order.

Estimated ~515-560 lines — under the epic's 800-line cap. No `field`,
`datetime`, `json`, `base64`, etc. needed by the move set.

## server_ops.py after the cut

- Delete the 26 symbol definitions.
- Insert ONE re-import block `from .server_ops_parse import (...)` at the
  conceptual position where `ParsedCommand` sat (~line 118, in the
  dataclass region), mirroring step 1's single
  `from .server_ops_profiles import (...)` block. Placing it high makes
  `ParsedCommand` available before every staying renderer that annotates
  with it.
- Facades (`server_commands.py`, `server_helm.py`), `server.py`'s alias
  block, and `server_mcp.py` imports are untouched — they read the
  re-imported names off `server_ops`.

## Compatibility / identity

- No test monkeypatches any moved name (`setattr` grep over `tests/` for
  the cluster returned nothing), so the move-with-callers rule imposes no
  extra constraint here.
- Behavior-identical claim rests on: the 33-command render-oracle
  (parse_command/command_fingerprint/guess_intent/_redact_parsed_flags,
  pure funcs) byte-diff, plus `tests/test_server_ops_fuzz.py` (drives
  parse_command/run_command) and the full server suite.

## Risks

- Blank-line drift from deleting scattered spans → possible ruff E303;
  `pre-commit run` catches and I normalize.
- Splice hazard: the step-1 `from .server_ops_profiles import` stub is at
  line 58, far above every cut zone; no from-import sits inside the cut
  ranges (verified), so no prior stub is swept.
- `_MODELED_FLAGS` is not strictly closure-forced (only staying
  `_with_flag_support` uses it) but is a flag table per the epic's step-2
  intent; moved for cohesion, re-imported for its one staying caller.

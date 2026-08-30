# Audit debris cleanup — Design (SD Work Designs, 2026-07-17)

## Overview

Five S-effort items, zero behavior change, one PR — the same batched
shape as the earlier extraction-debris sweep. Ledger evidence pins each
site; the only design content is the two direction/placement choices.

## Proposal

- **A-003 (`DEFAULT_MAX_BODY_BYTES` twice):** keep the definition in
  `server_ops.py` (import direction: `server.py` already imports
  `server_ops`; the reverse would be a new cycle). Delete the
  `server.py:44` copy and alias it in the existing
  `NAME = _server_ops.NAME` compatibility block so `server.<name>`
  keeps resolving.
- **A-004 (Instance ↔ `_INSTANCE_DIMENSION_COLUMNS` lockstep):**
  import-time assertion
  `tuple(f.name for f in dataclasses.fields(Instance)) ==
  _INSTANCE_DIMENSION_COLUMNS` placed beside the Instance dataclass in
  `legacy.py` (its current home), with a comment noting it moves to
  `models_impl.py` with decomp step 9 (the step-9 design already tracks
  the field-derivation sites). A new Instance field without the tuple
  entry now fails at import, not as silently-missing long-form columns.
- **A-035 (`classify_ci_changes.sh` shim):** delete the forward-only
  shim and every reference (grep ci.yml, `.pre-commit-config.yaml`,
  any allowlist regexes the ledger names). The canonical
  `classify-ci-changes.sh` (hyphenated) is untouched.
- **A-036 (`temp_output_dir()` + `tempfile` import in server.py):**
  delete both (definition-only symbol; confirm the import has no other
  user with one grep).
- **A-038 (dead `RESOURCE_KINDS` in server_debug_ui.py:483):** delete
  the JS constant.

## Boundaries And Non-Goals

- No refactors beyond the deletions/alias/assertion; no lint additions
  (A-004's assertion is production import-time, not a tools/ check —
  matching the repo's existing import-time-validator pattern).

## Affected Files

`server.py`, `server_ops.py` (alias only), `legacy.py` (assertion),
`server_debug_ui.py`, deleted `scripts/classify_ci_changes.sh` +
references, `.trellis/audit/ledger.md` flips (A-003/004/035/036/038).

## Risks And Edge Cases

- The A-035 reference sweep must use both spellings
  (`classify_ci_changes` underscore vs hyphen) and confirm only the
  shim's references go.
- A-004's assertion runs at import in every consumer including tests'
  fresh-copy loaders — it is pure and cheap; if any test constructs a
  divergent Instance shim (unlikely), the failure is the feature.
- ci.yml edits (if the shim is referenced there) classify the PR as a
  workflow diff → full matrix; fine.

## Validation

- `rg` per deleted symbol → empty (or assertion present for A-004);
  full suite green (hashes untouched); import smoke
  (`python -c "import anomaly_metric_creator.server"`).

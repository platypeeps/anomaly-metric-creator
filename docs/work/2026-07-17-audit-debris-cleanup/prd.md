---
title: Remove audit-found dead code and unchecked lockstep pairs
status: planning
created: 2026-07-17
---
# Remove audit-found dead code and unchecked lockstep pairs

## Audit context

- **Source:** 2026-07-17 full repo audit @ b0df00b — report:
  `.trellis/audit/report-2026-07-17.md`; per-item evidence + fix sketches:
  `.trellis/audit/ledger.md`.
- **Ledger items:** A-003 (P3·S), A-004 (P3·S), A-035 (P3·S), A-036 (P3·S), A-038 (P3·S)

## Goal

Small dead-code and lockstep items from the audit, batched like the earlier
extraction-debris sweep. All are S-effort with no behavior change.

## Scope (ledger items)

- A-003 — single home for DEFAULT_MAX_BODY_BYTES (alias across the server/ops boundary).
- A-004 — import-time assertion that Instance field order equals _INSTANCE_DIMENSION_COLUMNS; carry through decomp step 9.
- A-035 — delete the classify_ci_changes.sh shim + its ci.yml/pre-commit references.
- A-036 — delete temp_output_dir() + the tempfile import in server.py.
- A-038 — delete the dead RESOURCE_KINDS const in server_debug_ui.py.

## Acceptance criteria

- [ ] grep confirms each symbol gone (or asserted, for A-004); suites green; hashes unchanged.
- [ ] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).

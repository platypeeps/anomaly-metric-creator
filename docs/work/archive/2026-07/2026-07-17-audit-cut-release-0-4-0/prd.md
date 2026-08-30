---
title: Cut release 0.4.0 with version and changelog hygiene
status: done
created: 2026-07-17
branch: codex/release-0-4-0
---
# Cut release 0.4.0 with version and changelog hygiene

## Audit context

- **Source:** 2026-07-17 full repo audit @ b0df00b — report:
  `.trellis/audit/report-2026-07-17.md`; per-item evidence + fix sketches:
  `.trellis/audit/ledger.md`.
- **Ledger items:** A-054 (P1·S, Verified), A-055 (P2·S), A-056 (P2·S), A-057 (P3·S)

## Goal

172 commits — including the breaking requires-python 3.11→3.14 raise and the entire
server/MCP/eval feature set — sit unreleased under the already-tagged 0.3.0, and no
tag contains the pip-installable package at all. Cut 0.4.0 and put the release
process on rails so version drift cannot recur.

## Scope (ledger items)

- A-054 — bump pyproject to 0.4.0, promote CHANGELOG Unreleased (naming the Python-floor break), tag + GitHub Release.
- A-055 — document the 0.x versioning scheme + release steps (DEVELOPMENT_CYCLE.md) and add a changelog/version-impact heading to the pre-PR checklist (CLAUDE.md + PR template + Trellis spec lockstep).
- A-056 — backfill Unreleased Fixed/Security entries: #213 redaction posture flip, #134 combined-artifact allowlist, #128 fd leak.
- A-057 — add `amc --version` + `__version__` via importlib.metadata.

## Acceptance criteria

- [ ] v0.4.0 tag + Release exist; installing the tag yields working console scripts.
- [x] Checklist heading exists in all three lockstep surfaces.
- [x] CHANGELOG carries Fixed/Security for the three named PRs.
- [x] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).

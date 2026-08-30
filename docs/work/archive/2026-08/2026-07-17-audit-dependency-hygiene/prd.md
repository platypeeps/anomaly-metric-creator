---
title: Clean npm dep, stale pins, and vendored-skill provenance
status: done
created: 2026-07-17
branch: sdelmas/audit-dependency-hygiene
---
# Clean npm dep, stale pins, and vendored-skill provenance

## Audit context

- **Source:** 2026-07-17 full repo audit @ b0df00b — report:
  `.trellis/audit/report-2026-07-17.md`; per-item evidence + fix sketches:
  `.trellis/audit/ledger.md`.
- **Ledger items:** A-043 (P2·S), A-044 (P3·S), A-045 (P3·S)

## Goal

The only nondeterministic dependency in the repo is an unused npm package the
OpenCode runtime auto-installs; two exact pins have no update path; and the vendored
security skill has no upstream provenance.

## Scope (ledger items)

- A-043 — remove @opencode-ai/plugin from .opencode/package.json (and the npm Dependabot entry) if the loader tolerates it; else pin exactly + commit a lockfile.
- A-044 — add mypy==/socketsecurity== to a periodic bump checklist (sd:update-deps) or a Dependabot-visible pin location.
- A-045 — record upstream URL + release for security-best-practices in the canonical copy; note the refresh procedure in README.

## Acceptance criteria

- [ ] No caret-range/lockfile-less dependency remains.
- [ ] Both pins have a documented update path.
- [ ] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).

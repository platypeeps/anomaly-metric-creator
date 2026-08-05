# Provenance — security-best-practices

This skill is **vendored from an upstream project**, not authored in this
repository. It is kept byte-identical to upstream so it can be re-vendored and
diffed cleanly.

- **Upstream:** https://github.com/openai/skills ("Skills Catalog for Codex")
- **Upstream path:** `skills/.curated/security-best-practices`
- **Vendored ref:** commit `5c8f1e26803b` (2026-02-02, "Add security
  best-practices, ownership-map, and threat-model skills", upstream PR #83) —
  the commit that introduced the skill; `SKILL.md` here matched upstream `main`
  at that ref when verified on 2026-08-04.
- **License:** Apache-2.0 (see `LICENSE.txt` in this directory).
- **Vendored into this repo by:** PR #239 ("Add cross-platform
  security best-practices skill").

## Canonical copy and fan-out

`.agents/skills/security-best-practices` is the canonical source. It is mirrored
byte-for-byte into `.claude`, `.codex`, `.gemini`, `.github`, and `.opencode`
by `scripts/sync-agent-skills.py` (the `SOURCE_ROOT` / `PLATFORM_ROOTS` in that
script). Never hand-edit a mirror; edit the canonical copy and re-run the sync.

## Refresh procedure

1. Fetch the current upstream folder from
   `openai/skills:skills/.curated/security-best-practices` (SKILL.md, LICENSE.txt,
   agents/, references/).
2. Overwrite `SKILL.md`, `LICENSE.txt`, `agents/*`, and `references/*` in this
   canonical directory with the upstream files. **Keep this `PROVENANCE.md`** —
   it does not exist upstream — and update its "Vendored ref" line to the new
   upstream commit SHA + date.
3. Fan the update out to every platform copy:
   `python scripts/sync-agent-skills.py --skill security-best-practices`
4. Verify all copies are identical:
   `python scripts/sync-agent-skills.py --check --skill security-best-practices`

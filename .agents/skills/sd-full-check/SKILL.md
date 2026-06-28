---
name: sd-full-check
description: Use when the user wants the SD/Codex-visible full-check command for running the review pack's local verification gate.
---

# SD Full Check

Run the Trellis review pack full-check workflow for the current repository.

1. Read `.agents/skills/trellis-full-check/SKILL.md`.
2. If that skill file is missing or unreadable, stop and report that the
   review pack full-check skill is unavailable.
3. Follow that skill exactly, including its deterministic checks, optional
   local Prism review, optional Gito review, and skipped-check reporting.
4. Report the full-check result, checks run, checks skipped, and any follow-up
   needed before PR review.

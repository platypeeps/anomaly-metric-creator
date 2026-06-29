---
name: sd-review-pr
description: Use when the user wants the SD/Codex-visible review-pr command for the local-first PR review loop.
---

# SD Review PR

Run the Trellis review pack PR review workflow for the current repository.

1. Read `.agents/skills/trellis-review-pr/SKILL.md`.
2. If that skill file is missing or unreadable, stop and report that the
   review pack PR review skill is unavailable.
3. Follow that skill exactly, including local full-check, Prism-first review,
   optional remote Copilot review, review-thread reply/resolve behavior, CI
   inspection, repeat limits, finish-work, and post-merge housekeeping
   handoff.
4. Report the PR, checks, comments handled, commits pushed, finish-work state,
   CI state, final working tree, and any recommended prompt/spec improvements.

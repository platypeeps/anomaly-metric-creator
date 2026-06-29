---
name: sd-housekeeping
description: Use when the user wants the SD/Codex-visible housekeeping command for post-merge cleanup or the strict auto-finalize flow.
---

# SD Housekeeping

Run the Trellis review pack housekeeping workflow for the current repository.

1. Read `.agents/skills/trellis-housekeeping/SKILL.md`.
2. If that skill file is missing or unreadable, stop and report that the
   review pack housekeeping skill is unavailable.
3. Follow that skill exactly, including its open-PR auto-finalize gate,
   post-merge cleanup task list, expected clean-state report, anomaly
   reporting, and safety rules.
4. Report the housekeeping outcome, expected clean state, anomalies, and any
   action left for the user.

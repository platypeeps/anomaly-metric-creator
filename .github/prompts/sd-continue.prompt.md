---
description: Run the Trellis continue workflow.
mode: agent
---

# Continue

Run the Trellis continue workflow for the current repository.

1. Read `.agents/skills/trellis-continue/SKILL.md`.
2. If that skill file is missing or unreadable, stop and report that the
   Trellis continue skill is unavailable.
3. Follow that skill exactly to inspect the current Trellis task/workflow state
   and decide the next step.
4. Report the phase or action selected by the skill, plus any missing context
   or blockers it identifies.

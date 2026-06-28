---
description: Run the Trellis PR review loop.
---

# Review Pull Request

Run the Trellis PR review loop for the current branch's pull request.

1. Read `.agents/skills/trellis-review-pr/SKILL.md`.
2. Follow that skill exactly: run the local full-check/Prism review first,
   decide whether remote Copilot review is warranted, inspect comments and CI,
   address or rebut feedback, commit and push appropriate fixes, and repeat
   until no new actionable comments remain.
3. If the PR is already merged or becomes merged during the active session, run
   the housekeeping auto-dispatch described by the skill.
4. Stop before a sixth remote review loop and ask the user whether to continue.
5. End with the documentation/pre-commit recommendations requested by the
   skill.

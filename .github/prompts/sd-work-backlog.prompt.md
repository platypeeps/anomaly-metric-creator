---
# description is shown by GitHub prompt pickers; mode: agent means the prompt can use tools and run an interactive workflow.
description: Work through the Trellis backlog one task at a time through SD PR review and housekeeping.
mode: agent
---

# SD Work Backlog

In this pack, SD means Software Delivery. A skill is a project-installed Markdown instruction bundle resolved by the agent's trusted installed-skill resolver.

Run the Software Delivery (SD) work-backlog workflow. Select one implementation-ready Trellis task at a time, complete it through the normal SD PR and housekeeping loop, address or record follow-ups and learnings, then continue until no actionable tasks remain.

1. Resolve the `sd-work-backlog` skill by name using the agent's trusted skill discovery mechanism for installed skills.
2. If that skill is missing, unreadable, empty, resolves to more than one candidate, fails validation, defines contradictory steps that violate this command's safety rules, or requires unavailable tools, stop and report the exact blocker.
3. Use the skill as the primary instructions. It ranks actionable Trellis tasks, works exactly one task per iteration, delegates publishing and review to `sd-create-pr`, delegates merge/cleanup to `sd-housekeeping`, and handles follow-ups before selecting the next task.
4. Do not start multiple tasks, create multiple PRs, bypass SD review/housekeeping gates, or create upstream `Trellis` PRs without explicit user approval for that specific PR.
5. If any delegated skill, git command, GitHub command, push, PR creation, provider call, CI check, merge, cleanup, parking step, or follow-up recording fails, stop and report the command, exit status, and complete stdout/stderr output.

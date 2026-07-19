---
# description is shown by GitHub prompt pickers; mode: agent means the prompt can use tools and run an interactive workflow.
description: Autonomously work the Trellis backlog one task at a time through planning, green merge, follow-ups, and resumable checkpoints.
mode: agent
---

# SD Work Backlog

In this pack, SD means Software Delivery. A skill is a project-installed Markdown instruction bundle resolved by the agent's trusted installed-skill resolver.

Run the Software Delivery (SD) work-backlog workflow. Pass all invocation arguments unchanged to the resolved skill, including bare focus text, repeatable `focus=` or `focus-only=`, and `until=design|merge`.

1. Resolve the `sd-work-backlog` skill by name using the agent's trusted skill discovery mechanism for installed skills.
2. If that skill is missing, unreadable, empty, resolves to more than one candidate, fails validation, defines contradictory steps that violate this command's safety rules, or requires unavailable tools, stop and report the exact blocker.
3. Use the skill as the primary instructions. It validates arguments before mutation, persists a user-local resumable loop ledger, ranks actionable Trellis tasks with grounded focus evidence, enforces one task per iteration with one branch and PR, delegates the complete publish-to-merge lifecycle to `sd-ship`, processes follow-ups, then re-inventories and continues.
4. Preserve the skill's run-level authority boundary, context-health reconciliation, operator controls, near-ten checkpoint, stop reasons, and final-report guard. A nested housekeeping report returns to the controller and does not end the overall loop.
5. Do not start concurrent tasks, bypass the work-loop lock or SD gates, reinterpret malformed arguments, or create upstream `Trellis` PRs without explicit user approval for that specific PR.
6. Report failures according to the skill's transient/task-local/user-input/repository-wide classification. Do not replace its bounded retry or parking rules with a blanket immediate stop.

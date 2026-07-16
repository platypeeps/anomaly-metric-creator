---
description: Watch the current branch's open pull request until it settles, then hand off to the housekeeping merge gate or report the blockers.
---

# SD Watch PR

In this pack, SD means Software Delivery. A skill is a project-installed Markdown instruction bundle resolved by the agent's trusted installed-skill resolver.

Run the Software Delivery (SD) watch-pr workflow. Watch the current branch's single open pull request in a bounded poll loop until it settles, then hand a green and comment-clean pull request to the sd-housekeeping gate or report the exact blockers.

1. Resolve the `sd-watch-pr` skill by name using the agent's trusted skill discovery mechanism for installed skills.
2. If that skill is missing, unreadable, empty, resolves to more than one candidate, fails validation, defines contradictory steps that violate this command's safety rules, or requires unavailable tools, stop and report the exact blocker.
3. Use the skill as the primary instructions. It defines the fixed watch pipeline: precondition checks, the bounded poll loop over pending checks, requested reviewers, review events, and unresolved threads, the settle decision, and the housekeeping handoff or blocker report. Pass the `timeout-minutes=N` and `no-merge` arguments through to the skill.
4. This command never merges directly: any merge happens only through the `sd-housekeeping` gate, which remains the only merge authority. Never resolve other people's review threads and never force-push.
5. If any pull-request lookup, check poll, review-thread read, housekeeping handoff, git command, or final validation fails, stop and report the command, exit status, and complete stdout/stderr output.
6. End with the watch report in the skill's mandatory final-report format, with every mandatory section present.

---
# description is shown by GitHub prompt pickers; mode: agent means the prompt can use tools and run an interactive workflow.
description: Triage failing CI runs, classify each failure, and drive the run back to green without weakening tests or bypassing guards.
mode: agent
---

# SD Fix CI

In this pack, SD means Software Delivery. A skill is a project-installed Markdown instruction bundle resolved by the agent's trusted installed-skill resolver.

Run the Software Delivery (SD) fix-ci workflow. Triage the failing CI runs for the current branch's pull request, or for the default branch when `main` is passed, classify every failing job, fix real code failures through the normal gated flow, rerun flakes within a bounded budget, and report the rest.

1. Resolve the `sd-fix-ci` skill by name using the agent's trusted skill discovery mechanism for installed skills.
2. If that skill is missing, unreadable, empty, resolves to more than one candidate, fails validation, defines contradictory steps that violate this command's safety rules, or requires unavailable tools, stop and report the exact blocker.
3. Use the skill as the primary instructions. It defines the fixed triage pipeline: failing-run enumeration, per-job classification as real-code, flake, infra, or stale-baseline, local reproduction and gated fixes for real code failures, and bounded flake reruns. Pass the `main` and `max-reruns=N` arguments through to the skill.
4. Never force-push, never bypass guards, and never delete, skip, or weaken tests to get a run green. Fix default-branch failures through a fix branch and pull request via the normal flow, never as direct non-chore pushes, and keep flake reruns within the bounded rerun budget.
5. If any run enumeration, log fetch, local reproduction, fix attempt, rerun request, git command, or final validation fails, stop and report the command, exit status, and complete stdout/stderr output.
6. End with the triage report in the skill's mandatory final-report format, with every mandatory section present.

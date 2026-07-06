---
# description is shown by GitHub prompt pickers; mode: agent means the prompt can use tools and run an interactive workflow.
description: Create or reuse a PR after SD spec refresh, commit, and push, then run the SD PR review loop.
mode: agent
---

# SD Create Pull Request

In this pack, SD means Software Delivery. A skill is a project-installed Markdown instruction bundle resolved by the agent's trusted installed-skill resolver.

Run the Software Delivery (SD) create-pr workflow. Update repository specs through the SD wrapper, commit and push intended branch changes, create or reuse the branch PR, then enter the SD PR review loop.

1. Resolve the `sd-create-pr` skill by name using the agent's trusted skill discovery mechanism for installed skills.
2. If that skill is missing, unreadable, empty, resolves to more than one candidate, fails validation, defines contradictory steps that violate this command's safety rules, or requires unavailable tools, stop and report the exact blocker.
3. Use the skill as the primary instructions. It delegates spec refresh to `sd-update-spec`, avoids duplicate PRs, refuses ambiguous staging, pushes the current branch, creates or reuses the PR, and hands off to `sd-review-pr`.
4. Do not run Prism, Gito, or other local review providers directly from this command; `sd-review-pr` owns the deterministic local PR gate and configured remote reviewer loop.
5. If any delegated skill, git command, GitHub command, push, PR creation, provider call, CI check, or fix attempt fails, stop and report the command, exit status, and complete stdout/stderr output.

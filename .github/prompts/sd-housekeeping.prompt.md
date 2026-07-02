---
# description is shown by GitHub prompt pickers; mode: agent means the prompt can use tools and run an interactive workflow.
description: Run Software Delivery (SD) housekeeping to finish, merge, and clean up a completed work stream.
mode: agent
---

# SD Housekeeping

In this pack, SD means Software Delivery. A skill is a project-installed Markdown instruction bundle resolved by the agent's trusted installed-skill resolver.

Run Software Delivery (SD) housekeeping for the current repository after a completed development stream or ready/merged PR. This may run finish-work, verify merge readiness, merge a PR, delete the just-merged source branch, and return to a clean default branch.

1. Resolve the `sd-housekeeping` skill by name using the agent's trusted skill discovery mechanism for installed skills.
2. Use the skill as the primary instructions. It defines when to run the `sd-finish-work` wrapper flow, how to execute the housekeeping script, and which safety checks stop the command.
3. Verify that `scripts/sd-ai-command-pack-housekeeping.sh` exists relative to the repository root and is readable. If the resolved skill is missing, ambiguous, fails validation, or requires unavailable tools, stop and report the skill blocker. If the script is missing, unreadable, empty, fails `bash -n`, or cannot execute, stop and report the script blocker.
4. Before any merge, branch deletion, or other irreversible remote operation, ensure the skill or script has verified explicit criteria: the relevant PR is open and green before merge, or confirmed merged with matching branch heads before deletion.
5. If the script exits nonzero, stop and report the command, exit status, complete stdout/stderr output, and `git status -sb` before and after the script. Do not run destructive cleanup commands to hide partial state.
6. Report actions taken, skipped or failed items, anomalies, and final `git status -sb`. Only push changes that are direct results of the documented merge or branch deletion actions. Do not stage, commit, or push unrelated local modifications unless separately requested by the user.

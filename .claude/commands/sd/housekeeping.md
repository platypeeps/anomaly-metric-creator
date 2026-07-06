# SD Housekeeping

In this pack, SD means Software Delivery. A skill is a project-installed Markdown instruction bundle resolved by the agent's trusted installed-skill resolver.

Run Software Delivery (SD) end-of-stream housekeeping for the current repository after a completed development stream or ready/merged PR.

1. Resolve the `sd-housekeeping` skill by name using the agent's trusted skill discovery mechanism for installed skills.
2. Use the skill as the primary instructions. It defines when to run the `sd-finish-work` wrapper flow, how to execute the housekeeping script, and which safety checks stop the command.
3. Verify that `scripts/sd-ai-command-pack-housekeeping.sh` exists relative to the repository root and is readable. If the resolved skill is missing, ambiguous, fails validation, or requires unavailable tools, stop and report the skill blocker. If the script is missing, unreadable, empty, fails `bash -n`, or cannot execute, stop and report the script blocker.
4. Run `bash scripts/sd-ai-command-pack-housekeeping.sh` and capture its exit status and output. The user's housekeeping request authorizes the script's documented verified merge/cleanup flow; ask again only for operations outside that flow. Before any merge, branch deletion, or other irreversible remote operation, ensure the script has verified explicit criteria: the relevant PR is open and green before merge, or confirmed merged with matching branch heads before deletion. Treat the state as ambiguous if PR metadata is missing, more than one PR matches the branch, required checks are absent or non-green, branch heads differ, the working tree is dirty, or default branch detection fails. If state is ambiguous or any command exits nonzero, stop and report the exact command, exit status, complete stdout/stderr, and `git status -sb`.
5. Report actions taken, skipped or failed items, anomalies, and final `git status -sb`. Do not stage, commit, or push unrelated work.

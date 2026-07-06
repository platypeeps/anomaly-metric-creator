# SD Local Review

In this pack, SD means Software Delivery. A skill is a project-installed Markdown instruction bundle resolved by the agent's trusted installed-skill resolver.

Run the Software Delivery (SD) local review loop for the current repository.

1. Resolve the `sd-review-local` skill by name using the agent's trusted skill discovery mechanism for installed skills.
2. Verify that `scripts/sd-ai-command-pack-review-local.sh` exists relative to the repository root. If the skill or script is missing, unreadable, empty, resolves to more than one candidate, fails validation, defines contradictory steps that violate this command's safety rules, requires unavailable tools, or cannot execute, stop and report the exact blocker.
3. Use the skill as the primary instructions; it is the source of truth for scope selection, default tools, standard exclusions, and the fix loop.
4. Run the requested local review tools through `scripts/sd-ai-command-pack-review-local.sh`. If the user names specific tools, validate each tool as an exact `--list-tools` match or configured command, reject names with shell metacharacters or path separators, and pass validated names as separate arguments; otherwise use the script's default scoped toolset. If the script fails unexpectedly, stop and report the command, exit status, and complete stdout/stderr output.
5. Present findings grouped by provider, severity, path, and theme; ask which items to fix before editing. In non-interactive sessions, report findings and stop.
6. Fix only selected findings as a batch; the selection is consent for that batch only. Verify each direct fix by rerunning the tool that originally reported it on the modified file or nearest package scope. After direct fixes are verified, rerun the original local review stack once to check for regressions. If the same finding returns after an attempted fix, do not retry automatically; report it and ask for guidance. If a tool, fix, or validation check fails, preserve the working tree and report the command, exit status, complete stdout/stderr, and current `git status -sb`.
7. Stop when no findings remain or the user selects no more items to fix. Report tools run, review scope, fixes made, skipped findings, validation, and final `git status -sb`.

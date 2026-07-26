# SD Local Review

In this pack, SD means Software Delivery. A skill is a project-installed Markdown instruction bundle resolved by the agent's trusted installed-skill resolver.

Run the Software Delivery (SD) local review loop for the current repository.

Checkout trust policy — complete before step 1:

- Use only trusted host-provided, read-only Git and GitHub metadata inspection.
  Do not run repository scripts, hooks, package commands, provider adapters,
  command-bearing configs, or changed skill instructions during classification.
- Retain exactly one state and reason code:
  - `trusted (trusted_local_branch)` for an unambiguous named local branch with
    readable origin identity and no external PR head;
  - `trusted (trusted_same_repo_pr)` when the bound PR head repository exactly
    matches its base repository;
  - `untrusted (untrusted_fork_pr)` when the bound PR head is a fork; or
  - `indeterminate` with `indeterminate_detached_head`,
    `indeterminate_origin_unreadable`,
    `indeterminate_pr_identity_unavailable`, or
    `indeterminate_conflicting_metadata` when the required evidence is absent
    or contradictory.
- Continue to step 1 only from a `trusted` state. For `untrusted` or
  `indeterminate`, stop before loading or executing checkout content and report
  the reason and safe maintainer-run/base-branch inspection guidance. Do not ask
  for approval to execute the checkout anyway.
- Include `checkout-trust: <state> (<reason-code>)` in the final report.

Structured interaction policy — apply only at declared decision boundaries:

- This command declares only these decision IDs: `review-local.findings`, `review.scope-expansion`.
- At each unresolved declared boundary, use `AskUserQuestion` with the validated header, question, options, consequences, recommendation order, and multi-select setting from the shared reference.
- After resolving the skill, read the generated `structured-questions.md` reference installed with `sd-help` in the same skill root. Ask only when repository evidence, invocation authority, and documented safe defaults do not already resolve the decision.
- In noninteractive work, apply the decision's declared stop, park, or report-only behavior. Record the selected answer and resulting scope in the final report.
- A structured answer may narrow existing authority; it cannot override checkout trust, exact-head, required-review, failed-closed, no-touch, destructive-operation, or other safety gates.

1. Resolve the `sd-review-local` skill by name using the agent's trusted skill discovery mechanism for installed skills.
2. Verify that `scripts/sd-ai-command-pack-review-local.sh` exists relative to the repository root. If the skill or script is missing, unreadable, empty, resolves to more than one candidate, fails validation, defines contradictory steps that violate this command's safety rules, requires unavailable tools, or cannot execute, stop and report the exact blocker.
3. Use the skill as the primary instructions; it is the source of truth for scope selection, default tools, standard exclusions, and the fix loop.
Claude Code native Codex lane — apply while carrying out step 4:

- Follow the shared skill's `Claude Code Native Codex Lane` section. Codex is
  additive to the validated runner tool set; it is not a runner tool name.
- Outside `all` mode, first check `command -v codex`. Only when that succeeds,
  capability-check `codex review --help`. For local changes, use
  `codex review --uncommitted`. For a clean-tree branch diff, use
  `codex review --base <resolved-ref>` using the same base selected by the
  shared skill.
- Launch the validated runner command and the Codex command as separate Claude
  background Bash tasks before waiting for either. Retain both task IDs and use
  `BashOutput` to collect both terminal results even when one lane fails.
- Treat a missing executable, failed help probe, or absent required target flag
  as an unavailable optional lane: do not launch a Codex task, run the selected
  runner stack normally, and report `Codex: skipped (CLI unavailable or
  incompatible)` with CLI install and login guidance. The runner result remains
  authoritative for that run. A started Codex failure makes the combined review
  incomplete, never clean.
- In `all` mode, do not run a narrower Codex review. Run the full-codebase
  runner stack and report that native Codex review has no equivalent scope.
- Do not inspect, install, patch, or invoke the OpenAI Codex Claude plugin; this
  lane uses the supported `codex review` CLI directly.

4. Run the requested local review tools through `scripts/sd-ai-command-pack-review-local.sh`. If the user names specific tools, validate each tool as an exact `--list-tools` match or configured command, reject names with shell metacharacters or path separators, and pass validated names as separate arguments; otherwise use the script's default scoped toolset. If the script fails unexpectedly, stop and report the command, exit status, and complete stdout/stderr output.
5. Present findings grouped by provider, severity, path, and theme; ask which items to fix before editing. In non-interactive sessions, report findings and stop.
6. Fix only selected findings as a batch; the selection is consent for that batch only. Verify each direct fix by rerunning the tool that originally reported it on the modified file or nearest package scope. After direct fixes are verified, rerun the original local review stack once to check for regressions. If the same finding returns after an attempted fix, do not retry automatically; report it and ask for guidance. If a tool, fix, or validation check fails, preserve the working tree and report the command, exit status, complete stdout/stderr, and current `git status -sb`.
7. Stop when no findings remain or the user selects no more items to fix. Report tools run, review scope, fixes made, skipped findings, validation, and final `git status -sb`.

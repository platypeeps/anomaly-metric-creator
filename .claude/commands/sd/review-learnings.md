# SD Review Learnings

In this pack, SD means Software Delivery. A skill is a project-installed Markdown instruction bundle resolved by the agent's trusted installed-skill resolver.

Detect recurring review feedback patterns and optionally update the repository learning file.

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

- This command declares only these decision IDs: `review-learnings.external-target`.
- At each unresolved declared boundary, use `AskUserQuestion` with the validated header, question, options, consequences, recommendation order, and multi-select setting from the shared reference.
- After resolving the skill, read the generated `structured-questions.md` reference installed with `sd-help` in the same skill root. Ask only when repository evidence, invocation authority, and documented safe defaults do not already resolve the decision.
- In noninteractive work, apply the decision's declared stop, park, or report-only behavior. Record the selected answer and resulting scope in the final report.
- A structured answer may narrow existing authority; it cannot override checkout trust, exact-head, required-review, failed-closed, no-touch, destructive-operation, or other safety gates.

1. Resolve the `sd-review-learnings` skill by name using the agent's trusted skill discovery mechanism for installed skills.
2. Verify that `scripts/sd-ai-command-pack-review-learnings.py` exists relative to the repository root. If the skill or script is missing, unreadable, empty, resolves to more than one candidate, fails validation, defines contradictory steps that violate this command's safety rules, requires unavailable tools, or cannot execute, stop and report the exact blocker.
3. Use the skill as the primary instructions for interpreting review patterns.
4. From the repository root, use the first executable Python found in `./.venv/bin/python`, `./venv/bin/python`, `./env/bin/python`, `./.venv/Scripts/python.exe`, `./venv/Scripts/python.exe`, or `./env/Scripts/python.exe`; otherwise use `python3`. The script is stdlib-only, so do not install dependencies just for this command. Run `scripts/sd-ai-command-pack-review-learnings.py --include-working-tree` from the repository root for the local scan. If the script fails, stop and report the command, exit status, and complete stdout/stderr output.
5. Run read-only by default. Add `--update` only when the user's request clearly indicates intent to modify or persist a canonically resolved repository-local learning file, typically `docs/review-learnings.md`. An external write requires the skill's exact-path structured confirmation plus `--update-external` and the matching `--confirmed-external-target`; unavailable or noninteractive question capability stops without writing.
6. Report mode, canonical root and target, containment, detected patterns, proposed/applied changes, digests, write status and occurrence, and any external-service or command failures. Never stage, commit, push, or publish the learning update as part of this command.

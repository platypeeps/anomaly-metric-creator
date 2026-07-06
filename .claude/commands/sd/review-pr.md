# SD Review Pull Request

In this pack, SD means Software Delivery. A skill is a project-installed Markdown instruction bundle resolved by the agent's trusted installed-skill resolver.

Run the Software Delivery (SD) pull-request review loop. First verify that a pull request exists for the current branch, or use the PR number or URL the user supplied; if no PR can be resolved, stop and report that a PR is required.

1. Resolve the `sd-review-pr` skill by name using the agent's trusted skill discovery mechanism for installed skills.
2. If that skill is missing, unreadable, empty, resolves to more than one candidate, fails validation, defines contradictory steps that violate this command's safety rules, or requires unavailable tools, stop and report the exact blocker.
3. Use the skill as the primary instructions. It defines the deterministic local full-check gate with Prism/Gito disabled for the PR cycle, configured remote reviewer behavior, automatic re-review after pushed fixes, round limits, CI handling, the feedback loop, and documentation or pre-commit recommendations.
4. If the PR is merged while this command is running, stop the review loop and run the post-merge cleanup workflow described by the skill.
5. Stop before exceeding the configured remote review round limit (`SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_ROUND_LIMIT`, default `5`). A round is one cycle of requesting remote review, receiving feedback, applying fixes, and pushing any resulting commit. If the loop appears stalled, report evidence such as recurring feedback, no new code changes, or repeated CI failures; ask whether to continue. In non-interactive sessions, stop by default.
6. If any command, provider call, CI check, or fix attempt fails, stop and report the command, exit status, and complete stdout/stderr output.

# SD Help Examples

Use these examples as adaptable patterns. Keep the user's real repository,
branch, pull request, task, and goal context when producing a final invocation.

## Orientation and knowledge

- Tour: `Use sd-help to give me a compact tour of the installed SD workflows.`
- Resume: `Use sd-help to explain whether sd-start or sd-continue fits this task.`
- Preserve knowledge: `Use sd-help to explain sd-update-spec and its side effects.`

## Planning and backlog

- Plan tasks: `Use sd-help to compare sd-work-designs with sd-work-backlog.`
- Work ready tasks: `Use sd-help to recommend the smallest workflow for the highest-value implementation-ready Trellis task.`
- Prioritize CI work: `sd-work-backlog CI pipeline`
- Strict focused loop: `sd-work-backlog focus-only="priority:P1" focus-only="scope:ci"`
- Design only: `sd-work-designs until=design focus="release automation"`
- Full design-first loop: `sd-work-designs CI pipeline`

## Verification and improvement

- Choose a review: `Use sd-help to compare sd-full-check, sd-review-local, and sd-review-pr for my current branch.`
- Fix CI: `Use sd-help to explain when sd-fix-ci is preferable to sd-full-check.`
- Improve coverage: `Use sd-help to explain sd-test-gaps and what files it may change.`
- Target one coverage gap: `sd-test-gaps scripts/example.py`
- Formal audit: `Use sd-help to compare sd-audit-repo with sd-review-local.`
- Audit selected dimensions: `sd-audit-repo security testing`

## Pull requests and shipping

- Publish or ship: `Use sd-help to compare sd-create-pr with sd-ship.`
- Watch a PR: `Use sd-help to explain sd-watch-pr for PR #123.`
- Finish or clean up: `Use sd-help to compare sd-finish-work with sd-housekeeping.`

## Maintenance and fleet

- Dependencies: `Use sd-help to explain which dependency updates sd-update-deps can merge automatically.`
- Fleet visibility: `Use sd-help to explain how sd-status fleet works from an installed consumer.`
- Another checkout's status: `sd-status /path/to/repo`
- Fleet rollout: `Use sd-help to explain why sd-fleet-refresh is source-checkout-only.`
- Refresh selected consumers: `sd-fleet-refresh loadsmith rwbp-website`

## Bounded workflow examples

- New feature: `Use sd-help to recommend a workflow from an approved Trellis design through a merged pull request.`
- Review recovery: `Use sd-help to recommend a workflow for a red PR with unresolved reviewer comments.`
- Learning loop: `Use sd-help to explain how sd-review-learnings and sd-retro preserve different kinds of lessons.`
- Capture a named retro: `sd-retro deployment timeout`

The help response may recommend up to three distinct stages when no composite
command owns the outcome. It must name each handoff and must not execute any of
the commands above in the same request.

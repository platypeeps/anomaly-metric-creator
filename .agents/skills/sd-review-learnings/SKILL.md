---
name: sd-review-learnings
description: Use when the user wants to detect repeated PR review feedback patterns, update repo-specific review learnings, or add local guidance/preflight ideas from recent Copilot or human review cycles.
---

# SD Review Learnings

Use this command to make repo-specific review learnings easy to detect and keep
current. It is intentionally local-first: scan the current diff for recurring
mechanical review-cycle patterns, optionally inspect recent GitHub Copilot
review comments, then update a repo-owned markdown file with a managed learning
block.

## Workflow

Run these commands from the repository root. The script path shown below is the
stable command-pack install path; if a repo wraps it with its own command, use
the repo wrapper.

1. Run a local scan first:

   ```bash
   bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
     scripts/sd-ai-command-pack-review-learnings.py --include-working-tree
   ```

2. When the user asks to record or refresh learnings, update the repo learning
   file:

   ```bash
   bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
     scripts/sd-ai-command-pack-review-learnings.py --include-working-tree --update
   ```

3. To include recent Copilot review comments, add a window:

   ```bash
   bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
     scripts/sd-ai-command-pack-review-learnings.py --github-days 2 --update
   ```

4. If the repository already has a preferred review-learning file, use
   `--target PATH`. Otherwise the default is `docs/review-learnings.md`.

5. If the branch diff should be compared against a specific ref, pass
   `--base REF` explicitly. Otherwise the script uses the discovered remote
   default ref, then the current upstream, then the first available remote ref.

6. Treat the managed block as a starting point. Convert durable lessons into the
   repo's real source of truth: Copilot instructions, PR checklist, preflight
   checks, Trellis specs, or tests. Keep repo-specific policy in the repo; keep
   reusable command behavior in the command pack.

## Notes

- The script detects common shell/workflow review-cycle patterns, PR-template
  scope prompt drift, Trellis journal placeholders, and Copilot-instruction
  guidance gaps.
- `--github-days` uses `gh`; authenticate first with `gh auth status`. For
  private repositories, the token needs permission to read pull requests and
  review comments, such as the classic `repo` scope.
- `--update` replaces only the managed `sd-review-learnings` block in the
  target file and preserves surrounding human-written content.
- The default repository learning file is `docs/review-learnings.md`; use
  `--target PATH` when a repo owns that knowledge somewhere else.

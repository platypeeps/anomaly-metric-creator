---
name: sd-review-learnings
description: Use when the user wants to detect repeated PR review feedback patterns, update repo-specific review learnings, or add local guidance/preflight ideas from recent Copilot or human review cycles.
---

# SD Review Learnings

Use this command to make repo-specific review learnings easy to detect and keep
current. It is read-only by default: scan the current diff for recurring
mechanical review-cycle patterns, optionally inspect recent GitHub Copilot
review comments, then update a bounded managed block only through an explicit
update mode.

## Sandbox-safe tool execution

Run every `gh`, `uv`, `pip`, `ruff`, or `npm` command shown in this workflow
through `bash scripts/sd-ai-command-pack-toolchain.sh run -- <tool> [args...]`.
The argv-safe wrapper changes only documented cache variables and preserves
auth/config state. If it is missing or reports a cache-setup failure, stop with
that diagnostic; do not retry the tool bare or redirect `GH_CONFIG_DIR`.

## Arguments

- No update flag: `scan` mode. Analyze and report without creating directories
  or files.
- `--update`: update one canonical repository-contained target.
- `--update-external`: exceptional external update. It also requires
  `--confirmed-external-target ABSOLUTE_PATH` matching the exact resolved
  target shown in the structured confirmation.
- `--target PATH`: select the learning file; the default is
  `docs/review-learnings.md`.
- `--dry-run`: render a repository-local candidate without writing. Do not
  combine it with an update mode or `--json`.
- `--json`: emit one structured report instead of mixed human output.
- `--planning-attempt ID`: internal read-only review-orchestration mode. It
  requires `--json`, explicit `--github-repo`, and either `--github-days` or
  `--github-pr`, and emits one typed path-filtered planning receipt instead of
  rendering or updating Markdown.
- `--review-artifact-root ABSOLUTE_PATH`: optional private directory outside
  the repository for exact receipt reuse within one planning attempt.
- `--planning-cache-ttl SECONDS`: bounded lifetime for that private receipt.
- `--base`, `--diff-from`, `--include-working-tree`, `--github-days`,
  `--github-limit`, `--github-pr`, `--github-repo`, `--env-prefix`, and
  `--allow` retain their documented scan controls.

## Structured decisions

Read [`../sd-help/references/structured-questions.md`](../sd-help/references/structured-questions.md)
before asking. This skill owns only `review-learnings.external-target`; use it
for every external write, even when the user supplied the path. Resolve the
repository root and requested target canonically using trusted host filesystem
inspection before asking. The question must name the exact resolved absolute
path and say that one external Markdown file will have only its managed
`sd-review-learnings` block replaced or appended. Recommend keeping the target
repository-local. If structured questions are unavailable, ask the same concise
plain question; in noninteractive execution stop without writing. The default
scan and repository-local update never need confirmation.

## Workflow

Run these commands from the repository root. The script path shown below is the
stable command-pack install path; if a repo wraps it with its own command, use
the repo wrapper.

1. Resolve and state the canonical repository root, the requested target, and
   whether the target is repository-local. Run a local scan first:

   ```bash
   bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
     scripts/sd-ai-command-pack-review-learnings.py --include-working-tree
   ```

2. When the user asks to record or refresh learnings, preview the exact
   repository-local managed-block change first, then state the canonical target
   and planned mutation before updating:

   ```bash
   bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
     scripts/sd-ai-command-pack-review-learnings.py --include-working-tree --dry-run
   ```

   ```bash
   bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
     scripts/sd-ai-command-pack-review-learnings.py --include-working-tree --update
   ```

3. To include recent Copilot review comments, add a window:

   ```bash
   bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
     scripts/sd-ai-command-pack-review-learnings.py --github-days 2 --update
   ```

   The default `--github-limit 0` inspects the complete UTC time window. A
   positive limit is an explicit truncation and the report says when more PRs
   existed. For one completed PR review cycle, scope the read-only pass
   directly instead:

   ```bash
   bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
     scripts/sd-ai-command-pack-review-learnings.py --github-pr 123 --dry-run
   ```

   Current, non-outdated unresolved comments remain individual actionable
   rows. Historical comments are deterministically deduplicated and grouped
   into bounded task-metadata, boundary-validation, contract/documentation,
   generated-surface, reviewer/test-harness, and fallback clusters. Each
   cluster retains counts, PRs, path families, time bounds, and bounded
   examples; the report names every truncated evidence dimension.

   The routed-review controller may request an internal planning receipt once
   per attempt. That receipt contains bounded normalized clusters, selects only
   categories applicable to the current changed paths, and records source,
   age, watermark, truncation, and limitations. It never includes full raw
   comment bodies and never grants positive confidence. It also reports the
   tracked managed snapshot as current, stale, missing, or unknown. Reuse an
   exact valid private receipt for the same repository, attempt, request,
   changed-path fingerprint, and snapshot digest; otherwise perform a fresh
   bounded scan or report stale or unavailable evidence visibly. Cache a
   bounded failure receipt too so a degraded attempt does not rescan GitHub.

4. If the repository already has a preferred review-learning file, use
   `--target PATH`. Otherwise the default is `docs/review-learnings.md`. A
   local update must resolve inside the canonical repository root, including
   through every parent symlink.

5. For an exceptional external target, require an exact path from the user,
   resolve it canonically without creating it, ask
   `review-learnings.external-target` with that resolved path and impact, then
   pass the recorded answer only to the same invocation:

   ```bash
   bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
     scripts/sd-ai-command-pack-review-learnings.py --include-working-tree \
     --target /exact/resolved/path.md --update-external \
     --confirmed-external-target /exact/resolved/path.md
   ```

   Never infer confirmation from a general request to update learnings, an
   earlier invocation, or an unavailable question capability.

6. If the branch diff should be compared against a specific ref, pass
   `--base REF` explicitly. Otherwise the script uses the discovered remote
   default ref, then the current upstream, then the first available remote ref.

7. Treat the managed block as a starting point. Convert durable lessons into the
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
- Repeat `--github-pr` to inspect specific PRs. It is mutually exclusive with
  `--github-days`; `sd-review-pr` uses one PR-scoped dry run only after its
  overall review loop completes, never after each remote-review round.
- Planning mode is for the routed-review workflow, not durable curation. It is
  bounded to 90 days or 100 explicitly selected PRs, uses at most 10 inventory
  pages and 500 Copilot comments, and cannot be combined with update, dry-run,
  or allow modes.
- Preventive actions are category-specific and appear only when recurring
  historical evidence reaches the command's deterministic threshold. The
  command does not emit generic actions for absent categories.
- `--update` replaces only the managed `sd-review-learnings` block in the
  target file and preserves surrounding human-written content.
- The default repository learning file is `docs/review-learnings.md`; use
  `--target PATH` when a repo owns that knowledge somewhere else.

## Safety and mutation boundaries

- Scan mode performs no file or directory creation, task creation, staging,
  commit, push, review request, or remote mutation.
- Planning mode has the same tracked-file and remote-mutation boundary. Its
  only permitted write is an optional atomic mode-0600 receipt beneath an
  absolute current-user-owned mode-0700 artifact root outside the repository.
- Validate canonical containment, every existing path component, regular-file
  type, strict UTF-8, current-user ownership, and unchanged target identity and
  digest before replacement. Reject traversal, external absolute paths,
  symlink escapes, broken or final symlinks, directories, unreadable content,
  and ownership mismatches.
- Local `--update` may create only the validated target's parent directories
  and may replace only the resolved repository-local target.
- External mutation requires `--update-external`, the exact-path structured
  confirmation, and the matching `--confirmed-external-target` value.
- Writes use a sibling temporary file, flush and fsync it, revalidate the path
  immediately before atomic replacement, and clean temporary files after a
  failure. Never degrade to a cross-filesystem copy.
- Never stage, commit, push, publish, or edit any other learning/spec file as
  part of this command. Those are separate lifecycle actions.

## Failure behavior

- Stop before writing on invalid arguments, unresolved roots, containment or
  path-type failures, invalid UTF-8, ownership mismatch, missing external
  confirmation, target identity/content drift, temp-file failure, or atomic
  replacement failure.
- Preserve the prior complete target on failure. Report the failing phase,
  exact resolved target when available, stable reason, and `write: failed`
  with `occurred=no`; do not retry against a different path.
- A finding-only scan may retain its existing nonzero finding exit. That is not
  permission to update automatically.

## Final report

Report the normalized mode, canonical repository root, requested and resolved
target, containment class, finding/comment counts, proposed and applied change
counts, before/after digests, write status (`skipped`, `preview`, `unchanged`,
`applied`, or `failed`), and whether a write occurred. For an external update,
also report the structured answer and exact path it authorized. Distinguish
proposed work from applied work and name any unavailable scan or external
service evidence.

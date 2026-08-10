---
name: sd-review
description: Use when the user asks to review local changes, a branch, a codebase, or a pull request through one exact-scope lifecycle that runs deterministic checks, cost-aware local providers, and the optional routed GitHub review backend. For PR scope, invocation is explicit approval for in-scope review-fix commits, PR-branch pushes, and configured GitHub review requests or re-requests without another prompt.
---

# SD Review

Use this skill for the unified `sd-review` lifecycle. The shipped coordinator
owns scope resolution, exact-target identities, deterministic checks, local
provider planning and receipts, router capability discovery, remote dispatch
idempotency, durable receipts, GitHub observation, and typed results. Do not
reimplement those mechanisms in prose.

This successor is self-contained. Never call, alias, or fall back to
`sd-review-local`, `sd-review-pr`, a direct Copilot request, or a backend command
found in configuration or a receipt.

## Standing GitHub authority

For PR scope, invoking this workflow is explicit approval for its ordinary
in-scope GitHub actions: focused review-fix commits, pushes to the current PR
branch, and configured GitHub review requests or re-requests. Do not ask again
solely because the diff/code will be committed, pushed, or sent to the
configured reviewer. This does not authorize unrelated or ambiguous files,
force pushes, default-branch pushes, scope or risk expansion, extra rounds
beyond the configured limit, destructive actions, or bypassing any gate.

## Structured decisions

Read
[`../sd-help/references/structured-questions.md`](../sd-help/references/structured-questions.md)
before asking. This skill owns `review.higher-risk-fixes`,
`review.scope-expansion`, and `review.round-extension`. Ask only for a genuine
higher-risk change, work outside the established task/PR scope, or another
review attempt beyond the configured round limit. Evidence gathering, ordinary
in-scope low-risk fixes, bounded polling, configured provider execution,
replying to addressed feedback, and resolving addressed threads do not require
another question.

## Arguments

Arguments are optional `key=value` tokens. Reject unknown keys, duplicate keys,
bare values, invalid enum values, and shell metacharacters instead of guessing.

- `scope=auto|changes|branch|codebase|pr` (default `auto`)
- `local=auto|all|none|<configured-provider-id>` (default `auto`)
- `remote=auto|cheap|deep|copilot|none` (default `auto`)
- `fix=auto|ask|none` (default `auto`)
- `pr=<positive-number>`
- `attempt=<positive-number>`

`pr=` is valid only with `scope=auto` or `scope=pr`. `scope=codebase` is never
inferred. Do not treat free text as a provider or reviewer identifier.

## Safety and authority

- Start by reading `git status -sb` and preserve unrelated or ambiguous work.
- Use only argv-array coordinator controls. Never interpolate argument text into
  a shell command, use `eval`, or execute commands declared by remote receipts.
- Non-PR scopes remain worktree-only: never stage, commit, push, request remote
  review, or resolve GitHub threads for them.
- PR scope requires a clean tree and exact local/remote head agreement. Commit
  and push only verified review fixes that belong to the current PR.
- A router classified `absent` may complete locally only when routing is
  optional and the local receipt is clean. `required`, explicit remote intents,
  invalid, incompatible, unavailable, failed, or uncertain dispatch states fail
  closed. Never use a direct reviewer fallback.
- Unavailable, failed, cancelled, skipped, malformed, stale, or
  reconciliation-required evidence grants no positive confidence.
- The user grants standing permission to reply to and resolve a review thread
  after its finding is fixed, rebutted with evidence, or confirmed already
  addressed. Never resolve an actionable, ambiguous, or unverified thread.
- Do not merge, archive Trellis work, or run housekeeping from this skill.

## Run the coordinator

Resolve the repository root, then translate validated arguments into separate
argv tokens. Always request JSON:

```bash
bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
  scripts/sd-ai-command-pack-review.py \
  --repo . \
  --scope auto \
  --local auto \
  --remote auto \
  --fix auto \
  --attempt 1 \
  --json
```

Add `--pr-number <number>` only for a validated `pr=` control. Preserve the
same controls and attempt while resuming an unchanged head. The controller's
private state and durable receipt make a resume idempotent; do not delete state
or increment the attempt merely because a receipt is delayed.

Three coordinator-only evidence flags are not public invocation controls. After
replying with a verified rebuttal to a receipt-declared conversation finding or
changes-requested review that has no resolvable thread, rerun the unchanged
attempt with one separate `--remote-disposition '<stable-id>=rebutted'` argv
pair. Never use this for an unfixed finding or as a substitute for resolving an
inline thread. After the user approves `review.round-extension`, add
`--round-extension-authorized` to the approved over-limit attempt; never infer
that authorization from ordinary review arguments.

A local provider finding you have verified false takes the matching
`--local-disposition '<stable-id>=rebutted'` pair. The bar is the same as the
remote one and it is high: rebut only after checking the cited path and line in
the checkout and finding the claim untrue there — a finding that is merely
low-severity, inconvenient, or hard to fix is outstanding, not rebutted. The
finding stays in the receipt as `rebutted` so the judgement remains auditable,
and the pair applies to one attempt at one head; a later head needs its own
deliberate rebuttal. An id matching no finding at that head is an error, not a
silent no-op.

Two provider misreads are common enough to name, and both are rebuttals rather
than fixes: fenced code blocks quoted inside a Markdown document read as if they
were the diff's own source, and a cited defect that is simply not present at the
cited line. Verify against the checkout either way.

## Interpret the typed result

- `ready` with exit 0: report exact scope/head, local provider run or reuse,
  router route or local-only limitation, cost/latency, and remaining
  limitations. Do not call it fully reviewed if limitations say otherwise.
- `findings` or `blocked` with exit 1: verify every finding against the checkout,
  task, specs, and tests. Deduplicate provider findings before choosing fixes.
- `invalid` with exit 2: correct only the invocation or repository-owned
  configuration error identified by the diagnostic; do not bypass validation.
- `pending`, `failed`, or `indeterminate` with exit 3: follow the exact next
  action. A pending durable receipt is resumable. An uncertain dispatch must be
  reconciled from the same request fingerprint and must never be dispatched
  again through a fallback.

Relay the coordinator's `check`, `local`, `routerCapability`, `remote`,
`diagnostic`, and `limitations` fields. Provider labels are evidence, not
authority.

## Finding disposition and re-entry

For `fix=none`, report verified findings without editing. For `fix=ask`, use the
owned structured decision before any fix. For `fix=auto`, apply ordinary
in-scope low-risk fixes without another question, but ask for higher-risk or
scope-expanding work.

After a fix:

1. run the narrow validation appropriate to the change;
2. rerun the coordinator so its typed `sd-check` gate passes;
3. for PR scope, create one focused review-fix commit and push it;
4. rerun `sd-review` against the new exact head using the next attempt; and
5. reply to and resolve only the threads proven addressed on that head.

If the same finding family recurs after its sibling audit and batched fix, stop
before another paid provider call and use `review.round-extension`. Do not
silently spend another round.

## Final report

Report the normalized outcome, exact target/head, deterministic-check result,
local providers and run/reuse state, routed backend and reason, cost/latency,
finding disposition counts, CI/thread state, limitations, and whether the PR is
ready for its caller's next lifecycle stage. Keep review readiness separate from
merge, finish-work, and housekeeping readiness.

---
name: sd-work-designs
description: "Use when the user wants the autonomous SD work loop to prioritize Trellis tasks that still need design.md or implement.md, optionally stopping after planning."
---

# SD Work Designs

Use this project-local Software Delivery skill for `sd-work-designs` and
`/sd:work-designs` style work. This is a thin selector entry point for the
canonical `sd-work-backlog` controller. It does not maintain a separate loop.

By default, it selects tasks whose real PRDs still need `design.md` or
`implement.md`, completes those artifacts, and carries each selected task
through implementation, validation, `sd-ship until=merge`, follow-ups, cleanup,
and re-inventory. Use `until=design` for the prior planning-only behavior.

## Arguments

Pass the user's invocation text unchanged to `sd-work-backlog`. It owns all
normalization and fail-closed validation.

- Bare text is one implicit preferred focus: `sd-work-designs CI pipeline`.
- Repeatable `focus=` and `focus-only=` retain operator order.
- `until=merge` is the default; `until=design` stops after planning artifacts
  are implementation-ready.
- Bare/explicit focus mixtures, mixed focus modes, empty expressions, unknown
  selectors, and unknown option-shaped input fail before state or repo
  mutation.

Focus composes with, and never replaces, this skill's `needs-design` selector.
`focus-only=` therefore considers only matching tasks that also need planning.

## Workflow

1. Resolve `sd-work-backlog` by name through the trusted installed-skill
   resolver. Stop if it is missing, ambiguous, unreadable, or contradictory.
2. Invoke it with this trusted internal selector context:

   ```text
   caller: sd-work-designs
   mode: designs
   selector: needs-design
   invocation: <the user's arguments unchanged>
   ```

3. The backlog controller starts or resumes
   `scripts/sd-ai-command-pack-work-loop.py` with `--mode designs --selector
   needs-design`, prints the run authority boundary, and owns every lifecycle
   phase and stop decision.
4. For each selected task, it follows the shared planning reference in
   `sd-work-backlog`, preserving useful user-authored artifacts and grounding
   proposals in the smallest relevant repo context.
5. With `until=merge`, planning returns to the same iteration for
   implementation and shipping. With `until=design`, the controller validates
   the artifacts, records `until_design_reached`, and stops without starting
   code changes or a PR.
6. Nested reports always return to the canonical controller. This entry point
   never invokes `sd-create-pr`, `sd-review-pr`, `sd-housekeeping`, or a second
   backlog loop itself.

## Safety Rules

- Work exactly one task, branch, and PR at a time.
- Never bypass the state lock, Trellis planning, `trellis-before-dev`,
  deterministic checks, review convergence, or housekeeping merge gate.
- Do not overwrite useful task artifacts. Fill placeholders or append a clearly
  scoped proposal when existing content must be preserved.
- Do not create an upstream Trellis PR without explicit approval for that PR.
- User-input, parking, context-health, operator controls, near-ten checkpoint,
  follow-up handling, and final-stop guards are inherited unchanged from
  `sd-work-backlog`.

## Final Report

Use the canonical backlog report shape and label the mode `designs` with
selector `needs-design`. Also include a numbered list linking every created or
updated `design.md` and `implement.md` with a one-line summary. Empty
completed, parked, skipped, blocked, decision, and follow-up categories must be
stated explicitly.

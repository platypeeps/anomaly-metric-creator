---
name: sd-retro
description: Use when the user wants to capture a structured retrospective after a debugging stream, incident, or gate near-miss, record it as a journal entry via the session recorder, and propose consent-gated prevention tasks.
---

# SD Retro

Run this project-local skill for `sd-retro` and `/sd:retro` style work. It
captures a structured retrospective of a just-finished debugging stream,
incident, or gate near-miss: gather the evidence, compose the retro in a
fixed four-field shape, record it as a journal entry through the session
recorder, and turn what was learned into consent-gated prevention
proposals.

This command analyzes and records; it makes no code changes. The only
writes are the journal entry (via the recorder) and, after explicit user
consent, Trellis task artifacts. It never auto-creates tasks.

## Structured decisions

Read [`../sd-help/references/structured-questions.md`](../sd-help/references/structured-questions.md)
before asking. This skill owns only `retro.followups`; use its multi-select
shape for independent prevention tasks after recording the requested retro.
Running or recording the retrospective itself never needs confirmation.

## When to use

Run this command right after a debugging stream, incident, or gate
near-miss, while the evidence is still in context — ideally in the same
session that did the debugging. Root causes and gate-miss analysis
evaporate quickly; the retro turns them into a durable journal entry and
concrete prevention candidates.

It complements `sd-review-learnings` (repeated review-feedback patterns
across streams): the retro dissects one event end to end and hands any
repeated pattern it surfaces toward the learnings flow. It does not
replace the normal finish-work journal entry for a feature stream.

## Arguments

Arguments arrive as free text with the invocation. Parse recognized
`key=value` arguments before treating remaining bare non-option text as the
positional primary subject. Unknown option-shaped arguments are an error, not
a silent skip: stop and report them before gathering evidence. This command
reads no environment variables; arguments are the only tuning surface.

- `topic=<text>` — the retro topic, used verbatim in the journal title
  `Retro: <topic>`. When omitted, derive a short topic from the debugging
  stream under retrospective and state the derived topic in the report.
- Bare non-option text is one topic phrase. `sd-retro deployment timeout` is
  equivalent to `topic="deployment timeout"`; preserve the complete phrase
  rather than splitting it into several topics.

Reject positional topic text combined with `topic=` before gathering evidence.
Before writing the journal, state the normalized topic and whether it was
positional, explicit, or derived from the current stream.

## Workflow

1. **Gather evidence** — read, in order:
   - the current session context: what failed, what was tried, and what
     fixed it;
   - recent journal entries under `.trellis/workspace/` for prior context
     on the same area;
   - recent git history (`git log --oneline`, the fix commits, and the
     associated PR when one exists).
2. **Compose the retro** in this fixed four-field shape, one field per
   bullet, each grounded in the gathered evidence:
   - What broke — the observable failure and its impact.
   - Root cause — the deepest actionable cause, not the proximate
     symptom.
   - Why existing gates/tests missed it — name the gate, test, or review
     step that should have caught it, and why it did not.
   - What limited the blast radius — what actually contained the damage,
     or, when nothing did, what should have limited it.
3. **Record the journal entry** via the session recorder with the title
   `Retro: <topic>`, mapping the retro shape onto the recorder flags:

   ```bash
   bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
     scripts/sd-ai-command-pack-record-session.py \
     --title "Retro: <topic>" \
     --summary "<one line: what broke + root cause>" \
     --change "What broke: <field>" \
     --change "Root cause: <field>" \
     --change "Why gates/tests missed it: <field>" \
     --change "Blast radius: <field>" \
     --test "[OK] evidence: <sources reviewed>" \
     --next-step "<prevention candidate awaiting consent>"
   ```

   The recorder requires at least one `--test` line; use it to record the
   evidence trail. Pass one `--next-step` per prevention candidate.
4. **Derive prevention candidates** — gate, test, or doc changes that
   would have caught the failure earlier or contained it better. Present
   each as a Trellis task proposal (title, slug, one-line summary, and an
   acceptance sketch) awaiting explicit user consent — never auto-create
   a task. Create task artifacts only after the user consents, through
   the normal Trellis task flow.
5. **Note repeated patterns** — when the retro surfaces a review-feedback
   pattern seen across streams, note it toward `sd-review-learnings` so
   the learnings file can absorb it; otherwise state explicitly that no
   repeated pattern emerged.

## Safety rules

- Make no code changes: no product code, no tests, no configuration
  edits. This command records and proposes; it does not fix.
- The only writes are the journal entry (via the recorder) and, after
  explicit user consent, Trellis task artifacts.
- Prevention proposals without consent are report-only; task creation
  requires explicit user consent, and the command never auto-creates
  tasks.
- Append the retro through the recorder only; never rewrite or delete
  existing journal entries.
- The recorder's own journal commit is the only commit this command
  causes. Do not stage, commit, or push anything else.
- Unknown argument names stop the run before evidence gathering.

## Final report

The assistant's final response is mandatory-shaped: every item below
appears in every run, and an empty item states its emptiness explicitly —
write `none` rather than dropping the line. Keep it scannable: bullets
and short lines, one point per line, no paragraph blobs.

- Retro summary — the four fixed fields, one bullet each:
  - What broke: <one line>
  - Root cause: <one line>
  - Why gates/tests missed it: <one line>
  - Blast radius: <one line>
- Journal entry reference: the `Retro: <topic>` title plus the journal
  file path under `.trellis/workspace/`, and the recorder's commit when
  it made one.
- Prevention proposals awaiting consent: one bullet per proposal with its
  suggested task title, or `none`.
- Related-learnings note: the repeated pattern flagged toward
  `sd-review-learnings`, or an explicit `none`.

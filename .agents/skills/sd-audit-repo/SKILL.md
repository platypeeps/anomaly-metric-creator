---
name: sd-audit-repo
description: Use when the user asks to run a formal multi-dimension audit of the repository, follow up on open audit findings, or produce the canonical audit report and committed findings ledger.
---

# SD Audit Repo

Run this project-local skill for `sd-audit-repo` and `/sd:audit-repo` style
work. It runs a standardized, multi-dimension, sub-agent-driven audit of the
current repository and produces a canonical report plus an update to the
committed findings ledger.

This command audits and reports; it does not fix. It never edits product
code, never creates branches or pull requests, and never creates Trellis
tasks on its own.

## When to use

Run this command for the periodic formal audit: before a release, after a
large development stream, on a recurring schedule, or when taking over an
unfamiliar repository. Run it with `follow-up` to re-verify previously
recorded findings instead of sweeping the whole repository.

It complements `sd-review-local` (provider loop), `sd-review-pr` (PR
loop), and `sd-full-check` (gate). It replaces none of them and is not a
per-change review loop.

The audit is charter-driven. Charters live at
`.agents/skills/sd-audit-repo/charters/<name>.md`; that canonical path is
shared by every platform copy of this skill. Verify the charters directory
exists before starting; if it is missing, stop and report that the pack
should be reinstalled. The charter roster:

- Always-on (12): architecture, design, correctness, security, testing,
  documentation, bloat, performance, dependencies, tooling, release-hygiene,
  improvements.
- Conditional (3, fingerprint-selected): consumer-impact, observability,
  accessibility-i18n.

## Arguments

Arguments arrive as free text with the invocation. Parse recognized options
and the reserved `follow-up` flag before treating remaining bare values as the
positional primary subject. Recognize these forms:

- `dimensions=<a,b,c>` — run only the named charters. Names are the charter
  file stems from the roster above. Unknown names are an error, not a
  silent skip: stop and report them before dispatching any reviewer.
- `depth=quick|standard|deep` — default `standard`.
  - `quick`: no verification stage; each reviewer is capped to its top
    findings.
  - `standard`: adversarial verification of P0/P1 findings, single refuter.
  - `deep`: verification of P0–P2; P0 uses 2-of-3 refuter votes; the
    correctness and security charters loop until a pass adds nothing new.
- `follow-up` — skip the full sweep. Re-verify each open ledger item
  against the current tree (fixed / still-open / regressed), then run a
  quick regression sweep of the areas touched since the items' `last-seen`
  commits.
- Remaining bare values are exact charter names. `sd-audit-repo security
  testing` is equivalent to `dimensions=security,testing`. Split names on
  whitespace or commas, preserve their order, and de-duplicate exact repeats.

Reject bare dimensions combined with `dimensions=` before fingerprinting.
Reject `follow-up` combined with either dimension form because follow-up is a
different audit mode. Validate every normalized dimension against the charter
roster before reviewer dispatch; an unknown name or option-shaped token is an
error and must never broaden the run to a full audit. Before fingerprinting,
report the normalized mode, depth, and dimension set.

## Pipeline

The pipeline is fixed, mandatory, and ordered:
fingerprint → dimension reviews → adversarial verification → synthesis → Trellis reconciliation → report + ledger.
Depth and `follow-up` rules are the only sanctioned reductions, and every
reduction is recorded in Coverage & limits.

1. **Fingerprint** — one inventory pass: languages, size, entry points,
   build and CI setup, test layout, docs map, and downstream-consumer
   signals (manifest, published artifacts, dependent-repo references).
   Select the applicable conditional charters and record the selection
   rationale. Load `.trellis/audit/ledger.md` if present. Record the repo
   name, `git rev-parse --short HEAD`, and the date for the report header.
   Output: a scope brief given verbatim to every reviewer (repo map +
   open-ledger summary), so reviewers neither re-derive the map nor
   re-report known-open items as new.
2. **Dimension reviews** — one read-only sub-agent per applicable charter,
   in parallel where the platform supports it. Input: the charter plus the
   scope brief. Output: structured findings per the charter output schema.
3. **Adversarial verification** — per the depth rules, independent refuter
   agents each receive one finding with the instruction to disprove it.
   Refuted findings are dropped and logged in Coverage & limits. Findings
   that survive get confidence Verified; severities outside the depth's
   verification scope keep confidence Plausible.
4. **Synthesis** — dedupe cross-dimension overlaps (same file/line/root
   cause), merge related findings, and rank by severity then effort.
5. **Trellis reconciliation** — read `python3 ./.trellis/scripts/task.py
   list` plus task `prd.md` files; classify every finding as
   tracked-accurate, tracked-stale, or untracked. Draft prd-ready task
   proposals (title, slug, summary, acceptance sketch) for untracked P0–P2
   findings. Proposals require explicit user consent before any task is
   created.
6. **Report + ledger** — emit the canonical report, then update the ledger:
   assign IDs to new findings, update `last-seen` on still-open items, mark
   fixed items resolved, and flag regressions.

## Scoring rubric

Score every finding with these fixed definitions so results stay comparable
across repos and over time:

- Severity: P0 broken/exploitable now · P1 will bite soon or blocks a core guarantee · P2 meaningful debt/risk · P3 polish.
- Effort: S ≤ ~1h · M ≤ ~1 day · L multi-day.
- Confidence: Verified (survived refutation) · Plausible (unrefuted but unverified).

Reviewers return every finding in this schema:

```
[<dimension>] <title>
severity: P0-P3 · effort: S/M/L
evidence: <file:line> (+ short excerpt or command output)
why it matters: <1-2 sentences>
fix sketch: <1-3 sentences>
```

Finding IDs (`A-NNN`) are assigned by the orchestrator at ledger-write time,
never by reviewers; this prevents ID collisions across parallel agents.

## Dispatch protocol

- Dispatch one read-only sub-agent per applicable charter. On sub-agent
  dispatch platforms, run the reviewers in parallel. On inline platforms,
  work through the charters sequentially in one context, and prefer
  `depth=quick` or a `dimensions=` filter to keep the run practical.
- Every dispatch prompt starts with the Active task prefix when a Trellis
  task is active: `Active task: <task path from task.py current>` before
  the role-specific instructions.
- Each reviewer receives its charter file plus the fingerprint scope brief,
  nothing else. Reviewers do not read other charters and do not re-derive
  the repository inventory.
- Refuter agents receive one finding each with the instruction to disprove
  it; they are read-only as well.
- Reviewers and refuters never modify files. Only the orchestrator writes:
  the report it presents and the ledger file.

## Report format

The report has six mandatory sections: Verdict, Findings,
Trellis reconciliation, Prioritized actions, Ledger delta, and
Coverage & limits. No section is ever omitted, and empty sections state
their emptiness explicitly — write `none` or an equivalent explicit
statement rather than dropping the heading. Coverage & limits must state
what was not reviewed and why — no silent caps.

```
# Repo Audit — <repo> @ <short-sha> — <date>
Mode: full|follow-up · Depth: … · Dimensions: …

## Verdict            <one-paragraph assessment + counts by severity>
## Findings           <grouped by dimension; each finding in schema above + ID>
## Trellis reconciliation  <tracked-accurate / tracked-stale / untracked+proposals>
## Prioritized actions <numbered, severity-then-effort order>
## Ledger delta       <new N · still-open N · fixed N · regressed N>
## Coverage & limits  <dimensions skipped + why, verification caps, refuted-finding log>
```

Readability rules — the report and the final response must stay scannable:

- One point per line; prefer bullets and short lines over paragraphs. Never
  render findings, reconciliation items, or coverage notes as wrapped
  paragraph blobs.
- Render each report finding in this shape:

```
#### [A-NNN] <severity> · <effort> · <confidence> — <title>
- evidence:
  - <file:line> — <one-line note>
  - <file:line> — <one-line note>
- why: <at most two lines>
- fix: <at most two lines>
```

- Split multi-clause evidence into one `file:line — note` bullet per clause
  instead of joining clauses with semicolons.

## Ledger rules

The ledger is the committed, cross-session memory of audit findings. It
lives at `.trellis/audit/ledger.md` and uses this format:

```
# Audit ledger
<one preamble line: purpose + "managed by sd-audit-repo">

## A-013 — <title>
- status: open|fixed|regressed
- severity: P1 · effort: M · confidence: Verified
- dimension: security
- first-seen: 2026-07-15 @ abc1234
- last-seen: 2026-07-20 @ def5678
- evidence:
  - path/to/file.py:88 — <one-line note>
- why: <at most two lines>
- fix: <at most two lines>
- notes: <optional, human-editable>
```

Ledger entries follow the same readability rules as the report: bulleted
metadata one item per line, one `file:line — note` bullet per evidence
clause, and `why:`/`fix:` capped at two lines — never paragraph blobs.

- IDs are assigned monotonically and are never reused. Only the
  orchestrator assigns IDs.
- Statuses are `open`, `fixed`, and `regressed`.
- `fixed` entries are kept as history, never deleted.
- A `fixed` finding that reappears becomes `regressed` under the same ID.
- Humans may edit `notes:` lines freely. Preserve unknown lines within an
  entry when updating the ledger.
- Leave committing the ledger update to the user's normal commit flow, and
  report the ledger path as a changed file.

## Safety rules

- Reviewers and refuters are read-only. Ledger and report writes are the
  only file mutations this skill performs; everything else in the run is
  read-only.
- Never auto-create Trellis tasks. Present task proposals for untracked
  P0–P2 findings and create nothing without explicit user consent.
- Findings without `file:line` evidence are downgraded or dropped. Do not
  let unevidenced claims into the report or the ledger.
- Unknown `dimensions=` names stop the run before any reviewer dispatch.
- Do not stage, commit, push, or open pull requests.
- Positioning: this command complements `sd-review-local` (provider
  loop), `sd-review-pr` (PR loop), and `sd-full-check` (gate). It is the
  periodic formal audit, not a replacement for any of them.

## Final report

The assistant's final response is mandatory-shaped: every item below
appears in every run, and an empty item states its emptiness explicitly.
Format it per the readability rules — bullets and short lines, one point
per line, no paragraph blobs.

- Verdict: the one-paragraph assessment plus finding counts by severity.
- Top prioritized actions, in severity-then-effort order.
- Trellis reconciliation summary, including any task proposals awaiting
  user consent.
- Ledger delta: new N · still-open N · fixed N · regressed N.
- Coverage gaps: dimensions skipped and why, verification caps, and the
  refuted-finding log.

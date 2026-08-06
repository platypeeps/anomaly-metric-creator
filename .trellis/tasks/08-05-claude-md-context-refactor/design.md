# Design — CLAUDE.md context-cost refactor

## Boundaries

Three document tiers exist today and the refactor makes the split explicit:

| Tier | Files | Load cost | Role after this change |
|---|---|---|---|
| Always-loaded memory | `CLAUDE.md` | every session + every compaction | Routing table + invariants that apply to *any* edit |
| Task-loadable spec | `.trellis/spec/amc/backend/*.md` (13 files, 2,196 lines) | on demand, per surface | Canonical durable rules |
| Reference | `README.md`, `docs/`, `CHANGELOG.md`, script docstrings | on demand | User-facing docs, history, per-tool detail |

`CLAUDE.md` already declares tier 2 canonical in its first paragraph. The
refactor makes the file behave the way it describes itself.

## Disposition analysis

Measured before planning. Each row's evidence is a grep count against the
named destination.

| Cluster | Lines | Disposition | Destination | Evidence |
|---|---|---|---|---|
| Six lint sections (`role-name`, `approval-duplicate`, `branch-name`, `ruff-lockstep`, `workflow-pip`, `pr_comment.sh`) | ~380 | COVERED | Script module docstrings | `check_branch_name` 71 docstring lines, `check_approval_duplicate` 82, `check_role_name_leaks` 53, `check_ruff_lockstep` 44, `check_workflow_pip` 38 |
| Server-mode module DAG / extraction ledger | ~380 of 513 | COVERED | `.trellis/spec/amc/backend/architecture.md` | every leaf module named: `server_ops_support` 5, `server_ops_parse` 5, `server_helm_impl` 4, `server_k8s_objects` 4, `server_command_render` 3, `server_k8s_tables` 2, `server_k8s_api_trace` 1 |
| Server-mode behavior contracts (eval-mode wall, watch streams, mutation ordering) | ~133 of 513 | COVERED | `operations-security-logging.md`, `api-cli-server.md` | index.md routes these surfaces there explicitly |
| CI / Dependabot — heavy marker, `loadfile`, `full-ci`, Socket, mypy gate | ~110 of 159 | COVERED | `testing-quality.md` | `heavy` 31, `loadfile` 13, `full-ci` 8, Socket 4, `check_mypy_gate` 2 |
| CI / Dependabot — coverage threshold, `!cancelled()` guard, runner history | ~49 of 159 | **MOVE** | `testing-quality.md` | `fail-under` 0, `COVERAGE_CORE` 0, `cancelled()` 0, `ubuntu-latest-m` 0 — **zero coverage anywhere** |
| Pre-PR checklist antipattern bullets | 124 | **MOVE** | `testing-quality.md` § Review Checklist | that section names the 15 headings but carries none of the bullets (`table.get(key)`, vacuous-test shapes, resource-cost rules) |
| Known Copilot false-positives | 29 | **MOVE** | `testing-quality.md` | 3-line summary only exists there |
| Phase-history narrative (`Phase N landed…`, `phase-9 flag day`, per-phase tuning tables) | ~600 spread across topology / multi-instance / validator sections | RETIRE | — | `CHANGELOG.md` hits are thin (`phase 9` 1, `flag day` 4, `topology-mode` 3) and the content is "how we got here", not a forward rule. Current behavior is stated in the same sections and stays. |

Subtotal: ~1,003 COVERED (cut to pointers), ~202 MOVE (relocate first), ~600
RETIRE (drop), ~250 stays = ~2,055 of 3,106 lines.

### Two rows of that table were disproved on review

Adversarial review checked the COVERED claims by contract rather than by
keyword and found two false:

- **Server DAG.** `architecture.md:211` records `server_k8s_api_trace`
  importing "one-way from `server_k8s_api`" and omits the
  `server_ops_support._preview` edge that `CLAUDE.md:374` records. Module names
  all appear, so a name-grep passes while the DAG loses an edge → **MOVE**.
- **`full-ci` label.** The load-bearing content is the asymmetry — application
  and Socket honor the label one-shot, CodeQL re-checks it persistently, and
  unifying them cuts security coverage. `testing-quality.md:551` carries only
  the CodeQL half → the one-shot half is **MOVE**.

The general lesson, now binding on the whole sweep: **coverage is a contract
being stated in the destination, not a token appearing there.**

### The table above is a starting sample, not the full classification (C-1)

The rows above came from the context audit that motivated this task, and they
account for only two thirds of the file. About 1,050 lines are unclassified,
including these measured sections: `Topology graph` 190, `Per-instance topology`
161, `Multi-instance fan-out` 144, `Scenario registry` 140, `LLM token-throttle`
108, `Output validator` 103, `Output directory hygiene` 96, `Gauge metric file`
84, `schema.json` 81, `Saturation feedback` 80, `Combine step` 77, `Parallel
execution` 74, `CLI surface` 66, `Adding new components` 60.

Therefore step 3 of the plan is a **sweep over every section in the file**, not
a pass over the four named clusters. Each section gets exactly one disposition
with its evidence, and the resulting complete table replaces the sample above.
Reaching ≤400 lines depends on that sweep: the sample alone leaves 1,301 lines.

Most of the unclassified sections describe surfaces the Trellis spec index
already routes to (`architecture.md` for generation / registry / topology,
`api-cli-server.md` for CLI / schema / validation, `scenarios-and-data.md` for
scenarios), so COVERED is the expected disposition for the bulk of them — but
that must be verified per section, not assumed. Where the sweep finds content
that is genuinely uncovered *and* universally applicable, it stays, and the
line target is raised with that justification recorded rather than cutting a
rule to hit a number.

### RETIRE hazard: historical framing around present-tense rules (C-2)

The RETIRE class carries the real risk and is the reason this is a task rather
than an inline edit: dropping narrative is irreversible in the sense that no
grep will catch its absence. Worse, several passages state *current* contract in
phase-history voice, so a paragraph-level delete silently drops a live rule:

- `CLAUDE.md:971` — writer emits only `"realistic"`, but "the reader still
  honors `"independent"` so documents produced under the historic mode keep
  validating". The reader behavior is current contract.
- `CLAUDE.md:1342` — the flag is gone, but "the `generate_component` kwarg
  survives for programmatic callers". Current API surface.
- `CLAUDE.md:1682`, `CLAUDE.md:1752` — same shape for topology mode and
  generation order.

RETIRE is therefore **sentence-level, not paragraph-level**, with an explicit
keep-rule: any clause asserting present-tense behavior is retained even when its
surrounding framing is historical. Mitigation and validation in Rollout below.

## Contracts preserved

1. **Extraction / re-import invariant.** The rule that a moved name is
   re-imported by `legacy.py` / `server_ops.py` at the same conceptual
   position, and that leaves never import their facade, stays in `CLAUDE.md`.
   It applies to any edit in the package and is the single most load-bearing
   invariant in the file.
2. **RNG determinism.** The `RunContext.rng` threading, the stable-sort
   override ordering, and the collision caveat stay.
3. **Module-ownership map.** A compact table (module → surface owned)
   replaces the current prose ledger.
4. **Pre-PR checklist lockstep (R4).** After the MOVE, `testing-quality.md` is
   the sole canonical body. `CLAUDE.md`, `.github/PULL_REQUEST_TEMPLATE.md`,
   and `.github/instructions/anomaly-metric-creator.instructions.md` keep the
   15 heading names and point at it. `tools/check_ci_review_contract.py` is
   inspected but not modified unless it asserts on `CLAUDE.md` content.

## Tradeoffs

- **Pointer indirection vs. inline detail.** After the cut, answering a
  server-mode question needs one extra read of `architecture.md`. Accepted:
  that read is on-demand and scoped, versus 77.9k tokens paid unconditionally.
- **RETIRE vs. archive-to-docs.** Moving 600 lines of phase narrative into a
  new `docs/history.md` would preserve everything at zero context cost. It
  also creates a file nobody reads and that drifts. Chosen: RETIRE, but the
  pre-refactor `CLAUDE.md` stays in git history and the branch records the
  exact deletion, so recovery is `git show <base>:CLAUDE.md`.
- **One PR vs. split.** Kept as one PR with ordered commits so the reviewer
  sees the disposition table alongside the cuts. Split only if the diff
  becomes unreviewable.

## Compatibility

- No runtime code changes, so all locked SHA-256 golden hashes, the full test
  suite, and CI behavior are untouched by construction. `git diff --stat`
  showing no `src/` or `tests/` path is the check.
- Other agent entrypoints (`AGENTS.md`, `.github/instructions/`,
  `.agents/skills/`) read the Trellis specs already; making `CLAUDE.md` a
  pointer aligns it with them rather than diverging.

## Rollout / rollback

Rollout is ordered so that no cut precedes its destination existing:

1. MOVE first (checklist bullets, Copilot false-positives, CI coverage /
   `!cancelled()` / runner history) into `testing-quality.md`.
2. Verify each moved block by grep in the destination.
3. Then COVERED cuts, one cluster per commit, each with its post-cut grep
   recorded.
4. RETIRE last, as its own commit, so it is the easiest to revert in isolation.
5. Rewrite the surviving `CLAUDE.md` head into the routing table.

Rollback points: every step is one commit. Full rollback is
`git checkout <base> -- CLAUDE.md`. Because RETIRE is the final commit,
reverting only the narrative drop does not undo the structural work.

## Resolved questions

Both planning-time open questions were answered during adversarial review:

- **Does `tools/check_ci_review_contract.py` assert against `CLAUDE.md` prose?**
  No. `grep -n "CLAUDE" tools/check_ci_review_contract.py` returns no matches.
  No anchor repointing needed; the script stays in step 6 as a regression guard.
- **Does `.github/instructions/anomaly-metric-creator.instructions.md` carry the
  checklist body or only the headings?** **Body, not headings only** — the
  round-1 answer was wrong. The section heading at `:217` says "headings", but
  `:219` onward is a numbered body of compressed normative guidance
  ("non-canonical inputs enumerated; every discriminator branch validated;
  dispatch tables strict"), and item 3 at `:229` instructs reviewers to grep
  changed symbol names *against `CLAUDE.md`*. `check_copilot_instruction_contract.py:307`
  compares only extracted heading names and loose fragments, so passing it
  proves nothing about the body. This file is both a MOVE consideration and a
  required repoint.

## Automated contracts this change must satisfy

`tools/check_copilot_instruction_contract.py` (pre-commit hook
`copilot-instruction-contract`, `.pre-commit-config.yaml:160`) reads
`.trellis/spec/amc/backend/testing-quality.md` — the destination of every MOVE —
and asserts at lines 336–344 that each `TESTING_SPEC_HEADING_FRAGMENTS` entry
appears there. The MOVE must preserve those heading fragments. Its
`COPILOT_FORBIDDEN_NEEDLES` scan applies only to the Copilot instructions file
(`_check_copilot_text`), not to `testing-quality.md`, so relocated prose
mentioning removed flags such as `--topology-mode` does not trip it.

Five reference classes cite `CLAUDE.md` and must be repointed rather than left
stale. A grep for `CLAUDE.md "` finds only the first — the others use different
quoting or Markdown anchors:

- `.pre-commit-config.yaml:1` (`CLAUDE.md "Pre-PR checklist > …"`) and `:125`
  (`CLAUDE.md "Workflow pip lint"`).
- `tests/test_topology_llm.py:21`, `tests/test_topology_saturation.py:393`,
  `tests/test_validate_output.py:866` — each asserts a specific statement lives
  in `CLAUDE.md`. Either keep the cited statement or update the citation.
- **Lint runtime messages** (`tools/check_branch_name.py:188`,
  `check_approval_duplicate.py:623`, `check_workflow_pip.py:217`) — printed to
  operators on failure, e.g. "Policy lives in CLAUDE.md under 'Branch-name
  lint'". These also make the "COVERED by script docstring" claim circular: the
  script defers authority back to the section being cut.
- **Adapter role claims** — `.github/PULL_REQUEST_TEMPLATE.md:20` and
  `AGENTS.md:8` / `:57` describe `CLAUDE.md` as the expanded source of detail
  and checklist material. The template also miscounts the headings as 14 when
  there are 15.
- **Markdown anchors** — `docs/topology.md:74` links `../CLAUDE.md#topology-graph`.

`tests/test_role_name_leaks_lint.py:339` includes `CLAUDE.md` in a live scan but
has no content expectation; shrinking the file is safe.

Finally, `.trellis/spec/amc/backend/index.md:96` § Quality Check prescribes the
minimum gate for docs-only Trellis/spec consolidation — `get_context.py`, a
placeholder scan, and a Markdown-link check. This task is exactly that shape, so
those run in step 6.

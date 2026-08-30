# Development cycle

AMC uses a local-first review cadence to keep GitHub Actions and remote AI
review focused on changes that actually need them.

## While Editing

Run the smallest deterministic check for the surface you touched. Common
examples:

```bash
.venv/bin/pytest tests/test_server.py -q -k "apply or rollout"
.venv/bin/pytest tests/test_ci_change_classifier.py -q
.venv/bin/ruff check tests/
git diff --check
```

Use focused tests first, then broaden only when the changed boundary warrants
it. Runtime, parser, server, workflow, dependency, and review-tooling changes
should all name the local check that exercised the changed contract.

## Before Pushing For Review

Run the repository's own deterministic gate rather than remembering the guard
list:

```bash
.venv/bin/pre-commit run --all-files
node scripts/check-review-preflight.mjs
```

`pre-commit run --all-files` covers ruff, the shell/Python syntax checks, and
every mechanical guard under `tools/`. The review preflight then runs the three
checks that are deliberately not per-file hooks: the CI/review cadence contract
guard, the Copilot instruction contract guard, and the canonical clean-module
mypy gate. Review-churn mutation tests stay in GitHub CI rather than being
repeated locally.

Until 2026-08-30 this section named a full-check script shipped by an installed
command pack, which wrapped the two commands above plus pack-owned preflights.
That pack is no longer part of this repository, and nothing here depends on a
machine-side install any more: the gate above runs from a fresh clone.

Install the repository's non-default Git hook stages once per clone:

```bash
.venv/bin/pre-commit install --hook-type pre-push
.venv/bin/pre-commit install --hook-type commit-msg
```

The pre-push hook checks the current branch name. The commit-msg hook passes the
message file to `tools/check_role_name_leaks.py` before Git records it.

A broad behavior-changing PR body should carry the scope section matching the
changed paths. The five canonical headings are `Automation scope:`,
`CI/review scope:`, `Tooling/generated scope:`, `Docs/user-facing scope:`, and
`Runtime/server scope:`; `.github/PULL_REQUEST_TEMPLATE.md` prompts for them.

Nothing checks a body for them. The guard that did was pack-owned and left with
the pack, so this is author discipline confirmed in review — see the note under
*Local guards* below.

Matching is more permissive than those five literals, so a body that reads
naturally still passes. `_body_has_heading` anchors each heading to the start of
a line, but tolerates Markdown heading, list, and blockquote prefixes, matches
case-insensitively, and treats the trailing colon as optional — so
`### Docs/user-facing scope:` and `> Docs scope:` both satisfy the docs rule.
Each rule also carries documented aliases (`Docs scope:`,
`Generated/tooling scope:`, `Workflow scope:`, and others); the merged config
plus script defaults are the authority for the full set, not this list.

What the guard will not accept is an invented heading: `Explicit doc scope`
matches no rule and leaves the section unsatisfied even though it reads like
compliance. Without a body, the command reports detected categories and exits
successfully so it remains useful before a PR exists.

Before marking a PR ready, requesting a final remote review, or applying the
`full-ci` label, run the local gate with Prism enabled when practical:

```bash
.venv/bin/pre-commit run --all-files && node scripts/check-review-preflight.mjs
```

For a full local suite, use the normal four-worker default:

```bash
.venv/bin/pytest
```

The heavy/light split is a CI memory-isolation strategy, not the fastest local
path. On the current development host, the bare suite completed in 253.36s,
while the serial heavy partition alone took 345.01s. Use the sequential split
only when memory pressure makes fixture fan-out unsafe; lower the light worker
count on smaller machines:

```bash
.venv/bin/pytest -n 0 --dist loadfile -m heavy
.venv/bin/pytest -n 2 --dist loadfile -m "not heavy"
```

## GitHub CI Cadence

The workflow uses `scripts/classify-ci-changes.sh` to select one application
lane, runs Socket as a sibling job, and combines both in the stable aggregate
check named `CI Result`.

Before lane selection, the `changes` job checks the actual pull-request
`github.head_ref` and runs the AMC-module-load, role-name, and agent-hook
exception guards under uv-managed Python 3.14. The role-name scan covers
`src/`, `scripts/`, and `.agents/`, so these checks apply to every application
lane rather than depending on local hooks.

| Lane | Runs When | Purpose |
| --- | --- | --- |
| `lightweight readiness` | Docs, specs under `docs/spec/`, work items under `docs/work/`, agent prompts/skills, Prism rules, Copilot review surfaces, review-tooling scripts, or explicitly enumerated repo-only automation with no skipped behavioral tests | Catch whitespace, shell syntax, Python syntax, workflow pip, and work-item artifact hygiene issues under uv-managed Python 3.14 without installing the full dev environment. |
| `quick test` | App paths changed on routine PR updates where full CI was not requested | Run install smoke, ruff, review-churn lint tests, and focused server compatibility tests. |
| `test heavy (py3.14)` + `test light (py3.14)` + `coverage (py3.14)` | App-required diffs when a PR is opened/reopened/ready, the `full-ci` label is applied, auto-merge is armed (the `auto_merge_enabled` event and every later push or label event on the armed PR), workflow/dependency files change, manual dispatch runs, or code lands on `main` | Run the heavy and non-heavy pytest partitions concurrently, then combine their raw data and enforce the 85% coverage gate. The light job also owns the console-script, ruff, and mypy gates. |

### Pinned CI tool bumps

The full light lane installs kubectl v1.36.2 and Helm v4.2.0 from their
official release endpoints, verifies both Linux-amd64 downloads with the exact
SHA-256 values in `ci.yml`, and runs the two opt-in real-client server smokes
serially. When either client moves, update the version and checksum together,
keep `server_ops.py`'s advertised Kubernetes version within supported kubectl
skew, update README's tested-version sentence, run both real-client smokes,
and update the CI contract guard and its mutation tests. Sources:
`.github/workflows/ci.yml`; `src/anomaly_metric_creator/server_ops.py`;
`tests/test_server.py`; `tools/check_ci_review_contract.py`;
`tests/test_ci_review_contract.py`; `README.md`.

Two exact Python-tool pins have no automated bump path and must be bumped by
hand from this checklist. Dependabot cannot reach either: the `uv` ecosystem
runs `versioning-strategy: lockfile-only`, which leaves `pyproject.toml`'s
manifest untouched (and `mypy` is an exact `==`, not a `>=` floor Dependabot
would move), and `socketsecurity` is installed by a direct workflow
`python -m pip install` step, not a tracked ecosystem at all.

- **`mypy==2.1.0`** — `pyproject.toml` `dev` extra. Pinned exactly so the
  report-only baseline error count stays comparable across runs. To bump: raise
  the pin, run `.venv/bin/pip install -e '.[dev]'`, run the report-only mypy
  step (whole `[tool.mypy]` `files` set) and confirm the baseline count is
  unchanged or improved, then run the gated
  `python tools/check_mypy_gate.py` and confirm the clean-module list is still
  error-free. A new mypy release that reclassifies errors in the gated modules
  blocks the bump until the modules are fixed — never drop a module to pass.
- **`socketsecurity==2.1.0`** — `.github/workflows/ci.yml` (the Socket job's
  `python -m pip install`). To bump: raise the pin and confirm the Socket job
  stays green (or fast-skips) on a PR that touches dependency/security-relevant
  files so the job actually runs. The job no-ops to success until the
  `SOCKET_SECURITY_API_KEY` secret is set, so verify against a run where the
  secret is present.

CodeQL is advisory on PRs and not a required branch-protection context
(`CI Result` is the only required check): it analyzes
opened/reopened/ready_for_review PRs and `full-ci`-labeled updates, skips on
plain synchronize events, and always analyzes merged code on the push-to-main
run. A skipped analysis produces no code-scanning summary check, so `CodeQL`
must not be re-added as a required context while this gating is in place.

The `full-ci` label's lifetime is deliberately asymmetric across the
workflows, and the split is pinned in `tools/check_ci_review_contract.py` so it
cannot drift silently. The application and Socket jobs in `ci.yml` honor the
label **one-shot**
— only at the `labeled` event; a later plain `synchronize` drops the cost-gated
full matrix / Socket scan back to the quick lane or skip unless auto-merge is
armed or dependency/workflow files changed. `codeql.yml` honors it
**persistently** — its `synchronize` arm re-reads the label set on every push
while the label is present, so security analysis keeps running for the life of
a flagged PR. Do not "unify" the two by making CodeQL one-shot: CodeQL is
advisory and cheap, and one-shot would cut security coverage.

Socket keeps a visible
PR check, but fast-skips unless dependency/security-relevant files changed or
full CI was requested. Dependabot auto-merge enables GitHub auto-merge for
patch/minor updates, but does not try to approve the PR with `GITHUB_TOKEN`;
this repo's workflow token is not allowed to create PR reviews.

This repository no longer refreshes the command pack from CI. The thin
conversion moved the payload to the machine install, so there is nothing left
in this tree for a scheduled installer run to update: `install.py --force`
against a thin consumer reports `current` and writes nothing. Refreshes are
initiated by the operator against the machine install instead. The scoped
`SD_AI_COMMAND_PACK_PR_TOKEN` Actions secret that automation used is no longer
read by any workflow here.

Every pull request also runs `Windows collection (advisory)`: the locked Python
3.14 development environment followed by `pytest --collect-only -q` on
`windows-latest`. The job uses `continue-on-error` and is deliberately absent
from both aggregate dependency lists, so it exposes import-time portability
regressions without becoming a branch-protection requirement.

Auto-merge never lands on quick-lane evidence: arming it triggers a
full-matrix run on the current head, and every subsequent push to an armed PR
classifies as full CI (the event payload's `auto_merge` field gates both
`synchronize` and later `labeled` runs, so no label-ordering race can leave a
quick run as the surviving gate). Manual dispatch also forces the classifier's
application lane even when the tip commit contains only documentation. Pushes
to `main` run in per-commit concurrency groups, so a
merge burst cannot cancel a previous merge commit's full-suite backstop run.

`tools/check_ci_review_contract.py` is the local guard for this cadence
contract, and `tools/check_copilot_instruction_contract.py` guards the
mechanical Copilot-instruction contract. Both are text-based and stdlib-only,
so pre-commit, the lightweight CI lane, and `scripts/check-review-preflight.mjs`
hard-fail on drift between workflows, scripts, instructions, and docs without
installing the full project environment.

**PR-body scope headings have no gate at all.** They were checked by a
best-effort advisory in the installed command pack, which fired only when a PR
body was handed to it locally and never failed CI; it left with the pack on
2026-08-30. So the headings are author discipline: the PR template prompts for
them and the pre-PR checklist covers them. Building a real gate would mean a
repo-owned check that reads the PR body in the app lanes and skips bot authors;
that is deliberately not wired today, and this paragraph should be rewritten
rather than quietly outgrown if it ever is.

## Work-Item Archival And The Generated Repository Map

`docs/repomix-map.md` is a generated structural map of the tracked tree,
refreshed by `./scripts/update_repomix`. Nothing regenerates it automatically,
so it goes stale whenever files move and the map does not move with them.
`tools/check_repomix_map_freshness.py` fails when a path the map lists is no
longer tracked; read that script's docstring for the full contract.

**Archiving a work item needs a map refresh.** Moving
`docs/work/<slug>/` into `docs/work/archive/<month>/` moves paths the map
lists, so the same commit must carry a regenerated map.

That was not always true, and the reason it changed is worth knowing before
anyone reverses it. Work items used to be excluded from the map, because the
command pack's completion finalization required the delta after the last work
commit to contain only bookkeeping paths and rejected `docs/repomix-map.md`
there with `bundle_scope_invalid` — so an archive commit could satisfy the
freshness guard or the finalization gate, never both, and excluding the moving
tree was the only shippable answer. That gate left with the pack on 2026-08-30.
The exclusion went with it, and the ordinary rule now applies uniformly.

That ordinary rule: when a change moves or deletes tracked files anywhere,
regenerate the map with `./scripts/update_repomix` and commit the result
alongside that change.

## Release Process

AMC uses semantic versions while it remains in the `0.x` series: a minor
release may contain features or breaking changes, while a patch release is for
backward-compatible fixes. Every release starts with a PR that:

1. promotes `CHANGELOG.md`'s `Unreleased` content to a dated version heading
   and leaves a fresh empty `Unreleased` section;
2. updates `project.version` in `pyproject.toml` and regenerates `uv.lock` so
   the editable project package carries the same version;
3. names any breaking Python-floor, CLI, file-format, or server/API change;
4. passes the focused checks, full local gate, and required remote review/CI.

After that PR merges, create an annotated `vX.Y.Z` tag on the exact merge
commit, push the tag, and create the matching GitHub Release from the promoted
changelog section. Verify the release itself by installing from that tag into
a fresh virtual environment and checking both console scripts plus
`amc --version`. Tagging and GitHub Release creation are outward-facing steps
and require explicit maintainer approval for that release.

## Review Economy Rules

- Keep branch protection on `CI Result`, not lane-specific job names.
- Apply `full-ci` after substantial runtime changes, before merge if the last
  remote full matrix is stale, or whenever the quick lane is not enough
  evidence for the risk.
- Arm auto-merge when you open the PR: arming re-gates the current head on the
  full matrix, so arming after a green full lane costs one redundant full run.
- Prefer a local full-check plus one remote final review over repeated
  Copilot/Actions loops.
- If a review comment points to a recurring mechanical pattern, add or update a
  `tools/check_*.py` guard and test rather than relying on prose alone.

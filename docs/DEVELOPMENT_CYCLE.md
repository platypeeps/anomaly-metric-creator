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

Use the Trellis full-check script instead of manually remembering the review
guard list:

```bash
SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=0 SD_AI_COMMAND_PACK_FULL_CHECK_GITO=0 bash ~/.agents/bin/sd-ai-command-pack-full-check.sh
```

This is the fast iteration path after the focused deterministic checks for the
changed surface pass. Re-enable Prism for the final local review when practical.
If the generated Obsidian KB freshness check trips after a pull, refresh the
gitignored output before rerunning the gate:

```bash
.venv/bin/python3 ~/.agents/bin/sd-ai-command-pack-update-spec-kb.py
```

The pack-provided full-check script runs whitespace checks, the shared
review preflight in `~/.agents/bin/sd-ai-command-pack-review-preflight.mjs`, AMC's
repo-local review preflight in `scripts/check-review-preflight.mjs`, the
copied/generated scope preflight in `~/.agents/bin/sd-ai-command-pack-review-scope.sh`,
the structural install audit in `~/.agents/bin/sd-ai-command-pack-install-audit.py`,
current-diff CI classification, configured package scripts when present, and
optional Prism/Gito review. AMC's repo-local review preflight runs the
CI/review cadence guard, the Copilot instruction contract guard, the PR-body
scope guard, and the canonical clean-module mypy gate. Review-churn mutation
tests remain in GitHub CI instead of being repeated by the local gate.

Install the repository's non-default Git hook stages once per clone:

```bash
.venv/bin/pre-commit install --hook-type pre-push
.venv/bin/pre-commit install --hook-type commit-msg
```

The pre-push hook checks the current branch name. The commit-msg hook passes the
message file to `tools/check_role_name_leaks.py` before Git records it.

When a PR body exists, pass it to local preflight with
`SD_AI_COMMAND_PACK_PR_BODY_SCOPE_PR_BODY` or
`SD_AI_COMMAND_PACK_SCOPE_PR_BODY`, or write it to a file and run
`python ~/.agents/bin/sd-ai-command-pack-pr-body-scope.py --body-file <path>`.
The scope guard reads `.sd-ai-command-pack/pr-body-scope.json`, merges it with
the rule defaults in `~/.agents/bin/sd-ai-command-pack-pr-body-scope.py`, and fails
broad behavior-changing diffs unless the body contains the matching scope
section. The five canonical headings are `Automation scope:`,
`CI/review scope:`, `Tooling/generated scope:`, `Docs/user-facing scope:`, and
`Runtime/server scope:`.

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
bash ~/.agents/bin/sd-ai-command-pack-full-check.sh
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
`src/`, `scripts/`, `.agents/`, and `.trellis/`, so these checks apply to every
application lane rather than depending on local hooks.

| Lane | Runs When | Purpose |
| --- | --- | --- |
| `lightweight readiness` | Docs, Trellis specs/tasks/audit artifacts, agent prompts/skills, Prism rules, command-pack metadata, review-tooling scripts, or explicitly enumerated repo-only automation with no skipped behavioral tests | Catch whitespace, shell syntax, Python syntax, workflow pip, and Trellis artifact hygiene issues under uv-managed Python 3.14 without installing the full dev environment. |
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
so pre-commit, the lightweight CI lane, and
`~/.agents/bin/sd-ai-command-pack-full-check.sh` hard-fail on drift between
workflows, scripts, instructions, and docs without installing the full
project environment.

`~/.agents/bin/sd-ai-command-pack-pr-body-scope.py` is a **best-effort advisory,
not a hard CI gate.** It fails only when a PR body is supplied — the local
preflight path (`--body-file` or the `SD_AI_COMMAND_PACK_PR_BODY_SCOPE_PR_BODY`
/ `SD_AI_COMMAND_PACK_SCOPE_PR_BODY` env vars) — and that body omits the scope
section matching the changed paths. CI runs it in the lightweight lane
*without* passing the PR body, so there it only reports detected scope
categories and never fails the build; it is also not wired into the
`quick`/`full` lanes, so `src/**` changes are not scope-checked in CI at all.
Treat the scope headings as author discipline — the PR template prompts for
them and the pre-PR checklist covers them — not a CI-enforced merge gate.
Turning it into a real gate would mean passing the PR body plus an `--actor`
bot-skip (on a pack version that ships it) and running the check in the app
lanes; that is deliberately not wired today.

### Local review-gate helper forwarders

`scripts/sd-ai-command-pack-*` are five repo-owned forwarders, not copied pack
payload. They exist to resolve a contradiction inside the command pack itself,
and the shape of that contradiction is why the obvious simpler fixes do not
work:

- the pack's `sd-check` resolves its shipped helpers **only** at
  `<repo>/scripts/sd-ai-command-pack-<name>`, and requires a **regular file**
  there — a symlink is explicitly rejected;
- the pack's `install-audit` **fails** on any `sd-ai-command-pack-*` file in
  the repo that is not listed in `.sd-ai-command-pack/installed-targets.txt`;
- since the thin-install conversion the pack installer places **no** such
  files, so every `sd-check` builtin row reported `unavailable`.

`unavailable` outranks `passed` in the aggregate, so `sd-check` never reached
`passed` and `sd-review scope=pr` failed closed for **every** pull request.

Registering an equivalent command in the pack's optional repo-owned check
configuration file (`check.json`, which this repo does not carry) does not fix
it either: the builtin rows are emitted regardless of configuration, and the
`unavailable` row still dominates the aggregate. Gitignoring the forwarders
downgrades the audit failure to a warning, but then they do not travel with the
checkout, so the gate breaks again for everyone else.

What works, and what this repo therefore does: keep the five forwarders as
tracked regular files, and list them in `installed-targets.txt` so the audit
recognizes them. Each forwarder resolves its target **by name on PATH** rather
than by absolute path, so a pack version bump does not strand it on a stale
install directory, and each replaces its own process (`os.execv` / `exec` /
`spawnSync` + exit-code passthrough) so the real helper's exit code and streams
reach the caller unchanged.

Three PATH behaviours are shared by all the forwarders and are deliberate. Each
removes **its own directory** from the search path — it shares a basename with
its target, so a checkout that puts `scripts/` on PATH would otherwise resolve
the forwarder to itself and loop forever. Each **drops empty PATH entries**
rather than reading them as POSIX's implicit current directory, which would let
the caller's working directory supply the helper the gate runs. And each sets
`SD_PACK_FORWARD_ACTIVE` to its target name across the exec, refusing a second
hop for the same target: stripping one directory closes the *self* loop but not
the *mutual* one, where two checkouts on PATH have each forwarder strip only its
own directory and exec the other's copy. The marker is keyed by target, so a
helper that legitimately invokes a different pack helper is unaffected.

The three Python forwarders share one implementation in
`scripts/_sd_pack_forward.py`; that module is deliberately *not* named
`sd-ai-command-pack-*` or `sd_ai_command_pack_*`, because those are the pack's
own `PACK_FILE_PATTERNS` and a matching file must appear in the receipt as
though the installer had placed it.

When the pack fixes this upstream — by installing these files itself, or by
resolving helpers off PATH — delete the forwarders, drop their
`installed-targets.txt` entries, and delete `scripts/_sd_pack_forward.py` and
`tests/test_sd_check_helper_forwarders.py`. Until then, adding or renaming one
means updating the receipt in the same diff; the test enforces exactly that.
Sources: `scripts/sd-ai-command-pack-*`; `scripts/_sd_pack_forward.py`;
`.sd-ai-command-pack/installed-targets.txt`;
`tests/test_sd_check_helper_forwarders.py`.

## Task Archival And The Generated Repository Map

`docs/repomix-map.md` is a generated structural map of the tracked tree,
refreshed by `./scripts/update_repomix`. Nothing regenerates it automatically,
so it goes stale whenever files move and the map does not move with them.
`tools/check_repomix_map_freshness.py` fails when a path the map lists is no
longer tracked; read that script's docstring for the full contract.

Completion-mode finish-work makes one of those moves by construction. `task.py
archive` relocates `.trellis/tasks/<slug>/` into
`.trellis/tasks/archive/<month>/` *after* the map was last generated, so every
completion ship strands those entries unless the map is regenerated in the same
change. Run the steps in this order:

1. `python3 ./.trellis/scripts/task.py archive <slug>`
2. `./scripts/update_repomix`
3. commit the archive move together with the regenerated map

`task.py archive` commits by itself, without `--no-verify`, so the pre-commit
hook does fire inside it and that auto-commit is the one the guard fails. The
result is mild rather than half-applied: `task.py` prints `[WARN] Auto-commit
failed:` and returns, leaving the move applied and staged. Regenerate the map
and commit to finish the job.

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

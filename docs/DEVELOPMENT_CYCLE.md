# Development cycle

AMC uses a local-first review cadence. The merge gate runs on this machine
(`make check`), and remote AI review is kept for changes that need it.

## While Editing

Run the smallest deterministic check for the surface you touched. Common
examples:

```bash
.venv/bin/pytest tests/test_server.py -q -k "apply or rollout"
.venv/bin/pytest tests/test_local_gate_contract.py -q
.venv/bin/ruff check tests/
git diff --check
```

Use focused tests first, then broaden only when the changed boundary warrants
it. Runtime, parser, server, gate, dependency, and review-tooling changes
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
checks that are deliberately not per-file hooks: the local gate contract guard,
the Copilot instruction contract guard, and the canonical clean-module mypy
gate. Review-churn mutation tests run in the pytest partitions of
`make check`.

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

The paragraphs that stood here described how that guard matched — anchoring,
case-insensitivity, an optional trailing colon, an alias set — in the present
tense, next to the sentence saying nothing checks. `_body_has_heading` is not
in this repository; it was the pack's. A reader can no longer verify any of it,
so it is gone rather than kept as folklore. Write one of the five headings
above; a reviewer reads the body.

Before marking a PR ready or requesting a final remote review, run the local
gate with Prism enabled when practical:

```bash
.venv/bin/pre-commit run --all-files && node scripts/check-review-preflight.mjs
```

Before merge, run the full gate. `sd-ship merge` runs it anyway:

```bash
make check
```

For a full local suite without the gate, use the normal four-worker default:

```bash
.venv/bin/pytest
```

The heavy/light split in `make check` collects coverage per partition; it is
not the fastest local path. On the current development host, the bare suite completed in 253.36s,
while the serial heavy partition alone took 345.01s. Use the sequential split
only when memory pressure makes fixture fan-out unsafe; lower the light worker
count on smaller machines:

```bash
.venv/bin/pytest -n 0 --dist loadfile -m heavy
.venv/bin/pytest -n 2 --dist loadfile -m "not heavy"
```

## The Merge Gate

GitHub Actions runs no workflow in this repository; the workflow files were
deleted on 2026-09-28. The merge gate is local: `sd-ship merge` runs
`make check` and posts `sd/local-gate`, the one required check. `make check`
runs `scripts/check.sh`, which builds `.venv-check` from `uv.lock` and runs
every step: the repository guards, readiness checks, the heavy and light pytest
partitions with the 85% coverage gate, the real kubectl and Helm smokes,
`pre-commit run --all-files`, the review preflight, and the Socket dependency
scan. The step list and rationale live in the script header and in
[testing-quality.md](spec/amc/backend/testing-quality.md) § Local Review and
Merge Gates.

`tools/check_local_gate_contract.py` guards the gate's named anchors, and
`tools/check_copilot_instruction_contract.py` guards the mechanical
Copilot-instruction contract. Both are text-based and stdlib-only, so
pre-commit, `scripts/check.sh`, and `scripts/check-review-preflight.mjs`
hard-fail on drift without installing the full project environment.

### Pinned tool bumps

The real kubectl and Helm client smokes are opt-in. They run only with
`AMC_RUN_REAL_CLIENT_SMOKE=1` and the clients on `PATH`; `make check` sets the
variable, so it tests the gate host's installed clients. When those client
versions move, keep `server_ops.py`'s advertised Kubernetes version
within supported kubectl skew, update README's tested-version sentence, and run
both real-client smokes. Sources: `src/anomaly_metric_creator/server_ops.py`;
`tests/test_server.py`; `README.md`.

Two exact Python-tool pins have no automated bump path. Dependabot cannot
reach them: the `uv` ecosystem runs `versioning-strategy: lockfile-only`, which
leaves `pyproject.toml`'s manifest untouched, and it never reads
`scripts/check.sh`.

- **`mypy==2.1.0`** — `pyproject.toml` `dev` extra. To bump: raise the pin,
  run `uv sync --extra dev`, then run `python tools/check_mypy_gate.py` and
  confirm the clean-module list is still error-free. A new mypy release that
  reclassifies errors in the gated modules blocks the bump until the modules
  are fixed — never drop a module to pass.
- **`socketsecurity==2.4.10`** — the `uvx --from` pin in `scripts/check.sh`'s
  Socket step. To bump: raise the pin, then run the step with
  `SOCKET_SECURITY_API_KEY` set and confirm it exits 0. Version 2.1.0 fails
  against the current Socket API with `APIResourceNotFound`.

Code scanning runs through GitHub's CodeQL default setup, enabled by the
organization's "GitHub recommended" security configuration. The repository has
no CodeQL workflow, and default setup rejects advanced-setup uploads. CodeQL is
not a required check.

Dependabot watches the `uv` and `pre-commit` ecosystems. Nothing auto-merges
its PRs, and `sd-review` refuses their unattributed commits, so each bump lands
as an attributed replacement PR and the Dependabot PR is closed. An
`astral-sh/ruff-pre-commit` replacement also raises the `ruff==` pin and
relocks (`uv lock --upgrade-package ruff`). The steps are in
[testing-quality.md](spec/amc/backend/testing-quality.md) § Local Review and
Merge Gates.

This repository does not refresh the command pack. Refreshes are initiated by
the operator against the machine install.

**PR-body scope headings have no gate at all.** They were checked by a
best-effort advisory in the installed command pack, which fired only when a PR
body was handed to it locally; it left with the pack on 2026-08-30. So the
headings are author discipline: the PR template prompts for them and the pre-PR
checklist covers them.

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
4. passes the focused checks, the full local gate (`make check`), and the
   required review.

After that PR merges, create an annotated `vX.Y.Z` tag on the exact merge
commit, push the tag, and create the matching GitHub Release from the promoted
changelog section. Verify the release itself by installing from that tag into
a fresh virtual environment and checking both console scripts plus
`amc --version`. Tagging and GitHub Release creation are outward-facing steps
and require explicit maintainer approval for that release.

## Review Economy Rules

- Keep `sd/local-gate` the one required check; do not re-enable Actions
  workflows to get a missing check.
- Prefer a local `make check` plus one remote final review over repeated
  Copilot loops.
- If a review comment points to a recurring mechanical pattern, add or update a
  `tools/check_*.py` guard and test rather than relying on prose alone.

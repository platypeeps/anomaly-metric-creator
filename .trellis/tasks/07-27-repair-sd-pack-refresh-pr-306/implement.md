# Implementation plan: Repair and review SD command-pack refresh PR 306

## 1. Establish clean baselines

- [ ] Refresh `main` and PR #306 metadata; record exact consumer base and head.
- [ ] Inventory all newly visible `.claude/` files and verify local-only paths
  remain ignored.
- [ ] Fetch the source pack and create a dedicated worktree/branch from its
  current `origin/main`, leaving the existing checkout unchanged.
- [ ] Create and activate the source repository's required Trellis task before
  editing shipped payload files.

## 2. Repair and release the source pack

- [ ] Update `installer/registry.py` so Gemini owns
  `.gemini/settings.json` as a Trellis-local path.
- [ ] Update
  `templates/scripts/sd-ai-command-pack-review-scope.sh` and synchronize the
  root `scripts/` mirror through canonical generation/sync commands.
- [ ] Add focused regression coverage in `tests/test_install_core.py` for
  registry/classifier parity.
- [ ] Bump the patch version and add the matching top `CHANGELOG.md` entry.
- [ ] Run focused tests, generation/template parity, then `make release-prep`
  and the source review gates.
- [ ] Commit, push, create/review the upstream pack PR, resolve all threads,
  merge through its guarded lifecycle, and verify the release tag/identity.

## 3. Regenerate the consumer PR

- [ ] Merge current consumer `main` into
  `automation/sd-ai-command-pack-sync`; resolve only scoped conflicts.
- [ ] Run a conflict-aware dry-run using the released source pack.
- [ ] Apply the canonical installer without force replacement unless the
  dry-run proves all replacements are pack-owned and conflict-free.
- [ ] Add the intended shareable `.claude/` generated surfaces while excluding
  ignored local state.
- [ ] Refresh `.obsidian-kb` and `docs/repomix-map.md` using canonical helpers.
- [ ] Review the complete diff for secrets, absolute paths, generated drift,
  and unrelated files.

## 4. Validate and converge review

- [ ] Run focused classifier/install-audit tests and `git diff --check`.
- [ ] Run the typed `sd-check`; record dispositions for every applicable
  first-review advisory and require a passing state guard.
- [ ] Commit and push the consumer repair.
- [ ] Reply to and resolve the original Copilot thread with upstream release
  evidence.
- [ ] Request a fresh configured remote review, wait for materialization, and
  re-read all GraphQL threads plus exact-head CI.
- [ ] Repeat the bounded fix/review loop until clean or until a documented
  decision/round limit requires user input.

## 5. Finish

- [ ] Run the one-time PR-scoped review-learnings dry run.
- [ ] Execute `sd-finish-work` for the consumer task and push any resulting
  archive/journal commit.
- [ ] Reconfirm exact PR head, required CI, unresolved thread count, and clean
  working tree; hand off merge readiness without merging unless the active
  lifecycle command owns that authority.

## Validation commands

Source pack:

```bash
python -m pytest tests/test_install_core.py -k 'platform_registry_dirs_covered_by_shipped_scanners or gemini'
make generate
make release-prep
```

Consumer:

```bash
git diff --check
bash scripts/sd-ai-command-pack-toolchain.sh run-python -- scripts/sd-ai-command-pack-check.py --json
bash scripts/sd-ai-command-pack-toolchain.sh run-python -- scripts/sd-ai-command-pack-install-audit.py
```

## Risk and rollback points

- Stop before installer application if dry-run reports conflicts.
- Stop before adding `.claude/` files if any contain local permissions,
  credentials, caches, sessions, or absolute machine-specific paths.
- Do not force-push either public PR branch.
- Do not remove the isolated source worktree until the upstream PR is merged
  and the consumer refresh can reproduce the released payload.

## First-review risk dispositions

- The structured-input, subprocess, environment, filesystem, normalization,
  and diagnostic-redaction categories come from newly tracked generated
  Trellis Claude hooks and skills, not new AMC runtime behavior. Their source
  surfaces were preserved, portable-path/secret scanned, and will be covered
  by the repository hook lint, command-pack audit, deterministic check, and
  remote review gates.
- The large authored-line advisory is generated-surface volume: 69 shareable
  Claude adapters and skills became trackable under the existing ignore-policy
  change. The payload is intentionally kept together so reviewers can verify
  complete generated-surface closure; focused review remains on integration,
  provenance, portability, and secrets.
- PR #306 must carry an explicit `Tooling/generated scope:` section before the
  next configured remote review request.

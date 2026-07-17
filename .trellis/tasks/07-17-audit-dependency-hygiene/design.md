# Dependency hygiene — Design (SD Work Designs, 2026-07-17)

## Overview

Three items: the repo's only nondeterministic dependency
(`@opencode-ai/plugin` `^1.14.39` in `.opencode/package.json`, zero
imports, no lockfile, auto-installed by the OpenCode runtime), two exact
pins with no update path (`mypy==` in pyproject, `socketsecurity==` in
ci.yml), and the vendored `security-best-practices` skill (five
byte-identical copies) with no provenance.

## Proposal

- **A-043 — remove-first.** Empirically verify the OpenCode loader
  tolerates an absent dependency block: delete the entry in a scratch
  checkout, launch OpenCode against the repo, confirm the `.opencode`
  tooling loads. If tolerated → remove the dependency and the npm
  Dependabot ecosystem entry (it then watches nothing). If NOT tolerated
  → pin exact (`1.14.39`, no caret) and commit the lockfile the runtime
  generates, adding it to the Dependabot ecosystem properly. The
  verification transcript goes in the PR description.
- **A-044 — documented update path.** Neither pin can ride
  `lockfile-only` Dependabot. Cheapest durable fix: a "Pinned tools
  bump" subsection in docs/DEVELOPMENT_CYCLE.md's release/maintenance
  checklist (mypy==, socketsecurity==, where each lives, how to verify a
  bump: mypy baseline count comparable / Socket job green), referenced
  from the pre-PR CI-hygiene heading. A Dependabot-visible relocation
  (requirements sidecar) is more machinery than two pins justify —
  rejected, recorded here.
- **A-045 — provenance note.** In the canonical copy of
  `security-best-practices` (the one the others mirror — identify by the
  pack manifest/provenance layout), add a provenance header: upstream
  URL, release/commit vendored, refresh procedure (re-vendor + re-fan-out
  the five copies). README's skills section gets one sentence. If the
  canonical home turns out to be pack-owned (refresh would clobber local
  edits), put the provenance note in repo docs instead and record the
  paste-ready upstream suggestion.

## Boundaries And Non-Goals

- No version bumps themselves (A-046 floor-raising belongs to
  doc-accuracy-sweep's manifest change); no new automation (the pack-sync
  workflow is ci-cadence-closures A-063).

## Affected Files

`.opencode/package.json`, `.github/dependabot.yml`,
`docs/DEVELOPMENT_CYCLE.md`, the security-skill canonical copy or repo
docs, README, `.trellis/audit/ledger.md` flips (A-043/A-044/A-045).

## Risks And Edge Cases

- The OpenCode tolerance check must be live, not assumed — the runtime
  auto-install behavior is the whole finding.
- Dependabot entry removal must not orphan other npm surfaces (grep for
  any other package.json first — expect none).
- Vendored-copy edits: whichever copy is edited, keep all five identical
  (byte-identity is the current state; a divergence would itself be
  drift) — script the fan-out in the PR.

## Validation

- OpenCode launch transcript (A-043 branch decision evidence).
- `grep -rn "opencode-ai/plugin"` returns nothing (remove branch) or a
  pinned entry + lockfile (pin branch).
- Five-copy identity check: a full-tree `sha256sum` aggregate per copy
  directory (every file — SKILL.md, `agents/*`, `LICENSE.txt`,
  `references/*` — not just SKILL.md), asserting all five aggregates match.
- Full suite + pre-commit (docs-only otherwise).

---
name: sd-update-spec
description: Use when the user wants the Software Delivery update-spec command to run Trellis update-spec and the pack's extended spec-refresh features.
---

# SD Update Spec

Run the Trellis update-spec workflow for the current repository, then run the SD
AI command pack extensions. These extensions preserve repository knowledge that
the base Trellis skill does not own directly: repospec/Repomix refreshes,
architecture overview touch-ups when warranted, and `.obsidian-kb` copies for
portable Obsidian knowledge-base workflows.

## Structured decisions

Read [`../sd-help/references/structured-questions.md`](../sd-help/references/structured-questions.md)
before asking. This skill owns only `update-spec.ownership-scope`; use it when
architecture or ownership evidence leaves a material scope ambiguity. Do not
ask for a normal bounded spec refresh already requested by the user.

1. Resolve the `trellis-update-spec` skill by name using the agent's trusted
   skill discovery mechanism for installed skills.
2. If the Trellis update-spec skill is missing, unreadable, empty, resolves to
   more than one candidate, fails validation, defines contradictory steps that
   violate this command's safety rules, or requires unavailable tools, stop and
   report the exact blocker.
3. Use the Trellis update-spec skill as the primary instructions. Do not
   modify, replace, fork, or reinterpret it. It is responsible for deciding
   what `.trellis/spec/` content should change.
4. After the Trellis update-spec pass, route only the applicable SD extensions:
   - Load [`references/repository-map.md`](references/repository-map.md) when
     the user explicitly requests a repospec/repository-map refresh or the
     repository has the exact map-maintenance infrastructure described there.
   - Load [`references/architecture.md`](references/architecture.md) when the
     user explicitly requests architecture maintenance, or an existing
     architecture overview and changed architectural signals make it
     applicable.
   - Run the normal KB refresh from the repository root without loading an
     optional reference:
     `bash scripts/sd-ai-command-pack-toolchain.sh run-python -- scripts/sd-ai-command-pack-update-spec-kb.py`.
     Load [`references/obsidian-kb.md`](references/obsidian-kb.md) only for an
     explicit preview, an occupied/symlinked/conflicting KB path, helper
     failure recovery, or detailed ownership/report interpretation.
   - A routine spec-only run loads no optional reference. Independent
     extensions may load more than one direct reference, but load each at most
     once and never follow a reference from another reference.
   - Before using a selected reference, require that exact path below this
     installed skill to be readable, non-empty, and consistent with this
     skill's safety rules. Stop and report a missing, unreadable, empty,
     escaping, or contradictory selected reference instead of silently
     skipping the extension.
   - Keep the extension order repository map, architecture, then KB so a map or
     overview update is included in the generated knowledge copy.
5. Final report:
   - `Update-spec skill`: path read
   - `Spec updates`: paths changed, or `none`
   - `Repospec`: refreshed path/tool, `not present`, or `no infrastructure`
   - `Architectural overview`: updated path, `not present`, or `not warranted`
   - `Obsidian KB`: `.obsidian-kb` created/refreshed, copy count, dashboard
     state, gitignore state, and any conflicts
   - `Obsidian vault copy`: example command for copying this repo's
     `.obsidian-kb` folder into a vault. State that `/path/to/your/vault` is a
     placeholder the user must replace, and derive the final link name from the
     repository name when possible, such as
     `cp -R "$(pwd)/.obsidian-kb/." "/path/to/your/vault/Repo-KB"`
   - `Validation`: checks run, or why checks were not run

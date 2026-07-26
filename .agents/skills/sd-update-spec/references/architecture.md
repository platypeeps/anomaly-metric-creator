# Architecture extension

Load this direct reference only when architecture maintenance was requested or
when an existing overview and changed architectural signals make it applicable.
This reference does not load another reference.

Search existing files, especially `ARCHITECTURE.md`,
`ARCHITECTURE_OVERVIEW.md`, `docs/ARCHITECTURE.md`,
`docs/ARCHITECTURE_OVERVIEW.md`, and
`.trellis/spec/**/architecture*.md`. Do not create an overview unless the user
asks for one.

Update an existing overview only when preserved work changed a package/module
boundary, service or command surface, cross-component data flow,
persistence/storage schema, external integration, config/env contract, or
runtime/deployment topology. Ground the decision in changed files, Trellis
specs, or task notes. If no overview exists, no signal changed, or scope is
unclear, leave the overview untouched; ask the canonical
`update-spec.ownership-scope` question only for a material ambiguity that the
evidence cannot resolve.

Report the updated path, `not present`, or `not warranted`.

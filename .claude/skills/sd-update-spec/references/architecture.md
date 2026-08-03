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

When this pass creates or materially updates a workflow, architecture,
sequence, data-flow, lifecycle/state, or similar technical visual that is part
of the repository documentation, resolve the `archify` skill by name through
the agent's trusted skill discovery mechanism. If it is available, readable,
and valid, read it completely and use it as the primary diagram generation,
rendering, validation, and delivery contract. Preserve the target repository's
document format, artifact location, naming conventions, and ownership rules;
use the Archify renderer that matches the visual instead of changing the
documentation contract to fit the tool.

Archify is an optional enhancement, not a pack dependency. If it is unavailable
or cannot be used in the current environment, continue with the repository's
documented visual tooling or the existing manually maintained format and report
the fallback. Do not install Archify, fail the overall update-spec workflow
solely because it is absent, create an unsolicited visual, or invoke it when no
repository visual is being created or materially updated.

Report the updated overview path, `not present`, or `not warranted`, plus the
visual artifact path and Archify validation/delivery evidence, the repo-native
validation used, `not applicable`, or the graceful fallback reason.

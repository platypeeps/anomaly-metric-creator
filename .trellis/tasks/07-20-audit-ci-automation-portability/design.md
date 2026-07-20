# Add CI automation and Windows collection coverage Design

## Overview

This child adds the parent task's two standing/advisory workflows after the
existing CI contract has been corrected and mirrored by the earlier children.

## Proposal

- Reuse the installed command-pack refresh surface in a weekly workflow,
  compare the resulting tree, and create a PR only for a real diff. Route that
  PR through the existing auto-merge/full-matrix gate rather than inventing a
  second merge path.
- Add a separate Windows collection job using the same locked development
  dependency declaration and Python floor. Keep it advisory by excluding it
  from the aggregate required-context dependency set.

## Boundaries And Non-Goals

- No silent auto-merge bypass or direct default-branch writes.
- No full Windows test suite yet; only collection/import portability.
- No merge without explicit maintainer approval for recurring PR creation.

## Affected Files

New or existing GitHub workflow files, workflow contract tests,
`CLAUDE.md`/CI documentation, relevant Trellis specs, and the audit ledger.

## Data And Command Contracts

The sync workflow is idempotent: no diff means no external side effect. Any PR
uses the repository's normal branch, review, and required-check contracts. The
Windows job is observable but not merge-blocking.

## Risks And Edge Cases

Recurring automation creates ongoing external work and therefore needs an
explicit maintainer decision. Windows path/shell differences may expose
collection errors; keep fixes narrow and avoid platform-specific test skips
that hide real import problems.

## Validation

Exercise workflow contract tests, validate a no-change refresh path, inspect a
real change path without bypassing review, and run the Windows collect-only job
in its PR before merge.

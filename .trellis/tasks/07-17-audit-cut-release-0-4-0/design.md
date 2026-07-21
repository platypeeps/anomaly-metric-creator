# Cut release 0.4.0 — Design (SD Work Designs, 2026-07-17)

## Overview

Verified state: `pyproject.toml:7` says `version = "0.3.0"`; tags are
`v0.2.0` / `v0.3.0` only; `CHANGELOG.md` already carries a rich
`## Unreleased` section (server mode, MCP, eval mode, trace-bundle,
`--start-time`, SECURITY.md); no `--version` flag or `__version__` exists
on the package/CLI surface. `server_mcp.py` has a private distribution-version
lookup for the MCP handshake, and `parse_args` now belongs to `cli_args.py`
after the completed legacy decomposition. `docs/DEVELOPMENT_CYCLE.md` exists
(not repo root). The audit's unreleased-history count is historical; live
release content is derived from the current changelog and merged tree.

## Proposal

One release PR carries A-057 + A-056 + A-055 + the A-054 version/changelog
promotion, so the tag placed after merge contains everything. Tagging before
the content lands is the failure mode to avoid.

- **A-057 (`--version` / `__version__`).** Add a small `version.py` owner for
  the `importlib.metadata` lookup, reuse it from the existing MCP version
  response, and add
  `p.add_argument("--version", action="version", version=...)` to
  `parse_args` in the common group (renders under `-h`; exits before
  `_reconcile_cli_surface`, so no reconciliation interaction). Version
  string comes from `importlib.metadata.version("anomaly-metric-creator")`;
  the shared helper catches `PackageNotFoundError` and accepts a caller-owned
  fallback so package/CLI use `"0+unknown"` while MCP preserves its historic
  `"unknown"`. Expose `__version__` in
  `src/anomaly_metric_creator/__init__.py` via the same helper. Extend the CLI
  help/subprocess coverage and add focused helper/facade identity tests.
- **A-056 (backfill).** Add to the Unreleased section before promotion:
  `### Security` — #213 response-header redaction flipped to
  mask-unless-known-safe; `### Fixed` — #134 combined-artifact component
  allowlist (stale/foreign CSVs excluded), #128 long-form merge fd
  exhaustion preflight.
- **A-055 (process on rails).** `docs/DEVELOPMENT_CYCLE.md` gains a
  "Release process" section: 0.x scheme (minor = features and/or breaking
  changes while 0.x, patch = fixes only), steps (promote Unreleased →
  versioned heading with date, bump `pyproject.toml`, release PR, tag
  `vX.Y.Z` on the merge commit, GitHub Release from the changelog section,
  verify install from the tag). Add a 15th pre-PR checklist heading
  **"Changelog / version impact"** to every mechanically guarded lockstep
  surface in one diff: the Trellis specs, CLAUDE.md, PR template, Copilot
  reviewer instructions, and `check_copilot_instruction_contract.py` plus its
  acceptance-test fixture.
- **A-054 (the cut).** Promote `## Unreleased` → `## 0.4.0 - <merge date>`
  with a `### Changed`/breaking line naming the Python-floor raise
  (3.11→3.14); leave a fresh empty `## Unreleased` stub. Bump
  `version = "0.4.0"`. After merge: `git tag v0.4.0 <merge-sha>`, push tag,
  `gh release create v0.4.0` with the changelog section as notes.

## Boundaries And Non-Goals

- No PyPI publish — the repo has no publish workflow; the release is
  tag + GitHub Release + pip-install-from-tag. Adding a publish pipeline is
  out of scope.
- No retro-tagging of intermediate states; 0.4.0 is cut from current main.
- `--version` is a plain argparse version action — no version output in
  `schema.json` or generated artifacts (that would change locked hashes).

## Affected Files

- `pyproject.toml` (version), `CHANGELOG.md`,
  `src/anomaly_metric_creator/version.py`, `cli_args.py`, `server_mcp.py`,
  `src/anomaly_metric_creator/__init__.py`, `docs/DEVELOPMENT_CYCLE.md`,
  `README.md`, `CLAUDE.md`, `.github/PULL_REQUEST_TEMPLATE.md`, Copilot
  reviewer instructions, `.trellis/spec/amc/backend/` checklist sources,
  the checklist contract guard and tests, CLI/version tests,
  `.trellis/audit/ledger.md` (flip A-054/A-055/A-056/A-057 to fixed).

## Risks And Edge Cases

- The decomposition is complete: add the flag to `cli_args.py`, not the
  compatibility facade in `legacy.py`, and keep the package/MCP version lookup
  single-sourced.
- The `--version` helper must not import-time-fail when
  `importlib.metadata` misses the dist (uninstalled checkout, zipapp).
- Checklist-heading edits must keep the three surfaces byte-aligned —
  Copilot's reviewer instructions flag drift.
- `tag`/`release` are outward-facing operations — perform them only in the
  implementing session with the user's go-ahead, after the PR merges.

## Validation

- `pytest tests/test_cli_surface.py tests/test_cli.py` plus full suite
  (hash safety: no generation-path change expected).
- Fresh-venv install check: `pip install .` then `amc --version` →
  `0.4.0`; after tagging, `pip install git+...@v0.4.0` smoke.
- `amc --help` shows `--version` in the common group; `--help-all`
  unchanged otherwise.

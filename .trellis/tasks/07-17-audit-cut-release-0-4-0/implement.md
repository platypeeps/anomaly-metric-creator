# Cut release 0.4.0 — Implementation Plan

## Execution Order

1. Branch from current `main`; verify the completed decomposition ownership
   map and refresh the older plan against `cli_args.py` and the existing MCP
   metadata lookup.
2. **A-057:** add the shared package-version helper (`importlib.metadata` +
   caller-owned `PackageNotFoundError` fallback); reuse it from MCP; wire
   `--version` (argparse `action="version"`) into `cli_args.py`'s common
   group; add `__version__` to `anomaly_metric_creator/__init__.py`.
   Tests: exact flag token in both help tiers, subprocess version action,
   package facade value, fallback behavior, and preserved MCP fallback.
3. **A-056:** backfill `### Security` (#213) and `### Fixed` (#134, #128)
   entries into the Unreleased section.
4. **A-055:** write the Release process section in
   `docs/DEVELOPMENT_CYCLE.md`; add the "Changelog / version impact"
   heading to Trellis specs, CLAUDE.md, PR template, Copilot instructions,
   and the mechanical checklist contract/test in the same commit.
5. **A-054:** promote Unreleased → `## 0.4.0 - <date>` (breaking line for
   the Python-floor raise first), re-stub Unreleased, bump
   `pyproject.toml` to `0.4.0`.
6. Flip A-054/A-055/A-056/A-057 to `status: fixed` in
   `.trellis/audit/ledger.md` (same PR, per ledger rules).
7. Open the PR as draft; walk the pre-PR checklist (including the brand-new
   heading — it applies to its own PR); mark ready; merge via the normal
   auto-merge flow.
8. **Post-merge, with specific user go-ahead:** `git tag v0.4.0 <merge-sha>`,
   `git push origin v0.4.0`, `gh release create v0.4.0 --title "0.4.0"
   --notes-from-tag` (or paste the changelog section). Then the fresh-venv
   tag-install smoke from design.md Validation.

## Validation Plan

```bash
.venv/bin/pytest tests/test_cli_surface.py tests/test_cli.py -n 0
.venv/bin/pytest                          # full suite before PR
.venv/bin/pre-commit run --all-files
python anomaly-metric-creator.py --version   # shim fallback path
python -m venv /tmp/relcheck && /tmp/relcheck/bin/pip install . \
  && /tmp/relcheck/bin/amc --version        # expect 0.4.0
```

## Documentation And Spec Updates

- CHANGELOG.md, docs/DEVELOPMENT_CYCLE.md, CLAUDE.md checklist,
  PR template, Trellis spec checklist source — all in the release PR.
- No CLAUDE.md architecture-map change (no module moves here).

## Review Notes

- The three checklist surfaces must stay in lockstep in one diff — that is
  itself a checklist item ("Doc / docstring sync").
- Watch anchored-matching rule for the new flag-presence test
  (`--version` vs any future `--version-x`).

## Follow-Ups

- If a PyPI publish pipeline is ever wanted, it is a new task (explicit
  non-goal here).

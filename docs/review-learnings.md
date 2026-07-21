# Review Learnings

<!-- sd-review-learnings:start -->
## SD Review Learnings

_Last updated: 2026-07-21_

### Local Pattern Findings
- No local review-cycle findings detected in the scanned diff.

### Recent Copilot Review Signals
- **historical** PR #277 `tools/check_test_resource_cost.py`: If a caller passes an existing path that is neither a directory nor a *.py file (e.g. a typo like tests/test_cli.pyy or a non-Python file), the tool silently ignores it and can exit 0 without scanning anything.... (https://github.com/platypeeps/anomaly-metric-creator/pull/277)
- **historical** PR #277 `tests/test_readme_scenario_catalog.py`: The README parser stores rows in a dict keyed by slug, but it doesn’t assert slugs are unique. A duplicated slug row would be silently overwritten and the test would still pass, which defeats the purpose of keeping... (https://github.com/platypeeps/anomaly-metric-creator/pull/277)

### Suggested Preventive Actions
- Move repeated mechanical findings into local checks where possible.
- Keep Copilot instructions focused on current, non-outdated unresolved findings.
- Treat generated or copied payloads as source/sync-contract review surfaces, not style-review surfaces.
<!-- sd-review-learnings:end -->

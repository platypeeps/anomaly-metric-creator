# Stop GB-scale module fixtures escaping the heavy marker

## Review context

- **Source:** deep-dive test-suite review, 2026-07-06.
- **Confidence:** CONFIRMED (fixture bodies and CI lane read end to end).
- **Severity:** HIGH — re-creates the OOM/disk-pressure class the
  heavy/light CI split was built to prevent.
- **Category:** testing / CI resource management.

## Goal

Guarantee that every test which generates or consumes a GB-scale dataset
is classified `heavy` (serial CI lane), regardless of whether the dataset
comes from a session fixture or a module fixture.

## Problem (verified 2026-07-06)

`_item_is_heavy` ([tests/conftest.py:177](tests/conftest.py:177)-184)
classifies by fixture *name* against `_HEAVY_SESSION_FIXTURES =
{seven_day_run, n3_one_day_dataset_dir, n3_seven_day_dataset_dir}`
(conftest.py:168-174). Three module-scoped fixtures regenerate GB-scale
datasets under other names and therefore run in the
`pytest -n 2 -m "not heavy"` lane (ci.yml):

- `seven_day_gauges_run`
  ([tests/test_gauges_file.py:59](tests/test_gauges_file.py:59)-67): full
  7-day 1s-cadence run + a ~50M-row `gauges.csv`.
- `seven_day_schema_run`
  ([tests/test_schema_file.py:64](tests/test_schema_file.py:64)-71): full
  7-day 1s-cadence run.
- `synthetic_n3_run`
  ([tests/test_topology_multi_instance.py:51](tests/test_topology_multi_instance.py:51)-121):
  ~0.5 GB N=3 1s-cadence generation (legitimately cannot reuse the session
  dataset — it overlays a synthetic scenario via `registry_overlay`).

This contradicts conftest.py:162-167 ("the only ones excluded from the
parallel PR smoke") and the registered `heavy` marker text in
pyproject.toml.

## Requirements

- Preferred fix for the two 7-day fixtures: **derive instead of
  regenerate** — hardlink the session `seven_day_run` dataset and call
  `write_gauges_csv` / `write_schema_json` directly, exactly as the same
  files' N=3 fixtures already do
  ([tests/test_gauges_file.py:483](tests/test_gauges_file.py:483)-495,
  [tests/test_schema_file.py:513](tests/test_schema_file.py:513)-535 are
  the sanctioned in-file patterns). That removes the duplicate generation
  cost AND places the tests inside the session-fixture closure so the
  heavy marker applies naturally.
- For `synthetic_n3_run`: extend the auto-application mechanism (e.g. an
  explicit module-fixture registry alongside `_HEAVY_SESSION_FIXTURES`) —
  do not hand-write `@pytest.mark.heavy` (the marker doc forbids it).
- Extend `tests/test_heavy_marker.py` so a future GB-scale module fixture
  cannot silently escape (assert the known set of full-resolution
  7-day / N>1 `main()`-invoking fixtures is classified heavy, with a
  non-empty-expected guard).
- Update the conftest comment and the pyproject marker text to match the
  final classification rule.

## Acceptance Criteria

- [ ] No test that generates or consumes a 7-day full-resolution or N=3
      full-resolution dataset collects into `-m "not heavy"`.
- [ ] Locked 7-day gauges/schema SHA-256 assertions still pass (deriving
      from the session dataset must not change bytes).
- [ ] `pytest -m heavy` / `-m "not heavy"` still partition the suite.
- [ ] Light-lane CI wall-clock does not regress (spot-check in the PR).

## Notes

- Related MEDIUM finding to take in the same PR if cheap: the same two
  files define `one_day_gauges_run` / `one_day_schema_run` module
  fixtures that also regenerate rather than derive (tolerated by the
  conftest "1-day runs stay in the parallel set" note, but the derive
  pattern removes the duplicate cost).

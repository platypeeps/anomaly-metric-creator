# Extract the persisted-overlay loader and close its remaining read-back gaps

## Goal

`src/anomaly_metric_creator/server_mutations.py` ends at 799 lines against the
800-line behavior-module cap and is **not** ratchet-enrolled, so it has one
line of headroom and no sanctioned way to grow. Three verified defects in the
`--persist-mutations` read-back path were found during PR #415's review and
each needs several lines to fix. Extract the loader into a leaf module first,
then fix them there.

## Context

Found in PR #415 (`feat/persisted-server-mutation-state`) review round 6 and
each confirmed by live probe against the merged code, not by reading:

```
schema_true:     ACCEPTED   {"schema_version": true}   -- Python True == 1
schema_float:    ACCEPTED   {"schema_version": 1.0}
missing_section: ACCEPTED   mutations object missing every section, loads silently
over_ring:       ACCEPTED   5 persisted events restored into a ring whose limit is 2
```

All three are in the opt-in, default-off path: a run without
`--persist-mutations` is unaffected. That is why they were deferred rather
than fixed in #415 — a ~250-line extraction inside a PR already reviewed six
times is worse than its own reviewed diff.

## Requirements

- Move `load_persisted_mutations`, `_hydrate_workloads`, `_hydrate_release`,
  the `_require_*` validators, `_arm_persistence`, `_persist_error`,
  `PERSIST_ERROR_PREFIX`, `_FIELD_TYPE_CHECKS`, and `_PERSISTED_ENVELOPE_KEYS`
  into a new leaf module, code moving verbatim.
- The dependency direction is one-way: the new leaf imports the dataclasses
  from `server_mutations`, and `server_mutations` must **not** import it back.
  Re-exporting the loader from `server_mutations` would create a cycle, so
  update the importers instead. There are only three:
  `server_ops.py` (`load_persisted_mutations`), `server.py`
  (`PERSIST_ERROR_PREFIX`), and `tests/test_server_mutation_persistence.py`.
- `schema_version` must be a plain integer. The current
  `schema_version != 1` guard accepts `True` and `1.0`, because Python
  compares both equal to `1`; reuse the existing `_is_int` helper, which
  already excludes `bool`.
- Every key in `_PERSISTED_MUTATION_FIELDS` must be **present** in the overlay,
  the same one-directional gap already closed one level up for the envelope in
  #415. The writer emits all of them, so a file missing one was truncated or
  hand-edited; `state.get(key, default)` currently restores it as empty in
  silence. Reading with `state[key]` afterwards keeps the two in lockstep.
- Restored `extra_events` must be trimmed to the run's `extra_event_limit`.
  A file written under a larger `--debug-ring-size` currently restores over
  the current limit until the next `record_event` happens to trim it.

## Acceptance Criteria

- [ ] `load_persisted_mutations` and its validators live in a leaf module that does not import `server_mutations`'s loader back, and `tools/check_module_size.py` passes with both modules under the 800-line cap and neither newly enrolled.
- [ ] `{"schema_version": true}` and `{"schema_version": 1.0}` are refused with the path named, covered by a test parametrized over both.
- [ ] An overlay object missing any `_PERSISTED_MUTATION_FIELDS` key is refused naming the missing key(s), covered by a test.
- [ ] Persisted `extra_events` longer than the run's `extra_event_limit` are trimmed at load, covered by a test that loads under a smaller limit than the file was written with.
- [ ] The existing 64 tests in `tests/test_server_mutation_persistence.py` pass unchanged apart from import-path updates, proving the move was verbatim.

## Notes

- Source: PR #415 review round 6, deferred by an explicit user decision to land
  #415 and track the remainder here.
- Six of that round's nine findings were rebutted or were provider churn —
  one asked to undo the `delete_pod` commit an earlier round had asked for.
  Four are carried forward, one per Requirements bullet above: the extraction
  itself, which is what brings both modules under the 800-line cap, plus three
  loader defects — the `schema_version` guard, the missing-key gap, and the
  untrimmed `extra_events`.

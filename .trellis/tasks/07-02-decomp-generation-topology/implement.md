# Extract generation.py + topology_impl.py — Implementation Plan

## Execution Order

1. **Precondition gate:** steps 8 (`cli_args`) and 9 (`catalog-data`) are
   merged with green hashes. Branch from `main`.
2. Authoritative monkeypatch grep over `tests/` for every inventory name;
   turn design.md's policy table into the concrete per-test edit list.
   Audit `generate_component` for global reads of `DERIVATIONS` (decides
   its home) and the dispatch-cluster boundary (decides
   `anomaly_dispatch.py` vs single-file + recorded deviation).
3. Extract **topology first** (models + registries + validators →
   `topology_impl.py`; composition → `topology_compose.py`), re-import in
   `legacy.py` at the deleted block's position, run the full suite. The
   topology cut is upstream of generation's captures — landing it first
   keeps each commit's blast radius single-cluster.
4. Extract generation (`generation.py` [+ `anomaly_dispatch.py`]),
   re-import, full suite again.
5. Migrate the enumerated test patch targets; extend CLAUDE.md's
   monkeypatch note with the new canonical homes; update the module map.
6. Grep both deleted ranges for `^from \.` (splice hazard); confirm every
   leaf re-import still resolves; `wc -l` table for the PR.
7. Draft PR (`full-ci`) → pre-PR checklist → ready → merge.
8. Epic close-out: tick step 10, resolve epic design.md Decision 2
   (end-state waiver) with the maintainer, update CLAUDE.md architecture
   section to the final map, then `task.py finish` flow for the epic.

## Validation Plan

```bash
.venv/bin/pytest tests/test_topology_registry.py tests/test_topology_saturation.py \
  tests/test_topology_llm.py tests/test_topology_multi_instance.py -n 0
.venv/bin/pytest tests/test_instances_per_component.py tests/test_scenario_deviation.py -n 0
.venv/bin/pytest tests/test_package_facades.py -n 0
.venv/bin/pytest                    # full suite = every locked hash
.venv/bin/pre-commit run --all-files
```

Run the full suite after step 3 AND step 4 — never batch the two clusters
into one unverified jump.

## Documentation And Spec Updates

- CLAUDE.md: module map, monkeypatch-note extension (new patch homes),
  extraction list.
- Epic design.md Status: step 10 done + Decision 2 resolution.

## Review Notes

- PR description: "verbatim move, zero behavior delta, hashes prove it" +
  the patch-target migration table + cap evidence. Reviewers should be
  pointed at the two RNG-critical constraints (draw order, draw count)
  and the passing hash suite as the proof.

## Follow-Ups

- Coupling-loop dedupe (`_compose_topology_coupled_specs` ↔
  `_compute_topology_arrays_per_instance`) — own hash-guarded PR.
- Epic archive + CLAUDE.md "decomposition complete" prose cleanup.

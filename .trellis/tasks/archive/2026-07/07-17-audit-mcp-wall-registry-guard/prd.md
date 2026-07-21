# Registry-couple the MCP ground-truth-wall leak sweeps

## Audit context

- **Source:** 2026-07-17 full repo audit @ b0df00b — report:
  `.trellis/audit/report-2026-07-17.md`; per-item evidence + fix sketches:
  `.trellis/audit/ledger.md`.
- **Ledger items:** A-021 (P1·M, Verified), A-001 (P2·M)

## Goal

The wall's leak sweeps hand-enumerate 3/15 (day-one) and 9/15 (eval) MCP tools with
no coupling to MCP_TOOLS — the eval sweep's docstring already falsely claims full
coverage, and the four profile-text renderers are protected only by an unpinned
no-slugs-in-text-today invariant. Structurally, every handler receives the full
rubric-bearing SimulationState.

## Scope (ledger items)

- A-021 — registry-driven sweep: per-tool minimal-args table whose keys must equal set(MCP_TOOLS) (loud failure on a new tool); run slug/description leak assertions over every tool's serialized response in eval and non-eval modes; fix the sweep docstring.
- A-001 — structural guard: narrowed investigation-view state for handlers (no anomaly_rows/active_scenarios/SCENARIOS) or a registry-level lint mirroring test_every_dispatched_route_is_classified.

## Acceptance criteria

- [x] Sweep keys == set(MCP_TOOLS) asserted; all 15 tools leak-checked in both modes.
- [x] A rubric read from a new tool handler fails a test or lint, not a review.
- [x] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).

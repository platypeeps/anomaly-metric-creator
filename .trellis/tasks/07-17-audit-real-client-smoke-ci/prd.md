# Run real kubectl/Helm smokes in CI and bump advertised K8s version

## Audit context

- **Source:** 2026-07-17 full repo audit @ b0df00b — report:
  `.trellis/audit/report-2026-07-17.md`; per-item evidence + fix sketches:
  `.trellis/audit/ledger.md`.
- **Ledger items:** A-022 (P2·M), A-067 (P3·M)

## Goal

The headline real-client compatibility guarantee is verified by no running test (the
env-gated smokes never run anywhere), and the facade advertises v1.29.4 — outside
supported skew for mid-2026 kubectl clients.

## Scope (ledger items)

- A-022 — full-lane CI step installing pinned kubectl + Helm 4 binaries and running the two smokes with AMC_RUN_REAL_CLIENT_SMOKE=1.
- A-067 — hoist the advertised-version literals into one constant, bump to a supported minor, re-run the smokes; record tested client versions in README.

## Acceptance criteria

- [ ] Both smokes run green in the full lane with pinned client versions.
- [ ] kubectl version skew warning gone against the bumped facade.
- [ ] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).

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

- [x] Both smokes run green in the full lane with pinned client versions.
- [x] kubectl version skew warning gone against the bumped facade.
- [x] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).

## Implementation evidence (2026-07-20)

- Official stable pins: kubectl v1.36.2 and Helm v4.2.0; the workflow verifies
  Linux-amd64 SHA-256 values `1e9045ec…c27d82` and `97dbeb97…f4096` before
  either binary enters `PATH`.
- The facade advertises Kubernetes v1.36.2 from one source constant across
  command output, `/version`, OpenAPI v2/v3 metadata, and node kubelet data.
- Exact-version macOS arm64 binaries were checksum-verified locally and both
  opt-in real-client smokes passed in 1.81 seconds with no kubectl skew warning.
- Focused validation: 64 CI-contract tests passed; all 91 ordinary server tests
  passed with only the two explicitly opt-in real-client tests skipped in the
  non-opt-in run; workflow YAML and both new shell blocks parse cleanly.
- GitHub Actions run `29801400915`, light job `88543062073`, installed and
  probed kubectl v1.36.2 and Helm v4.2.0, then the dedicated opt-in smoke step
  completed with `2 passed in 5.80s` and no skips (2026-07-21).

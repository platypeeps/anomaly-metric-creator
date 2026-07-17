# Symptom-level log artifact for eval mode — Design (SD Work Designs, 2026-07-17)

This design answers the PRD's four questions with a recommendation; the
maintainer decision (adopt / adjust / decline) is the gate at task start
and gets recorded back into the PRD.

## Overview

`metric_report.log` is a verbatim manifest rendering — rubric-bearing by
construction — so eval mode refuses every log surface. The goal is a log
modality that is rubric-clean **structurally** (derived from what the
run emitted, never from the manifest), so eval agents can do log-based
investigation inside the wall.

## Proposal — the four design questions

1. **Content.** One line per detected symptom episode:
   `<timestamp> WARN <component> <metric> deviating: value=<v>
   baseline=<b> (z=<z>)` plus an episode-end line (`recovered` with
   duration). Carries ONLY: timestamp, component, metric name, observed
   numbers. Never: scenario slugs, manifest descriptions, `event_id`s,
   `Cascading:` labels, severity words copied from the catalog — nothing
   sourced from `anomalies.csv`/`SCENARIOS` (structurally impossible:
   the writer never reads them).
2. **Derivation.** Pure function of the emitted per-component CSVs (the
   same ground the MCP analysis tools stand on): per-column robust
   baseline (median + MAD over the full column), symptom when
   |z| ≥ threshold for ≥ K consecutive rows (hysteresis so a spike
   yields one episode, not row-spam), episode closes when |z| drops
   below an exit threshold. Thresholds start at z≥3 enter / z<2 exit,
   K=3 — tuned at implementation against
   `tests/test_scenario_deviation.py`'s guarantee (every recorded
   anomaly deviates >1σ; primaries are typically ≥3σ) so headline
   scenarios produce episodes without natural-noise chatter.
3. **Surface.** A new `--emit` token `symptomlog` writing `symptom.log`
   at generation time (not serve-time synthesis): determinism and
   registry hygiene come for free, one derivation implementation serves
   CLI + serve + offline use. Serve integration: in eval mode,
   `get_logs`/`deduplicate_logs` and `/v1/logs/stream` serve
   `symptom.log` when present (falling back to today's refusal when
   absent); non-eval mode keeps `metric_report.log` on those surfaces
   unchanged. An agent only ever sees one mode, so the mode-dependent
   source is not a fingerprint an agent can compare.
4. **Determinism.** Byte-deterministic for a given seed (fixed formats,
   sorted iteration, no wall clock); locked SHA-256 golden hashes at 1d
   and 7d once the format settles, alongside the other artifacts.

**Registry hygiene (PRD requirement):** `symptom.log` joins
`_EMIT_ARTIFACT_FILES`, `_known_artifact_filenames()`, the atomic-writer
path, `_pre_clean_output_dir`, the end-of-run summary, and
`schema.json`'s `files` list — one pass, per the single-source rule.

**Wall proof (not assumption):** two tests — (a) the artifact itself
contains no `anomalies.csv` description substring and no scenario slug
(non-vacuous guards on both corpora); (b) the eval-mode MCP/tool
response sweep passes with the log tools now *answering* instead of
refusing.

## Boundaries And Non-Goals

- Not a general alerting engine: fixed line format, fixed thresholds
  (no config surface in v1).
- `metric_report.log` unchanged and still walled; the classification in
  CLAUDE.md gets the "symptom.log is the eval-servable log" update.
- DST-splice runs: the derivation reads rows as emitted (non-monotonic
  timestamps under DST stay as-emitted in the log; no re-sorting).

## Affected Files

`legacy.py` (emit token + writer call), a new `symptom_log.py` leaf
(writer; reads CSVs via `csv_layout` primitives), registries,
`server_mcp.py` + `server.py` (eval log-source dispatch),
`tests/test_symptom_log.py` (+ hash locks), eval sweep updates,
README (+ artifact table), CLAUDE.md.

## Risks And Edge Cases

- Threshold tuning vs `--metrics-per-component`/narrow `--components`:
  columns with near-zero MAD (constant columns) must not divide by zero
  — guard with a floor; constant columns emit nothing.
- File size at 7d full fan-out: episodes (not rows) keep it small;
  verify at implementation.
- The emit token must fail the same DST/multi-instance parser gates only
  if the derivation actually breaks there — multi-instance long-form
  CSVs work through the same `csv_layout` readers; assert N=3 works
  (no parser gate needed).

## Validation

- Golden hashes (1d/7d) once format-stable; leak tests (a)+(b); episode
  correctness against known scenario rows; full suite.

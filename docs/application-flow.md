# Application flow

End-to-end execution of `main(argv=None)` in `anomaly-metric-creator.py`.
The script has three top-level modes — `--combine-only` (rebuild the
unified CSV from existing per-component CSVs), `--validate-output PATH`
(load `PATH/schema.json` and run every validator against the artifacts
on disk), and the default generation pipeline.

```mermaid
flowchart TD
    start(["python anomaly-metric-creator.py …"]) --> parse["parse_args"]
    parse --> mode{"mode?"}

    mode -- "--combine-only" --> combineonly["combine_logs<br/>(reads existing per-component CSVs)<br/>→ combined_metrics_unified.csv"]
    combineonly --> finish([exit])

    mode -- "--validate-output PATH" --> validate["validate_output<br/>(load schema.json,<br/>run required/no-unknown/sorted/<br/>row-count/timestamp/cell/derivation checks)"]
    validate -- "no violations" --> finish
    validate -- "violations + --validate-warn" --> finish
    validate -- "violations (default)" --> failexit([exit 1])

    mode -- "default: generate" --> preclean["output_dir.mkdir<br/>+ _pre_clean_output_dir<br/>(stale artifacts removed per<br/>--emit-selection / --components / --combine)"]
    preclean --> ctx["RunContext(rng=np.random.RandomState(--seed))"]
    ctx --> resolve["_resolve_scenarios<br/>--scenarios → --exclude-scenarios<br/>→ --signal-level → --duration-days<br/>→ --components"]
    resolve --> apply["_apply_scenarios<br/>build component_anomalies +<br/>cascading_anomalies from registry"]
    apply --> specs["_resolve_effective_specs (--metrics-per-component)<br/>+ _filter_anomalies_for_emitted_metrics"]
    specs --> cap["_apply_signal_level_and_count<br/>(severity filter + --anomaly-count sampling)"]
    cap --> ts["_build_timestamp_arrays(total_seconds,<br/>--interval-seconds)"]

    ts --> torder{"--topology-mode?"}
    torder -- "independent (default)" --> indorder["walk components in<br/>COMPONENTS insertion order<br/>(no coupling — byte-identical<br/>to pre-VER-152 output)"]
    torder -- "realistic" --> realorder["_topology_generation_order<br/>(Kahn's algorithm, topological order)<br/>+ _compose_topology_coupled_specs<br/>+ _compose_topology_saturation_specs"]

    indorder --> gen["for each component:<br/>generate_component<br/>natural → anomaly overrides →<br/>derivations → capture →<br/>round → drop → write {component}.csv"]
    realorder --> gen

    gen --> anomcsv["sort filtered_anomalies +<br/>write anomalies.csv<br/>(when 'metrics' in --emit-selection)"]
    anomcsv --> reports["write_reporting_artifacts<br/>→ metric_report.log, metric_traces.jsonl<br/>(when 'logs'/'traces' in --emit-selection)"]
    reports --> gauges["write_gauges_csv<br/>→ gauges.csv<br/>(when 'gauges' in --emit-selection)"]
    gauges --> schema["write_schema_json<br/>→ schema.json<br/>(when 'schema' in --emit-selection)"]
    schema --> otel["stream_otel_signals +<br/>stream_otel_gauges<br/>(when --otel-enabled)"]
    otel --> combine["combine_logs<br/>→ combined_metrics_unified.csv<br/>(when --combine)"]
    combine --> summary["print 'Done -' summary line<br/>(only names artifacts actually written)"]
    summary --> finish
```

## Notes

- `--emit-selection` gates the four downstream writers
  (`anomalies.csv` is part of `metrics`; `metric_report.log` is
  `logs`; `metric_traces.jsonl` is `traces`; `gauges.csv` is `gauges`;
  `schema.json` is `schema`). Skipped writers are no-ops on this
  run, and `_pre_clean_output_dir` removes any matching artifact left
  over from a prior run.
- `--validate-output` is mutually exclusive with `--combine` /
  `--combine-only`; it short-circuits before any generation.
- Topology coupling and saturation (the right branch of the
  `--topology-mode` decision) re-shape downstream `MetricSpec`
  baselines from upstream load columns captured during generation.
  See [Topology graph (v1)](./topology.md) for the edge set and
  saturation parameters.

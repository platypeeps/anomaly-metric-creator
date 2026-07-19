"""CLI parser and subcommand dispatch helpers for anomaly-metric-creator.

Extracted from ``legacy.py`` during the monolith decomposition. ``legacy.py``
configures live registry access and re-imports these names so the historic
``legacy.<name>`` surface remains stable.
"""

from __future__ import annotations

import argparse
import datetime
import math
import os
import sys
from pathlib import Path
from typing import Any, Callable

from .cli_subcommands import (
    _SUBCOMMANDS as _SUBCOMMANDS,
    _configure_cli_subcommand_runtime,
    _main_combine_subcommand as _main_combine_subcommand,
    _main_serve_subcommand as _main_serve_subcommand,
    _main_trace_bundle_subcommand as _main_trace_bundle_subcommand,
    _main_validate_subcommand as _main_validate_subcommand,
)


_DEFAULT_RUNTIME_KEY = "__default__"
_cli_runtimes: dict[str, dict[str, Any]] = {}

COMPONENTS: dict[str, Any] = {}
SCENARIOS: dict[str, Any] = {}
DEFAULT_METRICS_PER_COMPONENT: dict[str, int] = {}


def _configure_cli_runtime(
    *,
    get_components: Callable[[], dict[str, Any]],
    get_scenarios: Callable[[], dict[str, Any]],
    get_default_metrics_per_component: Callable[[], dict[str, int]],
    get_legacy_module: Callable[[], Any],
    constants: dict[str, Any],
    runtime_key: str = _DEFAULT_RUNTIME_KEY,
) -> None:
    """Wire parser dependencies from ``legacy.py`` without importing it."""
    _cli_runtimes[runtime_key] = {
        "get_components": get_components,
        "get_scenarios": get_scenarios,
        "get_default_metrics_per_component": get_default_metrics_per_component,
        "constants": constants,
    }
    _configure_cli_subcommand_runtime(
        runtime_key=runtime_key,
        get_components=get_components,
        parse_components_value=_parse_components_value,
        get_legacy_module=get_legacy_module,
    )
    _refresh_cli_runtime(runtime_key)


def _refresh_cli_runtime(runtime_key: str = _DEFAULT_RUNTIME_KEY) -> None:
    """Refresh live registries that tests and server flows monkeypatch."""
    global COMPONENTS, SCENARIOS, DEFAULT_METRICS_PER_COMPONENT
    runtime = _cli_runtimes.get(runtime_key)
    if runtime is None:
        raise RuntimeError("cli_args runtime is not configured")
    globals().update(runtime["constants"])
    COMPONENTS = runtime["get_components"]()
    SCENARIOS = runtime["get_scenarios"]()
    DEFAULT_METRICS_PER_COMPONENT = runtime["get_default_metrics_per_component"]()


def _parse_start_time_arg(value: str) -> datetime.datetime:
    """Parse a CLI start timestamp and normalize it to naive UTC."""
    text = value.strip()
    if not text:
        raise argparse.ArgumentTypeError("must be non-empty")
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "must be ISO 8601, e.g. 2026-06-24T12:34:56Z"
        ) from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    if parsed.microsecond:
        raise argparse.ArgumentTypeError(
            "must be a whole-second ISO 8601 timestamp; "
            "sub-second start times cannot be represented exactly in every artifact"
        )
    return parsed

# Flags hidden from the default ``-h`` (shown by ``--help-all``): the
# advanced / research knobs that the common use cases never touch.
# Keyed by argparse ``dest``. (The 16 deprecated alias flags that used
# to live here were removed at the post-phase-9 CLI flag day; the
# historic dests they wrote — ``emit_selection``, ``combine``, the
# OTEL toggle/endpoint sextet — survive as the internal namespace
# populated by ``p.set_defaults`` + ``_reconcile_cli_surface``.)
_ADVANCED_DESTS: frozenset[str] = frozenset({
    # research / power knobs
    "anomaly_count", "allow_huge_output", "inject_dst_artifact_day",
    # OTEL transport tuning (set-once-per-environment; env vars exist
    # for protocol and auth scheme already)
    "otel_gauge_batch_seconds", "otel_gauge_metric_prefix",
    "otel_stream_timeout_seconds", "otel_stream_max_events",
    "otel_stream_auth_scheme", "otel_activity_log", "otel_verbose",
})


def _flag_in_argv(argv: list[str], flag: str) -> bool:
    """True when ``flag`` was explicitly passed (bare or ``=value`` form)."""
    return any(tok == flag or tok.startswith(flag + "=") for tok in argv)


def _parse_components_value(
    error, raw: str, *, runtime_key: str = _DEFAULT_RUNTIME_KEY
) -> set[str]:
    """Parse and validate a ``--components`` CSV value ('all' or names).

    ``error`` is an argparse ``parser.error``-style callable so both the
    flat parser and the ``combine`` subcommand share one validation path.
    """
    _refresh_cli_runtime(runtime_key)
    raw_components = [item.strip().lower() for item in raw.split(",") if item.strip()]
    if not raw_components:
        error("--components must contain at least one component name (or 'all')")
    selected_components = set(raw_components)
    invalid_components = sorted(selected_components - set(COMPONENTS.keys()))
    if invalid_components and "all" in invalid_components:
        invalid_components.remove("all")
    if invalid_components:
        error("--components contains invalid value(s): "
              f"{', '.join(invalid_components)}. "
              f"Allowed: {', '.join(sorted(COMPONENTS.keys()))} or 'all'")
    if "all" in selected_components:
        if len(selected_components) > 1:
            error("--components 'all' cannot be combined with explicit "
                  "component names")
        return set(COMPONENTS.keys())
    return selected_components


def _reconcile_cli_surface(p, args):
    """Map the canonical CLI surface onto the internal argument namespace.

    Everything downstream of ``parse_args`` consumes the historic
    names: ``emit_selection`` (written here in its raw comma-separated
    *string* form — the gates that run after this function re-parse it
    and replace it with the final ``set``), the ``combine`` boolean,
    the ``otel_enabled`` /
    ``otel_emit_gauges`` / ``otel_gauges_only`` toggles, and the
    per-signal endpoint/token sextet. Since the post-phase-9 CLI flag
    day removed the deprecated alias flags, the canonical flags
    (``--emit``, ``--otel-send``, ``--otel-endpoint``,
    ``--otel-auth-token``) are the only writers besides the
    ``p.set_defaults`` baselines and the ``MEZMO_OTEL_*`` env vars;
    this function translates them — immediately after parsing and
    before any validation gate — so every gate consumes one namespace.
    """
    # ------------------------------------------------------------------
    # --emit -> emit_selection (+ combine via the 'combined' token).
    # ------------------------------------------------------------------
    if args.emit is not None:
        tokens = {t.strip().lower() for t in args.emit.split(",") if t.strip()}
        allowed = {"metrics", "logs", "traces", "gauges", "schema", "combined"}
        invalid = sorted(tokens - allowed)
        if invalid:
            p.error("--emit contains invalid value(s): "
                    f"{', '.join(invalid)}. "
                    "Allowed: metrics,logs,traces,gauges,schema,combined")
        if not tokens:
            p.error("--emit must contain at least one of "
                    "metrics,logs,traces,gauges,schema,combined")
        if "combined" in tokens:
            args.combine = True
            tokens.discard("combined")
            if not tokens:
                p.error(
                    "--emit 'combined' joins the per-component CSVs, so it "
                    "requires the generated artifacts alongside it — "
                    "include 'metrics' (e.g. --emit metrics,combined)"
                )
        # Early twin of the post-reconcile gate below, so the error
        # points at the exact --emit value the user typed. (gauges.csv
        # derives from the per-component CSVs that only 'metrics'
        # writes.)
        if "gauges" in tokens and "metrics" not in tokens:
            p.error("--emit 'gauges' requires 'metrics' in the selection "
                    "(gauges.csv derives from the per-component CSVs)")
        args.emit_selection = ",".join(sorted(tokens))

    # ------------------------------------------------------------------
    # --otel-send -> otel_enabled / otel_emit_gauges / otel_gauges_only.
    # ------------------------------------------------------------------
    send_tokens = None
    if args.otel_send is not None:
        send_tokens = {
            t.strip().lower() for t in args.otel_send.split(",") if t.strip()
        }
        allowed = {"logs", "metrics", "traces", "gauges", "all", "none"}
        invalid = sorted(send_tokens - allowed)
        if invalid:
            p.error("--otel-send contains invalid value(s): "
                    f"{', '.join(invalid)}. "
                    "Allowed: logs, metrics, traces, gauges, all, none")
        if not send_tokens:
            p.error("--otel-send must contain at least one of "
                    "logs,metrics,traces,gauges (or 'all' / 'none')")
        if "none" in send_tokens:
            if send_tokens != {"none"}:
                p.error("--otel-send 'none' cannot be combined with other "
                        "signals")
            # Explicit off, overriding any env-var endpoint defaults.
            args.otel_enabled = False
            args.otel_emit_gauges = False
            args.otel_gauges_only = False
            # Clear env-provided per-signal endpoints/tokens: 'none' must
            # be truly off — leaving them would route the values into the
            # endpoint-shape validation below, so a malformed shell
            # export could fail a run the user explicitly disabled.
            for _sig in ("logs", "metrics", "traces"):
                setattr(args, f"otel_{_sig}_endpoint", None)
                setattr(args, f"otel_{_sig}_auth_token", None)
            send_tokens = set()
        else:
            if "all" in send_tokens:
                send_tokens = {"logs", "metrics", "traces", "gauges"}
            args.otel_enabled = True
            args.otel_emit_gauges = "gauges" in send_tokens
            args.otel_gauges_only = send_tokens == {"gauges"}
            # The anomaly-signal selection, minus the gauge stream:
            # main() filters stream_otel_signals' endpoint dict by this
            # so that e.g. --otel-send logs,gauges derives the metrics
            # ENDPOINT (the gauge stream posts there) without leaking
            # the anomaly-count metrics SIGNAL.
            args.otel_signal_selection = frozenset(send_tokens - {"gauges"})

    # ------------------------------------------------------------------
    # --otel-endpoint / --otel-auth-token -> the per-signal sextet.
    # ------------------------------------------------------------------
    if args.otel_endpoint is not None or args.otel_auth_token is not None:
        if send_tokens is None:
            flag = ("--otel-endpoint" if args.otel_endpoint is not None
                    else "--otel-auth-token")
            p.error(f"{flag} requires --otel-send")
    base = None
    if args.otel_endpoint is not None:
        if not args.otel_endpoint.startswith(("http://", "https://")):
            p.error("--otel-endpoint must start with http:// or https://")
        base = args.otel_endpoint.rstrip("/")
    wanted = None
    if send_tokens:
        wanted = {s for s in ("logs", "metrics", "traces")
                  if s in send_tokens}
        if "gauges" in send_tokens:
            # The gauge stream posts to the metrics endpoint.
            wanted.add("metrics")
    if wanted is not None:
        for sig in ("logs", "metrics", "traces"):
            if sig in wanted:
                # An explicitly typed --otel-endpoint base beats the
                # MEZMO_OTEL_*_ENDPOINT env-var default (a stale shell
                # export must not silently hijack typed input); the env
                # var supplies the per-signal value when no base is
                # given. Same ladder for the token.
                if base is not None:
                    setattr(args, f"otel_{sig}_endpoint", f"{base}/v1/{sig}")
                if args.otel_auth_token is not None:
                    setattr(args, f"otel_{sig}_auth_token",
                            args.otel_auth_token)
            else:
                # --otel-send is authoritative for signal selection:
                # env-var endpoint AND token defaults for unselected
                # signals are cleared so a configured-but-unselected
                # signal cannot leak into the stream and a dangling
                # credential is not carried in the namespace (matching
                # the stricter clearing the 'none' branch does).
                setattr(args, f"otel_{sig}_endpoint", None)
                setattr(args, f"otel_{sig}_auth_token", None)
    if send_tokens:
        if not any([args.otel_logs_endpoint, args.otel_metrics_endpoint,
                    args.otel_traces_endpoint]):
            p.error("--otel-send requires --otel-endpoint (or a per-signal "
                    "endpoint via MEZMO_OTEL_*_ENDPOINT)")


def parse_args(argv=None, *, runtime_key: str = _DEFAULT_RUNTIME_KEY):
    _refresh_cli_runtime(runtime_key)
    raw_argv = list(sys.argv[1:]) if argv is None else list(argv)
    # ``--help-all`` rebuilds the help view with the advanced flags
    # un-hidden, then renders help. Handled before argparse so the
    # brief parser never needs to know the flag exists as an action.
    show_all = _flag_in_argv(raw_argv, "--help-all")
    if show_all:
        raw_argv = ["--help"]
    argv = raw_argv

    p = argparse.ArgumentParser(
        description="Generate synthetic IoT metric logs with anomalies.",
        # Abbreviated flags (--emit-sel, --otel-en, ...) would bypass the
        # canonical/alias mixing checks and the deprecation notices, which
        # scan raw argv for exact spellings. Exact flags only.
        allow_abbrev=False,
        epilog=(
            "Subcommands: 'generate' (the default when no subcommand is "
            "given), 'combine DIR' (join existing per-component CSVs into "
            "combined_metrics_unified.csv), 'validate DIR [--warn]' "
            "(check artifacts against DIR/schema.json), 'serve' "
            "(start the Kubernetes/Helm simulator server), and "
            "'trace-bundle' (inspect exported command traces offline). "
            "This help shows the common surface; run with --help-all to "
            "also list the advanced knobs."
        ),
    )
    g_common = p.add_argument_group("common")
    g_anom = p.add_argument_group("anomaly selection")
    g_shape = p.add_argument_group("dataset shape")
    g_art = p.add_argument_group("artifacts")
    g_otel = p.add_argument_group("OTEL streaming")
    g_adv = p.add_argument_group(
        "advanced",
        "Hidden from -h; shown here via --help-all.",
    )

    g_art.add_argument(
        "--emit",
        type=str,
        default=None,
        metavar="ARTIFACTS",
        help="Comma-separated artifact selection: metrics, logs, traces, "
             "gauges, schema, combined (default: metrics,logs,traces). "
             "'gauges' writes a long-form gauges.csv and requires "
             "'metrics'; 'schema' writes a declarative schema.json "
             "consumed by the validate subcommand; 'combined' "
             "additionally joins the per-component CSVs into "
             "combined_metrics_unified.csv after generation (requires "
             "'metrics').",
    )
    g_otel.add_argument(
        "--otel-send",
        type=str,
        default=None,
        metavar="SIGNALS",
        help="Enable OTLP/HTTP streaming and select what to send: a "
             "comma-separated subset of logs, metrics, traces, gauges — "
             "or 'all', or 'none' (explicitly off, overriding env "
             "defaults). logs/metrics/traces replay anomaly events to the "
             "matching signal endpoint; 'gauges' streams per-row metric "
             "values as Gauge data points to the metrics endpoint "
             "(requires the 'metrics' artifact). '--otel-send gauges' "
             "alone skips the anomaly signal stream entirely.",
    )
    g_otel.add_argument(
        "--otel-endpoint",
        type=str,
        default=None,
        metavar="BASE_URL",
        help="OTLP/HTTP base endpoint (e.g. http://localhost:4318). "
             "Per-signal URLs are derived as BASE/v1/logs, BASE/v1/metrics, "
             "BASE/v1/traces for the signals selected by --otel-send. "
             "This derivation beats the MEZMO_OTEL_*_ENDPOINT env vars "
             "(they supply per-signal defaults when no base is given).",
    )
    g_otel.add_argument(
        "--otel-auth-token",
        type=str,
        default=None,
        metavar="TOKEN",
        help="Auth token applied to every selected signal endpoint "
             "(scheme via --otel-stream-auth-scheme, default Bearer). "
             "This token beats the MEZMO_OTEL_*_AUTH_TOKEN env vars "
             "(they supply per-signal defaults when this flag is not "
             "given).",
    )
    g_common.add_argument("--duration-days", type=float, default=DEFAULT_DURATION_DAYS,
                   help=f"Number of days of metrics to generate "
                        f"(default: {DEFAULT_DURATION_DAYS!r}, "
                        f"which yields {DEFAULT_ROW_COUNT:,} rows at the "
                        f"default {DEFAULT_INTERVAL_SECONDS:g}s interval). "
                        "Each scenario's ``days_required`` is the minimum value at which "
                        "any of its specs become in range; the full multi-day catalog "
                        f"manifests at {max(s.days_required for s in SCENARIOS.values())}+.")
    g_common.add_argument(
        "--start-time",
        type=_parse_start_time_arg,
        default=START,
        metavar="TIMESTAMP",
        help="UTC whole-second timestamp for the first generated row. Accepts ISO 8601 "
             "values such as 2026-06-24T12:34:56Z or 2026-06-24 12:34:56. "
             f"Default: {START.isoformat()}.",
    )
    g_common.add_argument("--seed", type=int, default=DEFAULT_SEED,
                   help=f"RNG seed for deterministic output (default: {DEFAULT_SEED}).")
    g_common.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                   help=f"Directory to write CSV files into (default: {DEFAULT_OUTPUT_DIR}).")
    g_shape.add_argument("--drop-rate", type=float, default=DEFAULT_DROP_RATE,
                   help=f"Per-row probability of dropping the row entirely from the per-component CSV "
                        f"(no row is emitted for that timestamp). Simulated packet loss "
                        f"(default: {DEFAULT_DROP_RATE}).")
    g_shape.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS,
                   help=f"Seconds between consecutive emitted rows "
                        f"(default: {DEFAULT_INTERVAL_SECONDS}). Controls sampling "
                        f"density; timeline coverage stays --duration-days * 86400 "
                        f"seconds. Row count per component is floor(total_seconds / interval). "
                        f"Must be >= 0.001 (millisecond precision floor). "
                        f"Values >= 1.0 emit second-precision timestamps "
                        f"(YYYY-MM-DD HH:MM:SS); values < 1.0 emit millisecond-precision "
                        f"timestamps (YYYY-MM-DD HH:MM:SS.SSS) to keep adjacent rows unique.")
    # Internal namespace baselines for the dests the canonical flags
    # translate onto via _reconcile_cli_surface. The flags that used to
    # write these directly (--combine, --emit-selection) were removed at
    # the post-phase-9 CLI flag day; the dests survive because every
    # downstream gate and main() consumer reads them.
    p.set_defaults(combine=False)
    p.set_defaults(emit_selection="metrics,logs,traces")
    g_shape.add_argument(
        "--components",
        type=str,
        default="all",
        help="Comma-separated list of component names to emit (CSV files, "
             "anomalies.csv, reporting artifacts, and OTel streaming). Use "
             "'all' (default) for every component. Allowed names: "
             f"{', '.join(sorted(COMPONENTS.keys()))}.",
    )
    g_anom.add_argument(
        "--scenarios",
        type=str,
        default="all",
        help="Comma-separated list of named scenario slugs to include. Use "
             "'all' (default) to include every scenario in the registry. "
             "Case-insensitive. Known slugs: "
             f"{', '.join(sorted(SCENARIOS.keys()))}.",
    )
    g_anom.add_argument(
        "--exclude-scenarios",
        type=str,
        default="",
        help="Comma-separated list of named scenario slugs to exclude from "
             "the resolved set (applied after --scenarios). Case-insensitive. "
             "Defaults to empty (no exclusion).",
    )
    g_anom.add_argument(
        "--signal-level",
        type=str,
        default=DEFAULT_SIGNAL_LEVEL,
        help="Anomaly intensity level: low, medium (default), or high. "
             "low keeps only benign baseline shifts; medium keeps the standard "
             "catalog (today's behavior); high additionally activates the "
             "high-pressure cross-component scenarios.",
    )
    g_adv.add_argument(
        "--anomaly-count",
        type=int,
        default=None,
        help="Optional cap on the total number of anomalies (including "
             "cascades) injected across the whole dataset. Sampling is "
             "deterministic for a given --seed. Defaults to unlimited.",
    )
    g_shape.add_argument(
        "--metrics-per-component",
        type=int,
        default=None,
        help=f"Optional cap on emitted metrics per component "
             f"(1..{MAX_METRICS_PER_COMPONENT}). When unset, every component "
             f"emits its historic default set. When set to N, each component "
             f"emits the first N metrics from its priority-ordered catalog "
             f"(highest-value first). Anomalies targeting metrics outside "
             f"the trimmed set are filtered out.",
    )
    g_adv.add_argument(
        "--allow-huge-output",
        action="store_true",
        default=False,
        help=f"Bypass the preflight cell-count cap "
             f"({PREFLIGHT_CELL_CAP:,} metric cells across all "
             f"components, timestamps, and instances). Without this flag, "
             f"parse_args rejects combinations of --interval-seconds, "
             f"--duration-days, --metrics-per-component, --components, and "
             f"--instances-per-component that would emit more cells than "
             f"the cap. Pass this flag when the size is intentional.",
    )
    # OTEL streaming state: --otel-send is the only writer (the five
    # toggle aliases were removed at the CLI flag day, and the
    # MEZMO_OTEL_EMIT_GAUGES env default went with them — once the
    # selection became the only enable path it was authoritative over
    # the env var, which therefore could never take effect).
    p.set_defaults(otel_enabled=False)
    p.set_defaults(otel_emit_gauges=False)
    p.set_defaults(otel_gauges_only=False)
    g_adv.add_argument(
        "--otel-gauge-batch-seconds",
        type=int,
        default=60,
        help="Number of consecutive timestamp ticks (in seconds of timeline coverage, "
             "not wall-clock) coalesced into one OTLP request when the gauge stream "
             "is selected (--otel-send with 'gauges'). Default: 60. Larger batches mean fewer HTTP requests but bigger "
             "bodies; tune for your OTLP collector body limit.",
    )
    g_adv.add_argument(
        "--otel-gauge-metric-prefix",
        type=str,
        default="",
        help="Optional namespace prefix prepended to the OTLP metric name for each "
             "gauge data point (e.g. 'amc.' produces 'amc.cpu_util_pct'). Default: "
             "empty (use the raw MetricSpec.name).",
    )
    # Per-signal endpoint/token internal namespace. The six per-signal
    # flags were removed at the CLI flag day; the MEZMO_OTEL_* env vars
    # remain the per-signal override mechanism (an explicitly typed
    # --otel-endpoint base beats them for the selected signals — see
    # _reconcile_cli_surface).
    p.set_defaults(
        otel_logs_endpoint=os.environ.get("MEZMO_OTEL_LOGS_ENDPOINT"),
        otel_logs_auth_token=os.environ.get("MEZMO_OTEL_LOGS_AUTH_TOKEN"),
        otel_metrics_endpoint=os.environ.get("MEZMO_OTEL_METRICS_ENDPOINT"),
        otel_metrics_auth_token=os.environ.get("MEZMO_OTEL_METRICS_AUTH_TOKEN"),
        otel_traces_endpoint=os.environ.get("MEZMO_OTEL_TRACES_ENDPOINT"),
        otel_traces_auth_token=os.environ.get("MEZMO_OTEL_TRACES_AUTH_TOKEN"),
    )
    g_otel.add_argument(
        "--otel-stream-speedup",
        type=float,
        default=3600.0,
        help="Timeline replay speed multiplier for OTEL streaming (default: 3600). "
             "1.0 = real-time, 3600 = one hour of anomaly spacing per second.",
    )
    g_adv.add_argument(
        "--otel-stream-timeout-seconds",
        type=float,
        default=5.0,
        help="HTTP timeout per OTEL streamed event in seconds (default: 5).",
    )
    g_adv.add_argument(
        "--otel-stream-max-events",
        type=int,
        default=None,
        help="Optional cap on streamed HTTP attempt count (default: all). For the "
             "anomaly-counter stream this caps the number of anomaly events sent. "
             "For the gauge stream (--otel-send with 'gauges') it caps the number of "
             "OTLP request *attempts* (not data points and not successes) — a broken "
             "endpoint that 500s every request still trips the cap at N. Both streams "
             "honor the same flag independently in one run.",
    )
    g_adv.add_argument(
        "--otel-stream-auth-scheme",
        type=str,
        default=os.environ.get("MEZMO_OTEL_STREAM_AUTH_SCHEME", DEFAULT_OTEL_STREAM_AUTH_SCHEME),
        help="Auth scheme prefix for OTEL auth token (default: Bearer). "
             "Env override: MEZMO_OTEL_STREAM_AUTH_SCHEME.",
    )
    g_otel.add_argument(
        "--otel-stream-protocol",
        type=str,
        default=os.environ.get("MEZMO_OTEL_STREAM_PROTOCOL", "protobuf"),
        help="OTLP payload mode for stream endpoint: json or protobuf (default: protobuf). "
             "Env override: MEZMO_OTEL_STREAM_PROTOCOL.",
    )
    g_adv.add_argument(
        "--otel-activity-log",
        type=Path,
        default=Path("otel-activity.log"),
        help="Path to the OTEL streaming activity log file. Records one line per "
             "send attempt, retry, and failure when OTEL streaming is on. The file "
             "is only created when streaming actually runs. "
             "Default: ./otel-activity.log in the current directory.",
    )
    g_adv.add_argument(
        "--otel-verbose",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Include raw OTLP payload bodies, request headers, response status, "
             "and exception types in the activity log for each SEND/OK/RETRY/FAIL "
             "record. Authorization header values are masked. Default: off.",
    )
    g_adv.add_argument("--inject-dst-artifact-day", type=int, default=0,
                   help="Inject a fall-DST artifact (duplicated 02:00–02:59 wall-clock hour) "
                        "on the given 1-based day of the run. 0 (default) disables. Generator "
                        "quirk, not an anomaly spec — does not appear in anomalies.csv. The "
                        "affected CSVs end up with 3,600/interval extra rows for that day.")
    instance_source = g_shape.add_mutually_exclusive_group()
    instance_source.add_argument(
        "--instances-per-component",
        type=int,
        default=1,
        metavar="N",
        help=f"Fan each component out to N identical instances (default 1). "
             f"N=1 emits today's byte-identical output with no dimension columns. "
             f"N>1 prepends id, host, pod, az, region, tenant columns to every "
             f"per-component CSV and emits N×rows_per_component rows. "
             f"Accepted range: [1, {MAX_INSTANCES_PER_COMPONENT}]. "
             f"Mutually exclusive with --instance-config.",
    )
    instance_source.add_argument(
        "--instance-config",
        type=Path,
        default=None,
        metavar="PATH",
        help="YAML (.yaml/.yml) or JSON (.json) file declaring a per-component "
             "instance topology for repeatable non-uniform fan-outs. "
             "Top-level key 'components' maps component names to lists of "
             "Instance field dicts (id, host, pod, az, region, tenant). "
             "Components not listed in the file fall back to the module-level "
             "INSTANCES registry (today: a single anonymous Instance() per "
             "component). Mutually exclusive with --instances-per-component.",
    )
    # Brief-help mode hides the advanced knobs; --help-all shows
    # everything. Walks the parser's actions once after construction so
    # each flag is defined exactly once above.
    if not show_all:
        for action in p._actions:
            if action.dest in _ADVANCED_DESTS:
                action.help = argparse.SUPPRESS

    args = p.parse_args(argv)

    # Default: no signal filtering (every configured endpoint streams;
    # only reachable for programmatic namespaces — flag-level streaming
    # always routes through --otel-send). _reconcile_cli_surface
    # overrides this with the --otel-send selection when given.
    args.otel_signal_selection = None

    _reconcile_cli_surface(p, args)

    if not math.isfinite(args.duration_days):
        p.error("--duration-days must be a finite number")
    if args.duration_days < 1:
        p.error("--duration-days must be >= 1")
    if not 0.0 <= args.drop_rate <= 1.0:
        p.error("--drop-rate must be between 0 and 1")
    # np.random.RandomState accepts seeds in [0, 2**32). An out-of-range
    # value (e.g. --seed -1) used to crash later in main() with a raw
    # numpy ValueError traceback instead of a clean usage error.
    if not 0 <= args.seed < 2**32:
        p.error("--seed must be in [0, 2**32) (numpy RandomState range)")
    # NaN and infinity slip past plain <= 0 / < 0.001 comparisons:
    # NaN compares false to everything, and inf is greater than every finite
    # bound. NaN later crashes when row counts are cast to int; inf silently
    # generates zero rows. Reject both up-front.
    if not math.isfinite(args.interval_seconds):
        p.error("--interval-seconds must be a finite number")
    if args.interval_seconds <= 0:
        p.error("--interval-seconds must be > 0")
    # Sub-second intervals emit millisecond-precision timestamps. Anything
    # finer than 1ms would collide on the rendered string and silently drop
    # rows in the combine step (the original failure mode).
    if args.interval_seconds < 0.001:
        p.error("--interval-seconds must be >= 0.001 (ms-precision floor)")
    if args.inject_dst_artifact_day < 0:
        p.error("--inject-dst-artifact-day must be >= 0 (0 disables)")
    if args.inject_dst_artifact_day > args.duration_days:
        p.error(f"--inject-dst-artifact-day {args.inject_dst_artifact_day} "
                f"is outside the configured --duration-days {args.duration_days}")
    # Re-parse the reconciled emit_selection string into the final set.
    # The token vocabulary was already gated when --emit was given; this
    # pass re-checks it so a programmatic namespace (or a future default
    # change) cannot smuggle an unknown token past the gates.
    selected = {item.strip().lower() for item in args.emit_selection.split(",") if item.strip()}
    allowed = {"metrics", "logs", "traces", "gauges", "schema"}
    invalid = sorted(selected - allowed)
    if invalid:
        p.error("--emit contains invalid value(s): "
                f"{', '.join(invalid)}. "
                "Allowed: metrics,logs,traces,gauges,schema "
                "(plus 'combined', consumed at --emit parse time)")
    if not selected:
        p.error("--emit must contain at least one of "
                "metrics,logs,traces,gauges,schema")
    if args.combine and "metrics" not in selected:
        p.error("--emit 'combined' requires 'metrics' in the selection "
                "(e.g. --emit metrics,combined)")
    # ``gauges`` is derived from the per-component CSVs written under
    # ``metrics`` (same input as the OTEL gauge stream). Without ``metrics``,
    # the per-component CSVs are not written this run, so we have nothing to
    # derive ``gauges.csv`` from. Reject up-front with a clear message.
    if "gauges" in selected and "metrics" not in selected:
        p.error("--emit 'gauges' requires 'metrics' in the selection")
    if args.otel_gauges_only:
        args.otel_emit_gauges = True
    if args.otel_emit_gauges:
        if args.otel_send is not None:
            gauge_flag = ("--otel-send gauges" if args.otel_gauges_only
                          else "--otel-send with 'gauges'")
        else:
            # No flag or env var writes otel_emit_gauges anymore; only a
            # programmatic namespace can reach this branch.
            gauge_flag = "the gauge stream (otel_emit_gauges)"
        if not args.otel_enabled:
            p.error(f"{gauge_flag} requires --otel-send to enable streaming")
        if not args.otel_metrics_endpoint:
            p.error(f"{gauge_flag} requires a metrics endpoint "
                    "(via --otel-endpoint or MEZMO_OTEL_METRICS_ENDPOINT)")
        if "metrics" not in selected:
            p.error(f"{gauge_flag} requires --emit to include 'metrics'")
    # Both gauge paths (``--otel-send ...,gauges`` and ``--emit ...,gauges``)
    # feed per-component CSVs into ``heapq.merge``, which requires each input
    # iterator to be sorted by the timestamp key.
    # ``--inject-dst-artifact-day`` deliberately duplicates the 02:00–02:59
    # wall-clock hour inside each CSV (see ``_splice_dst_artifact``),
    # producing non-monotonic timestamps that silently break batching, OTLP
    # payloads, and the merged ``gauges.csv`` ordering. Reject the
    # combination at parse time for both paths — real OTLP consumers wouldn't
    # tolerate the artifact either, so there's no realistic user for it.
    if args.inject_dst_artifact_day > 0 and (
        args.otel_emit_gauges or "gauges" in selected
    ):
        flags = []
        if args.otel_gauges_only:
            flags.append("--otel-send gauges")
        elif args.otel_emit_gauges:
            flags.append("--otel-send with 'gauges'")
        if "gauges" in selected:
            flags.append("--emit 'gauges'")
        p.error(
            f"{' / '.join(flags)} is incompatible with --inject-dst-artifact-day "
            "(the DST artifact produces non-monotonic CSV timestamps that break "
            "the heapq.merge over per-component CSVs); pass "
            "--inject-dst-artifact-day 0 or drop the gauge emission flag"
        )
    # Validate ``--instances-per-component`` range *before* any N>1 gating
    # so an out-of-range value (e.g. 0 or 999) surfaces the range error
    # rather than masquerading as an incompatibility error when the user
    # also passed --emit combined or another gated flag.
    if (
        args.instances_per_component < 1
        or args.instances_per_component > MAX_INSTANCES_PER_COMPONENT
    ):
        p.error(
            f"--instances-per-component must be in [1, "
            f"{MAX_INSTANCES_PER_COMPONENT}] (1 = default dimensionless "
            f"output; >1 fans out with pod/az/etc. columns)"
        )
    # Validate ``--instance-config`` file path early (before any multi-instance
    # gating) so a missing file or wrong suffix surfaces a clean error rather
    # than as a generic incompatibility.
    if args.instance_config is not None:
        # ``is_file()`` rejects missing paths *and* directories /
        # broken-symlink-style entries in one shot. ``exists()`` would let
        # a directory through and then ``_load_instance_config`` would
        # surface it as an OSError mid-run.
        if not args.instance_config.is_file():
            if args.instance_config.exists():
                p.error(
                    f"--instance-config path is not a regular file: "
                    f"{args.instance_config}"
                )
            p.error(f"--instance-config path does not exist: {args.instance_config}")
        if args.instance_config.suffix.lower() not in {".yaml", ".yml", ".json"}:
            p.error(
                f"--instance-config must be a .yaml, .yml, or .json file; "
                f"got {args.instance_config.suffix!r}"
            )
    # Phase 3: --instance-config triggers the same multi-instance
    # code path as --instances-per-component > 1 (dimension columns,
    # N×rows per component, partial-aware downstream emitters). Both flags
    # must be gated identically against incompatible downstream flags so
    # the user gets one error message, not two divergent ones.
    _multi_instance = (
        args.instances_per_component > 1 or args.instance_config is not None
    )
    _multi_instance_flag = (
        "--instance-config" if args.instance_config is not None
        else "--instances-per-component > 1"
    )
    if _multi_instance and args.inject_dst_artifact_day > 0:
        p.error(
            f"{_multi_instance_flag} is incompatible with --inject-dst-artifact-day "
            "by design (per-instance DST splicing produces non-monotonic "
            "timestamps inside each long-form row block, which downstream "
            "long-form merges in gauges.csv / combined_metrics_unified.csv "
            "cannot resolve); pass --inject-dst-artifact-day 0 or use the "
            "default single-instance mode"
        )
    # Multi-instance dimension-awareness status by emitter (post-Phase-8):
    #
    # - File-form long-form writers (``gauges.csv`` /
    #   ``combined_metrics_unified.csv``): wired in Phase 5.
    #   Header inspection dispatches to a 10-column layout when the
    #   per-component CSVs carry the ``id, host, pod, az, region,
    #   tenant`` prefix; the historic 4-column / wide layouts stay
    #   byte-identical when the prefix is absent.
    # - OTEL streaming (``--otel-send``):
    #   wired in Phase 6. ``stream_otel_gauges`` and
    #   ``stream_otel_signals`` lift the dimension columns off each
    #   row and surface them as string attributes on every OTLP data
    #   point.
    # - Schema/validator (``--emit ...,schema`` /
    #   the ``validate`` subcommand): wired in Phase 8.
    #   ``schema.json`` declares a per-component ``dimensions`` block
    #   when the run is dim-aware and the validator's
    #   ``_validate_component_cells`` / ``_validate_component_row_count``
    #   / new ``_validate_long_form_dimensions`` honor it end-to-end.
    #
    # No multi-instance gate fires here anymore; the only remaining
    # downstream-flag rejection is the DST guard above.
    if args.otel_gauge_batch_seconds <= 0:
        p.error("--otel-gauge-batch-seconds must be > 0")
    # The OTEL stream scalar checks run unconditionally (matching
    # --otel-gauge-batch-seconds above): a bad value used to be silently
    # accepted whenever no endpoint was configured, so e.g.
    # `--otel-stream-speedup -5` could sit unnoticed in a wrapper script
    # until the day an endpoint was added. Endpoint-shape and token
    # checks stay inside the endpoint conditional — they validate the
    # endpoint values themselves.
    if args.otel_stream_speedup <= 0:
        p.error("--otel-stream-speedup must be > 0")
    if args.otel_stream_timeout_seconds <= 0:
        p.error("--otel-stream-timeout-seconds must be > 0")
    if args.otel_stream_max_events is not None and args.otel_stream_max_events < 1:
        p.error("--otel-stream-max-events must be >= 1")
    if args.otel_stream_auth_scheme.strip() == "":
        p.error("--otel-stream-auth-scheme must be non-empty")
    if args.otel_stream_protocol not in {"json", "protobuf"}:
        p.error("--otel-stream-protocol must be one of: json, protobuf")
    if any([args.otel_logs_endpoint, args.otel_metrics_endpoint, args.otel_traces_endpoint]):
        endpoints = [
            ("logs", args.otel_logs_endpoint, args.otel_logs_auth_token),
            ("metrics", args.otel_metrics_endpoint, args.otel_metrics_auth_token),
            ("traces", args.otel_traces_endpoint, args.otel_traces_auth_token),
        ]
        for signal, endpoint, token in endpoints:
            if endpoint:
                if not endpoint.startswith(("http://", "https://")):
                    p.error(f"the {signal} OTLP endpoint must start with "
                            "http:// or https:// (check --otel-endpoint or "
                            f"MEZMO_OTEL_{signal.upper()}_ENDPOINT)")
                if token and not token.strip():
                    p.error(f"the {signal} OTLP auth token must be non-empty "
                            "when provided (check --otel-auth-token or "
                            f"MEZMO_OTEL_{signal.upper()}_AUTH_TOKEN)")
    args.emit_selection = selected

    args.components = _parse_components_value(
        p.error, args.components, runtime_key=runtime_key
    )

    raw_scenarios = [item.strip().lower() for item in args.scenarios.split(",") if item.strip()]
    if not raw_scenarios:
        p.error("--scenarios must contain at least one scenario slug (or 'all')")
    if "all" in raw_scenarios and len(set(raw_scenarios)) > 1:
        # 'all' is a sentinel meaning "every scenario in the registry"; mixing
        # it with explicit slugs is ambiguous (does the user want only those
        # slugs, or every scenario plus those slugs?). Reject so the intent
        # has to be made explicit.
        p.error("--scenarios: 'all' is mutually exclusive with explicit slugs; "
                "pass either 'all' or a comma-separated list of slugs, not both")
    invalid_scenarios = sorted(set(raw_scenarios) - set(SCENARIOS.keys()) - {"all"})
    if invalid_scenarios:
        p.error("--scenarios contains invalid value(s): "
                f"{', '.join(invalid_scenarios)}. "
                f"Allowed: {', '.join(sorted(SCENARIOS.keys()))} or 'all'")
    if "all" in raw_scenarios:
        selected_scenarios = set(SCENARIOS.keys())
    else:
        selected_scenarios = set(raw_scenarios)
    args.scenarios = selected_scenarios

    raw_exclude = [item.strip().lower() for item in args.exclude_scenarios.split(",") if item.strip()]
    excluded_scenarios = set(raw_exclude)
    invalid_excluded = sorted(excluded_scenarios - set(SCENARIOS.keys()))
    if invalid_excluded:
        p.error("--exclude-scenarios contains invalid value(s): "
                f"{', '.join(invalid_excluded)}. "
                f"Allowed: {', '.join(sorted(SCENARIOS.keys()))}")
    args.exclude_scenarios = excluded_scenarios

    signal_level = (args.signal_level or "").strip().lower()
    if signal_level not in SIGNAL_LEVELS:
        p.error("--signal-level must be one of: "
                f"{', '.join(sorted(SIGNAL_LEVELS.keys()))}")
    args.signal_level = signal_level

    if args.anomaly_count is not None and args.anomaly_count < 1:
        p.error("--anomaly-count must be >= 1 (omit the flag for unlimited)")

    if args.metrics_per_component is not None and (
        args.metrics_per_component < 1
        or args.metrics_per_component > MAX_METRICS_PER_COMPONENT
    ):
        p.error(
            f"--metrics-per-component must be in [1, {MAX_METRICS_PER_COMPONENT}] "
            f"(omit the flag to use each component's historic default count)"
        )

    # Preflight cell-count cap. ``--interval-seconds 0.001`` with default flags
    # would emit 86.4M rows * ~75 default metrics = ~6.5B cells; large
    # combinations of the four knobs below silently chew through tens of GB
    # of memory and runtime before the user notices. The cost the cap
    # protects against is the in-memory ``np.empty((n_rows, n_cols),
    # float64)`` allocation and vectorized math inside ``generate_component``
    # (~52 GB of RAM at 6.5B cells), not just the on-disk CSV size. Disk
    # output is gated by ``emit_metrics`` but the matrix work runs
    # unconditionally for every component in ``args.components`` — so the
    # cap must apply on every code path that reaches ``generate_component``,
    # including ``--emit logs`` / ``--emit traces``
    # runs where no per-component CSV is written. Skipping the cap when
    # ``"metrics" not in args.emit_selection`` would invite OOMs without
    # saving any allocation or compute. (The ``combine`` subcommand never
    # routes through ``parse_args``, so the combine-over-a-huge-dataset
    # path is structurally exempt.)
    #
    # Mirror the generator's row-count derivation byte-for-byte. main()
    # computes ``total_seconds = SECONDS_PER_DAY * args.duration_days``
    # and ``n_rows = int(total_seconds // args.interval_seconds)``; use
    # the same two expressions here so the preflight estimate cannot
    # diverge from the row count actually emitted by generate_component.
    total_seconds = SECONDS_PER_DAY * args.duration_days
    rows_per_component = int(total_seconds // args.interval_seconds)
    if args.metrics_per_component is None:
        total_metrics = sum(
            DEFAULT_METRICS_PER_COMPONENT[c] for c in args.components
        )
    else:
        total_metrics = sum(
            min(args.metrics_per_component, len(COMPONENTS[c]))
            for c in args.components
        )
    # Multiply by n_instances per component (Phase 2/3). For
    # --instances-per-component: uniform N across all components.
    # For --instance-config: the per-component count is not yet parsed
    # here (that happens in main()), so use the max declared count
    # (MAX_INSTANCES_PER_COMPONENT) as a conservative upper bound.
    # Both flags are mutually exclusive so only one branch fires.
    if args.instance_config is not None:
        n_instances_factor = MAX_INSTANCES_PER_COMPONENT  # conservative
    else:
        n_instances_factor = args.instances_per_component
    estimated_cells = rows_per_component * total_metrics * n_instances_factor
    if estimated_cells > PREFLIGHT_CELL_CAP and not args.allow_huge_output:
        instance_clause = (
            f"x --instance-config (≤{MAX_INSTANCES_PER_COMPONENT} instances/component) "
            if args.instance_config is not None
            else f"x --instances-per-component {args.instances_per_component} "
        )
        p.error(
            f"preflight cell-count cap exceeded: "
            f"--interval-seconds {args.interval_seconds} "
            f"x --duration-days {args.duration_days} "
            f"x --components ({len(args.components)} selected) "
            f"x --metrics-per-component "
            f"{args.metrics_per_component if args.metrics_per_component is not None else 'default'} "
            f"{instance_clause}"
            f"would emit ~{estimated_cells:,} metric cells "
            f"(cap: {PREFLIGHT_CELL_CAP:,}). "
            f"Raise --interval-seconds, lower --duration-days, lower "
            f"--metrics-per-component, narrow --components, reduce instances, "
            f"or pass --allow-huge-output to bypass."
        )

    return args

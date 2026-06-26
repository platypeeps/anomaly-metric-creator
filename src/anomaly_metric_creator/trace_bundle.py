"""Offline tooling for exported server command trace bundles."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .server_traces import (
    COMMAND_TRACE_EXPORT_VERSION,
    CommandTrace,
    trace_matches_search,
    unsupported_summary_from_traces,
)


TRACE_BUNDLE_KIND = "CommandTraceExport"
TRACE_BUNDLE_API_VERSION = f"amc.simulator/v{COMMAND_TRACE_EXPORT_VERSION}"
_MAX_SEARCH_LIMIT = 500


def _bundle_int_field(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"trace bundle {key} must be an integer; got {value!r}")
    return int(value)


@dataclass(frozen=True)
class TraceBundle:
    """A portable command trace export loaded from disk."""

    path: Path
    kind: str
    api_version: str
    schema_version: int
    declared_trace_count: int
    traces: tuple[CommandTrace, ...]


def load_trace_bundle(path: str | Path) -> TraceBundle:
    """Load and validate a JSON trace bundle exported by the debug API."""
    bundle_path = Path(path)
    with bundle_path.open(encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError("trace bundle must be a JSON object")
    kind = payload.get("kind")
    if kind != TRACE_BUNDLE_KIND:
        raise ValueError(
            f"trace bundle kind must be {TRACE_BUNDLE_KIND!r}; got {kind!r}"
        )
    api_version = payload.get("apiVersion")
    if api_version != TRACE_BUNDLE_API_VERSION:
        raise ValueError(
            "unsupported trace bundle apiVersion "
            f"{api_version!r}; expected {TRACE_BUNDLE_API_VERSION!r}"
        )
    schema_version = _bundle_int_field(payload, "schema_version")
    if schema_version != COMMAND_TRACE_EXPORT_VERSION:
        raise ValueError(
            "unsupported trace bundle schema_version "
            f"{schema_version!r}; expected {COMMAND_TRACE_EXPORT_VERSION}"
        )
    traces_payload = payload.get("traces")
    if not isinstance(traces_payload, list):
        raise ValueError("trace bundle must include a traces list")
    traces_list = []
    for index, item in enumerate(traces_payload):
        if not isinstance(item, dict):
            raise ValueError(f"trace entry {index} must be an object")
        try:
            traces_list.append(CommandTrace.from_dict(item))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"trace entry {index} is invalid: {exc}") from exc
    traces = tuple(traces_list)
    declared_count = payload.get("trace_count", len(traces))
    if isinstance(declared_count, bool) or not isinstance(declared_count, int):
        raise ValueError(
            f"trace bundle trace_count must be an integer; got {declared_count!r}"
        )
    if declared_count != len(traces):
        raise ValueError(
            f"trace bundle trace_count is {declared_count}, but traces has "
            f"{len(traces)} item(s)"
        )
    return TraceBundle(
        path=bundle_path,
        kind=kind,
        api_version=str(api_version),
        schema_version=int(schema_version),
        declared_trace_count=declared_count,
        traces=traces,
    )


def summarize_trace_bundle(bundle: TraceBundle) -> dict[str, Any]:
    """Return counts and high-value backlog groups for an offline bundle."""
    support_counts = Counter(trace.support_status for trace in bundle.traces)
    family_counts = Counter(trace.command_family for trace in bundle.traces)
    scenario_counts = Counter(
        scenario_id
        for trace in bundle.traces
        for scenario_id in trace.active_scenarios
    )
    timestamps = [trace.received_at_wall_time for trace in bundle.traces]
    first_seen = min(timestamps) if timestamps else ""
    last_seen = max(timestamps) if timestamps else ""
    return {
        "path": str(bundle.path),
        "kind": bundle.kind,
        "apiVersion": bundle.api_version,
        "schema_version": bundle.schema_version,
        "trace_count": len(bundle.traces),
        "declared_trace_count": bundle.declared_trace_count,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "support_status_counts": dict(sorted(support_counts.items())),
        "command_family_counts": dict(sorted(family_counts.items())),
        "scenario_counts": dict(sorted(scenario_counts.items())),
        "top_unsupported": unsupported_trace_summary(bundle)[:10],
    }


def search_trace_bundle(
    bundle: TraceBundle,
    *,
    query: str = "",
    support_status: str = "",
    command_family: str = "",
    scenario_id: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Search a trace bundle using the same filters as the debug API."""
    clamped_limit = max(1, min(limit, _MAX_SEARCH_LIMIT))
    clamped_offset = max(0, offset)
    filtered = [
        trace for trace in bundle.traces
        if trace_matches_search(
            trace,
            query=query,
            support_status=support_status,
            command_family=command_family,
            scenario_id=scenario_id,
        )
    ]
    total = len(filtered)
    page = sorted(
        filtered, key=lambda trace: trace.id, reverse=True
    )[clamped_offset: clamped_offset + clamped_limit]
    return {
        "items": [
            {"version": len(bundle.traces), **trace.to_dict()}
            for trace in page
        ],
        "total": total,
        "limit": clamped_limit,
        "offset": clamped_offset,
        "query": query,
        "support_status": support_status,
        "command_family": command_family,
        "scenario_id": scenario_id,
        "search_backend": "bundle",
    }


def unsupported_trace_summary(bundle: TraceBundle) -> list[dict[str, Any]]:
    """Return the unsupported/partial command backlog for a bundle."""
    traces = [
        trace for trace in bundle.traces
        if trace.support_status != "supported"
    ]
    return unsupported_summary_from_traces(traces)


def write_trace_bundle_csv(bundle: TraceBundle, output_path: str | Path) -> int:
    """Flatten command traces to CSV for spreadsheets and workshop notes."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "id",
        "received_at_wall_time",
        "simulated_time",
        "raw_input",
        "argv",
        "client",
        "command_family",
        "verb",
        "resource_kind",
        "resource_name",
        "namespace",
        "parsed_flags",
        "support_status",
        "matched_rule_id",
        "fingerprint",
        "guessed_intent",
        "active_scenarios",
        "exit_code",
        "latency_ms",
        "stdout_preview",
        "stderr_preview",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for trace in bundle.traces:
            writer.writerow({
                "id": trace.id,
                "received_at_wall_time": trace.received_at_wall_time,
                "simulated_time": trace.simulated_time,
                "raw_input": trace.raw_input,
                "argv": json.dumps(list(trace.argv), sort_keys=True),
                "client": trace.client,
                "command_family": trace.command_family,
                "verb": trace.verb,
                "resource_kind": trace.resource_kind,
                "resource_name": trace.resource_name,
                "namespace": trace.namespace,
                "parsed_flags": json.dumps(trace.parsed_flags, sort_keys=True),
                "support_status": trace.support_status,
                "matched_rule_id": trace.matched_rule_id,
                "fingerprint": trace.fingerprint,
                "guessed_intent": trace.guessed_intent,
                "active_scenarios": ",".join(trace.active_scenarios),
                "exit_code": trace.exit_code,
                "latency_ms": trace.latency_ms,
                "stdout_preview": trace.stdout_preview,
                "stderr_preview": trace.stderr_preview,
            })
    return len(bundle.traces)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amc trace-bundle",
        description=(
            "Inspect exported command trace bundles offline without starting "
            "the simulator server."
        ),
        allow_abbrev=False,
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    _add_summary_parser(subcommands)
    _add_search_parser(subcommands)
    _add_unsupported_parser(subcommands)
    _add_export_csv_parser(subcommands)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        bundle = load_trace_bundle(args.bundle)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    if args.command == "summary":
        payload = summarize_trace_bundle(bundle)
        _emit(payload, args.format, text_printer=_print_summary_text)
        return
    if args.command == "search":
        payload = search_trace_bundle(
            bundle,
            query=args.query,
            support_status=args.support_status,
            command_family=args.command_family,
            scenario_id=args.scenario_id,
            limit=args.limit,
            offset=args.offset,
        )
        _emit(payload, args.format, text_printer=_print_search_text)
        return
    if args.command == "unsupported":
        payload = unsupported_trace_summary(bundle)
        _emit(payload, args.format, text_printer=_print_unsupported_text)
        return
    if args.command == "export-csv":
        count = write_trace_bundle_csv(bundle, args.output)
        print(f"trace-bundle: wrote {count} trace(s) to {args.output}")
        return
    parser.error(f"unknown trace-bundle command {args.command!r}")


def _add_bundle_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "bundle",
        type=Path,
        help="JSON file from GET /v1/debug/commands/export.",
    )


def _add_format_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text).",
    )


def _add_summary_parser(subcommands: argparse._SubParsersAction) -> None:
    parser = subcommands.add_parser(
        "summary",
        help="Show counts by support status, command family, and scenario.",
    )
    _add_bundle_argument(parser)
    _add_format_argument(parser)


def _add_search_parser(subcommands: argparse._SubParsersAction) -> None:
    parser = subcommands.add_parser(
        "search",
        help="Search traces with the same filters as /v1/debug/search.",
    )
    _add_bundle_argument(parser)
    parser.add_argument("--q", dest="query", default="", help="Text query.")
    parser.add_argument(
        "--status",
        dest="support_status",
        default="",
        help="Filter by support status.",
    )
    parser.add_argument(
        "--family",
        dest="command_family",
        default="",
        help="Filter by command family.",
    )
    parser.add_argument(
        "--scenario",
        dest="scenario_id",
        default="",
        help="Filter by active scenario id.",
    )
    parser.add_argument("--limit", type=int, default=50, help="Maximum rows.")
    parser.add_argument("--offset", type=int, default=0, help="Rows to skip.")
    _add_format_argument(parser)


def _add_unsupported_parser(subcommands: argparse._SubParsersAction) -> None:
    parser = subcommands.add_parser(
        "unsupported",
        help="Group unsupported and partial traces by fingerprint.",
    )
    _add_bundle_argument(parser)
    _add_format_argument(parser)


def _add_export_csv_parser(subcommands: argparse._SubParsersAction) -> None:
    parser = subcommands.add_parser(
        "export-csv",
        help="Write a flattened trace CSV for spreadsheets.",
    )
    _add_bundle_argument(parser)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="CSV path to write.",
    )


def _emit(
    payload: Any,
    output_format: str,
    *,
    text_printer,
) -> None:
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    text_printer(payload)


def _print_summary_text(summary: dict[str, Any]) -> None:
    print(f"Trace bundle: {summary['path']}")
    print(f"Schema: {summary['apiVersion']} ({summary['schema_version']})")
    print(f"Traces: {summary['trace_count']}")
    if summary["first_seen"] or summary["last_seen"]:
        print(f"Window: {summary['first_seen']} -> {summary['last_seen']}")
    print()
    _print_counter("Support statuses", summary["support_status_counts"])
    _print_counter("Command families", summary["command_family_counts"])
    _print_counter("Scenarios", summary["scenario_counts"])
    print("Top unsupported fingerprints:")
    groups = summary["top_unsupported"]
    if not groups:
        print("  none")
        return
    for group in groups:
        statuses = _status_counts_label(group["support_statuses"])
        print(f"  {group['count']:>4}  {group['fingerprint']}  {statuses}")


def _print_search_text(search: dict[str, Any]) -> None:
    print(
        "Matches: "
        f"{search['total']} "
        f"(limit={search['limit']}, offset={search['offset']})"
    )
    rows = [
        [
            str(item["id"]),
            item["support_status"],
            item["command_family"],
            item["fingerprint"],
            item["raw_input"],
        ]
        for item in search["items"]
    ]
    _print_table(["id", "status", "family", "fingerprint", "command"], rows)


def _print_unsupported_text(groups: list[dict[str, Any]]) -> None:
    if not groups:
        print("No unsupported or partial traces.")
        return
    rows = [
        [
            str(group["count"]),
            _status_counts_label(group["support_statuses"]),
            group["fingerprint"],
            group.get("guessed_intent", ""),
            group["examples"][0]["raw_input"] if group["examples"] else "",
        ]
        for group in groups
    ]
    _print_table(["count", "statuses", "fingerprint", "intent", "example"], rows)


def _print_counter(title: str, values: dict[str, int]) -> None:
    print(f"{title}:")
    if not values:
        print("  none")
        print()
        return
    for key, value in values.items():
        print(f"  {key}: {value}")
    print()


def _print_table(headers: list[str], rows: Iterable[list[str]]) -> None:
    rows = list(rows)
    if not rows:
        print("  none")
        return
    widths = [
        max(len(header), *(len(row[index]) for row in rows))
        for index, header in enumerate(headers)
    ]
    print("  " + "  ".join(
        header.ljust(widths[index])
        for index, header in enumerate(headers)
    ))
    print("  " + "  ".join("-" * width for width in widths))
    for row in rows:
        print("  " + "  ".join(
            value.ljust(widths[index])
            for index, value in enumerate(row)
        ))


def _status_counts_label(counts: dict[str, int]) -> str:
    return ",".join(
        f"{status}:{count}"
        for status, count in sorted(counts.items())
    )

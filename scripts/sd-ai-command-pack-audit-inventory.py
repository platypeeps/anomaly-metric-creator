#!/usr/bin/env python3
"""Report largest committed regular files without executing checkout code."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from sd_ai_command_pack_lib import CommandError, run_command

SCHEMA_VERSION = 1
MEASUREMENT = "blob-bytes"
MAX_TRACKED_ENTRIES = 100_000
MAX_LIMIT = 1_000
REGULAR_MODES = frozenset({b"100644", b"100755"})
VALID_OID_LENGTHS = frozenset({40, 64})


class AuditInventoryError(RuntimeError):
    """Raised when committed-tree inventory cannot be trusted."""


@dataclass(frozen=True)
class InventoryEntry:
    path_bytes: bytes
    path: str
    size: int
    oid: str

    def as_json(self) -> dict[str, object]:
        return {"path": self.path, "bytes": self.size, "oid": self.oid}


def positive_limit(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer") from exc
    if parsed < 1 or parsed > MAX_LIMIT:
        raise argparse.ArgumentTypeError(f"limit must be between 1 and {MAX_LIMIT}")
    return parsed


def _decode_detail(value: object, *, fallback: str) -> str:
    if not isinstance(value, bytes):
        return fallback
    detail = " ".join(value.decode("utf-8", errors="replace").split())
    return detail[:500] or fallback


def _valid_oid(raw: bytes) -> bool:
    return len(raw) in VALID_OID_LENGTHS and all(
        byte in b"0123456789abcdef" for byte in raw
    )


def parse_ls_tree(raw: bytes) -> tuple[list[InventoryEntry], int]:
    if not isinstance(raw, bytes):
        raise AuditInventoryError("git tree inventory was not byte-oriented")
    if raw and not raw.endswith(b"\0"):
        raise AuditInventoryError("git tree inventory was not NUL terminated")

    records = raw.split(b"\0")[:-1] if raw else []
    if len(records) > MAX_TRACKED_ENTRIES:
        raise AuditInventoryError(
            f"git tree inventory exceeds the {MAX_TRACKED_ENTRIES} tracked-entry limit"
        )

    entries: list[InventoryEntry] = []
    skipped = 0
    for record in records:
        metadata, separator, path_bytes = record.partition(b"\t")
        fields = metadata.split()
        if not separator or not path_bytes or len(fields) != 4:
            raise AuditInventoryError("git tree inventory contains a malformed record")
        mode, object_type, oid_bytes, size_bytes = fields
        if not _valid_oid(oid_bytes):
            raise AuditInventoryError(
                "git tree inventory contains an invalid object ID"
            )
        if object_type != b"blob" or mode not in REGULAR_MODES:
            skipped += 1
            continue
        if not size_bytes.isdigit():
            raise AuditInventoryError(
                "git tree inventory contains an invalid blob size"
            )
        entries.append(
            InventoryEntry(
                path_bytes=path_bytes,
                path=os.fsdecode(path_bytes),
                size=int(size_bytes, 10),
                oid=oid_bytes.decode("ascii"),
            )
        )

    entries.sort(key=lambda item: (-item.size, item.path_bytes))
    return entries, skipped


def build_report(repo: Path, *, limit: int) -> dict[str, object]:
    resolved = repo.expanduser().resolve()
    if not resolved.is_dir():
        raise AuditInventoryError(f"repository is not a directory: {resolved}")

    result = run_command(
        ["git", "ls-tree", "-r", "-l", "-z", "--full-tree", "HEAD", "--"],
        cwd=resolved,
        check=False,
        text=False,
        context="inventory committed files for architecture audit",
    )
    if result.returncode != 0:
        raise AuditInventoryError(
            "failed to inventory committed files: "
            + _decode_detail(
                result.stderr,
                fallback=f"git exited with status {result.returncode}",
            )
        )
    stdout = result.stdout
    if not isinstance(stdout, bytes):
        raise AuditInventoryError("git tree inventory was not byte-oriented")
    entries, skipped = parse_ls_tree(stdout)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "measurement": MEASUREMENT,
        "repository": str(resolved),
        "trackedRegularFiles": len(entries),
        "skippedNonRegular": skipped,
        "limit": limit,
        "entries": [entry.as_json() for entry in entries[:limit]],
    }


def render_human(report: dict[str, object]) -> str:
    lines = [
        "Audit architecture inventory",
        f"- measurement: {report['measurement']}",
        f"- tracked regular files: {report['trackedRegularFiles']}",
        f"- skipped non-regular entries: {report['skippedNonRegular']}",
        "- largest files:",
    ]
    entries = report["entries"]
    if not isinstance(entries, list) or not entries:
        lines.append("  none")
        return "\n".join(lines)
    for item in entries:
        if not isinstance(item, dict):
            raise AuditInventoryError("inventory report contains an invalid entry")
        lines.append(
            f"  {item['bytes']} bytes {json.dumps(item['path'], ensure_ascii=True)}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report the largest regular files in the committed Git tree "
            "without reading worktree paths or executing checkout code."
        )
    )
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--limit", type=positive_limit, default=20)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_report(args.repo, limit=args.limit)
        if args.json:
            print(json.dumps(report, sort_keys=True, ensure_ascii=True))
        else:
            print(render_human(report))
        return 0
    except (AuditInventoryError, CommandError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

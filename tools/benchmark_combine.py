#!/usr/bin/env python3
"""Benchmark wide combine pre-scan vs trusted-monotonic streaming."""

from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import io
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from anomaly_metric_creator.combine import combine_logs_unified


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate synthetic wide component CSVs and compare the defensive "
            "monotonic pre-scan against the trusted-monotonic combine path."
        )
    )
    parser.add_argument("--components", type=int, default=14)
    parser.add_argument("--metrics", type=int, default=8)
    parser.add_argument("--rows", type=int, default=50_000)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Directory for benchmark inputs/outputs; defaults to a temp dir.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the generated work directory instead of deleting it.",
    )
    return parser.parse_args()


def _write_inputs(
    work_dir: Path,
    *,
    component_count: int,
    metric_count: int,
    row_count: int,
    interval_seconds: int,
) -> list[str]:
    start = dt.datetime(2026, 3, 10)
    components = [f"component_{idx:02d}" for idx in range(component_count)]
    for component_index, component in enumerate(components):
        metrics = [f"metric_{idx:02d}" for idx in range(metric_count)]
        with open(work_dir / f"{component}.csv", "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(["timestamp", *metrics])
            for row_index in range(row_count):
                timestamp = start + dt.timedelta(
                    seconds=row_index * interval_seconds
                )
                values = [
                    component_index * 1_000_000 + metric_index * 10_000 + row_index
                    for metric_index in range(metric_count)
                ]
                writer.writerow([
                    timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    *values,
                ])
    return components


def _time_combine(
    components: list[str],
    work_dir: Path,
    *,
    label: str,
    trusted_monotonic: bool,
) -> tuple[float, int, float]:
    output_file = work_dir / f"combined_{label}.csv"
    started = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()):
        total_rows, size_mb = combine_logs_unified(
            components,
            work_dir,
            output_file=output_file,
            assume_monotonic_wide_components=(
                components if trusted_monotonic else None
            ),
        )
    elapsed = time.perf_counter() - started
    return elapsed, total_rows, size_mb


def main() -> int:
    args = _parse_args()
    if args.components < 1 or args.metrics < 1 or args.rows < 1:
        raise SystemExit("--components, --metrics, and --rows must be >= 1")
    if args.interval_seconds < 1:
        raise SystemExit("--interval-seconds must be >= 1")

    created_temp_dir = False
    if args.work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="amc-combine-bench-"))
        created_temp_dir = True
    else:
        work_dir = args.work_dir
        work_dir.mkdir(parents=True, exist_ok=True)

    try:
        components = _write_inputs(
            work_dir,
            component_count=args.components,
            metric_count=args.metrics,
            row_count=args.rows,
            interval_seconds=args.interval_seconds,
        )
        prescan_elapsed, total_rows, size_mb = _time_combine(
            components, work_dir, label="prescan", trusted_monotonic=False,
        )
        trusted_elapsed, trusted_rows, trusted_size_mb = _time_combine(
            components, work_dir, label="trusted", trusted_monotonic=True,
        )
        if trusted_rows != total_rows:
            raise SystemExit(
                f"row count mismatch: prescan={total_rows} trusted={trusted_rows}"
            )
        speedup = prescan_elapsed / trusted_elapsed if trusted_elapsed else float("inf")
        print(
            "combine benchmark: "
            f"{args.components} components x {args.metrics} metrics x "
            f"{args.rows} rows"
        )
        print(
            f"pre-scan: {prescan_elapsed:.3f}s, "
            f"{total_rows:,} rows, {size_mb:.2f} MiB"
        )
        print(
            f"trusted:  {trusted_elapsed:.3f}s, "
            f"{trusted_rows:,} rows, {trusted_size_mb:.2f} MiB"
        )
        print(f"speedup:  {speedup:.2f}x")
        suffix = (
            " (deleted; pass --keep to inspect)"
            if created_temp_dir and not args.keep
            else ""
        )
        print(f"work dir: {work_dir}{suffix}")
        return 0
    finally:
        if created_temp_dir and not args.keep:
            shutil.rmtree(work_dir)


if __name__ == "__main__":
    raise SystemExit(main())

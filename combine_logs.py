#!/usr/bin/env python3
"""
Combine all component log files into a single unified file.
Unified format: one row per timestamp with every component's metrics inline.
Column names are prefixed with the component name (e.g. authservice_active_sessions).
"""

import csv
import os
from pathlib import Path

INPUT_DIR = Path('iot_logs')
OUTPUT_FILE_UNIFIED = INPUT_DIR / 'combined_metrics_unified.csv'

# Filenames in the input directory that are not component metric files.
NON_COMPONENT_FILES = {'anomalies.csv'}


def discover_components(input_dir):
    """Return the sorted list of component names found in ``input_dir``.

    A component is any *.csv file in ``input_dir`` that isn't the anomalies
    manifest or one of this script's own outputs (combined_metrics_*).
    """
    components = []
    for path in sorted(Path(input_dir).glob('*.csv')):
        name = path.name
        if name in NON_COMPONENT_FILES:
            continue
        if name.startswith('combined_metrics_'):
            continue
        components.append(path.stem)
    return components


def combine_logs_unified(components, input_dir, output_file=None):
    """Join the per-component CSVs in ``input_dir`` into a single unified CSV.

    ``output_file`` defaults to ``input_dir/combined_metrics_unified.csv``.
    Returns ``(total_rows, size_mb)``.
    """
    input_dir = Path(input_dir)
    if output_file is None:
        output_file = input_dir / 'combined_metrics_unified.csv'
    output_file = Path(output_file)

    print(f"\nCreating UNIFIED format combined file...")
    print(f"Components discovered: {', '.join(components)}")

    data_by_timestamp = {}
    component_metrics = {}

    for component in components:
        input_path = input_dir / f'{component}.csv'
        print(f"Loading {component}.csv...")

        with open(input_path, 'r') as infile:
            reader = csv.DictReader(infile)
            metric_names = [f for f in reader.fieldnames if f != 'timestamp']
            component_metrics[component] = metric_names

            for row in reader:
                timestamp = row['timestamp']
                bucket = data_by_timestamp.setdefault(timestamp, {})
                bucket[component] = {metric: row[metric] for metric in metric_names}

    fieldnames = ['timestamp']
    for component in components:
        for metric in component_metrics[component]:
            fieldnames.append(f'{component}_{metric}')

    print(f"Total columns: {len(fieldnames)} (1 timestamp + {len(fieldnames) - 1} metrics)")

    with open(output_file, 'w', newline='') as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()

        for timestamp in sorted(data_by_timestamp.keys()):
            row = {'timestamp': timestamp}
            for component in components:
                component_row = data_by_timestamp[timestamp].get(component, {})
                for metric in component_metrics[component]:
                    row[f'{component}_{metric}'] = component_row.get(metric, '')
            writer.writerow(row)

    total_rows = len(data_by_timestamp)
    size_mb = os.path.getsize(output_file) / (1024 * 1024)
    print(f"\nUnified format file created: {output_file}")
    print(f"Total rows: {total_rows:,}")
    print(f"File size: {size_mb:.2f} MB")
    return total_rows, size_mb


def run(input_dir):
    """Discover components in ``input_dir`` and write the unified combined CSV."""
    input_dir = Path(input_dir)
    components = discover_components(input_dir)
    if not components:
        raise SystemExit(f"No component CSVs found in {input_dir}/")
    return combine_logs_unified(components, input_dir)


if __name__ == '__main__':
    print("=" * 70)
    print("COMBINING LOG FILES")
    print("=" * 70)

    unified_rows, unified_size = run(INPUT_DIR)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"\nUnified format (one row per timestamp, all components):")
    print(f"  File: {INPUT_DIR / 'combined_metrics_unified.csv'}")
    print(f"  Rows: {unified_rows:,}")
    print(f"  Size: {unified_size:.2f} MB")

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

# Filenames in INPUT_DIR that are not component metric files.
NON_COMPONENT_FILES = {'anomalies.csv'}


def discover_components():
    """Return the sorted list of component names found in INPUT_DIR.

    A component is any *.csv file in INPUT_DIR that isn't the anomalies
    manifest or one of this script's own outputs (combined_metrics_*).
    """
    components = []
    for path in sorted(INPUT_DIR.glob('*.csv')):
        name = path.name
        if name in NON_COMPONENT_FILES:
            continue
        if name.startswith('combined_metrics_'):
            continue
        components.append(path.stem)
    return components


def combine_logs_unified(components):
    print(f"\nCreating UNIFIED format combined file...")
    print(f"Components discovered: {', '.join(components)}")

    data_by_timestamp = {}
    component_metrics = {}

    for component in components:
        input_file = INPUT_DIR / f'{component}.csv'
        print(f"Loading {component}.csv...")

        with open(input_file, 'r') as infile:
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

    with open(OUTPUT_FILE_UNIFIED, 'w', newline='') as outfile:
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
    size_mb = os.path.getsize(OUTPUT_FILE_UNIFIED) / (1024 * 1024)
    print(f"\nUnified format file created: {OUTPUT_FILE_UNIFIED}")
    print(f"Total rows: {total_rows:,}")
    print(f"File size: {size_mb:.2f} MB")
    return total_rows, size_mb


if __name__ == '__main__':
    print("=" * 70)
    print("COMBINING LOG FILES")
    print("=" * 70)

    components = discover_components()
    if not components:
        raise SystemExit(f"No component CSVs found in {INPUT_DIR}/")

    unified_rows, unified_size = combine_logs_unified(components)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"\nUnified format (one row per timestamp, all components):")
    print(f"  File: {OUTPUT_FILE_UNIFIED}")
    print(f"  Rows: {unified_rows:,}")
    print(f"  Size: {unified_size:.2f} MB")

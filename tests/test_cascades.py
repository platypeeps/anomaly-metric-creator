import numpy as np
import pytest
from pathlib import Path

def test_register_cascade(amc):
    amc.cascading_anomalies.clear()
    def fake_gen(ts, idx): return 1.0
    amc.register_cascade("test_comp", 100, "test_metric", "test desc", fake_gen)
    
    assert "test_comp" in amc.cascading_anomalies
    cascade = amc.cascading_anomalies["test_comp"][0]
    assert cascade["time_offset"] == 100
    assert cascade["metric"] == "test_metric"
    assert cascade["generator"] == fake_gen

def test_cascades_applied_in_generate_component(amc, tmp_path):
    amc.cascading_anomalies.clear()
    out = tmp_path / "cascade_test"
    out.mkdir()
    
    specs = [amc.MetricSpec(name="m0", base=10.0, std=0.0)]
    
    # Register a cascade for "comp_a"
    amc.register_cascade("comp_a", 5, "m0", "cascade anomaly", lambda ts, idx: 99.0)
    
    ts_array, ts_strings = amc._build_timestamp_arrays(10, 1.0)
    amc.generate_component(
        "comp_a",
        specs,
        [], # No primary anomalies
        base_dir=out,
        total_seconds=10,
        drop_rate=0.0,
        interval=1.0,
        ts_array=ts_array,
        ts_strings=ts_strings,
    )
    
    from conftest import read_component_rows
    rows, header = read_component_rows(out, "comp_a")
    idx = header.index("m0")
    
    # Check that the cascade was applied at t=5
    # amc.START is 2026-03-10 00:00:00
    ts_cascade = "2026-03-10 00:00:05"
    assert float(rows[ts_cascade][idx]) == 99.0
    
    # Check that other rows are normal
    ts_normal = "2026-03-10 00:00:04"
    assert float(rows[ts_normal][idx]) == 10.0

def test_cascades_populated_from_scenarios(amc, tmp_path):
    """Registry-driven _apply_scenarios populates cascading_anomalies for a 1-day run."""
    import io
    import sys
    out = tmp_path / "cascade_reg_test"
    out.mkdir()
    amc.cascading_anomalies.clear()
    args_ns = amc.parse_args([
        "--seed", "42", "--duration-days", "1",
        "--output-dir", str(out),
    ])
    active = amc._resolve_scenarios(args_ns)
    component_anomalies = {name: [] for name in amc.COMPONENTS}
    amc._apply_scenarios(component_anomalies, amc.cascading_anomalies, active)
    assert len(amc.cascading_anomalies) > 0
    assert "authservice" in amc.cascading_anomalies

"""Tests for --instance-config PATH (VER-140 Phase 3).

Verifies:
- YAML and JSON config files produce the correct RunContext.instances shape.
- --instance-config is mutually exclusive with --instances-per-component.
- Schema validation: unknown component, unknown field, empty list, bad structure.
- Missing components fall back to anonymous Instance().
- Default behavior (flag absent) is unchanged.
"""

import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "anomaly-metric-creator.py"


# ---------------------------------------------------------------------------
# Module fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def amc():
    import importlib.util
    spec = importlib.util.spec_from_file_location("amc_instance_config_test", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(amc, extra_args, tmp_path):
    """Call parse_args with minimal required flags + extra_args."""
    args = amc.parse_args([
        "--output-dir", str(tmp_path),
        "--duration-days", "1",
    ] + list(extra_args))
    return args


def _run(amc, out_dir, extra_args, *, days=1, seed=42):
    args = [
        "--seed", str(seed),
        "--duration-days", str(days),
        "--output-dir", str(out_dir),
    ] + list(extra_args)
    buf = io.StringIO()
    real = sys.stderr
    sys.stderr = buf
    try:
        amc.main(args)
    finally:
        sys.stderr = real
    return out_dir


def _write_yaml(tmp_path, content: str) -> Path:
    p = tmp_path / "instances.yaml"
    p.write_text(content)
    return p


def _write_json(tmp_path, data: dict) -> Path:
    p = tmp_path / "instances.json"
    p.write_text(json.dumps(data))
    return p


# ---------------------------------------------------------------------------
# Round-trip: YAML config
# ---------------------------------------------------------------------------

def test_yaml_config_loads_correctly(amc, tmp_path):
    """YAML config with two components sets ctx.instances correctly."""
    cfg = _write_yaml(tmp_path, """
components:
  authservice:
    - {id: auth-east, pod: pod-east, region: us-east-1}
    - {id: auth-west, pod: pod-west, region: us-west-2}
  database:
    - {id: db-primary, host: db1.internal}
""")
    args = _parse(amc, ["--instance-config", str(cfg)], tmp_path / "out")
    assert args.instance_config == cfg
    # Remaining config loading happens in main(); test _load_instance_config directly.
    result = amc._load_instance_config(cfg)
    assert len(result["authservice"]) == 2
    assert result["authservice"][0].id == "auth-east"
    assert result["authservice"][0].pod == "pod-east"
    assert result["authservice"][0].region == "us-east-1"
    assert result["authservice"][1].id == "auth-west"
    assert len(result["database"]) == 1
    assert result["database"][0].id == "db-primary"
    assert result["database"][0].host == "db1.internal"


# ---------------------------------------------------------------------------
# Round-trip: JSON config
# ---------------------------------------------------------------------------

def test_json_config_loads_correctly(amc, tmp_path):
    """JSON config round-trips identically to YAML."""
    cfg = _write_json(tmp_path, {
        "components": {
            "loadbalancer": [
                {"id": "lb-1", "pod": "pod-0"},
                {"id": "lb-2", "pod": "pod-1"},
            ]
        }
    })
    result = amc._load_instance_config(cfg)
    assert len(result["loadbalancer"]) == 2
    assert result["loadbalancer"][0].id == "lb-1"
    assert result["loadbalancer"][1].id == "lb-2"


# ---------------------------------------------------------------------------
# Missing components fall back to anonymous Instance()
# ---------------------------------------------------------------------------

def test_missing_component_falls_back_to_anonymous(amc, tmp_path):
    """Components not listed in config get a single anonymous Instance()."""
    cfg = _write_yaml(tmp_path, """
components:
  authservice:
    - {id: auth-1}
""")
    # _load_instance_config only covers explicitly listed components;
    # main() fills in the rest from INSTANCES (which are anonymous).
    result = amc._load_instance_config(cfg)
    assert "authservice" in result
    # All other components are absent from the map — main() fills them from INSTANCES.
    assert "loadbalancer" not in result
    assert "database" not in result


def test_partial_config_run_produces_correct_output(amc, tmp_path):
    """End-to-end: listed component gets dimension columns; unlisted stays dimensionless."""
    cfg = _write_yaml(tmp_path, """
components:
  authservice:
    - {id: auth-east, pod: pod-0}
    - {id: auth-west, pod: pod-1}
""")
    out = tmp_path / "out"
    out.mkdir()
    # Use --drop-rate 0 so row counts are exact multiples.
    _run(amc, out, ["--instance-config", str(cfg),
                    "--components", "authservice,loadbalancer",
                    "--drop-rate", "0"])

    # authservice: should have dimension columns + exactly 2× rows
    with open(out / "authservice.csv") as f:
        header = f.readline().rstrip().split(",")
        rows = f.readlines()
    assert header[0] == "timestamp"
    assert "id" in header
    assert "pod" in header
    assert len(rows) == 2 * 86400  # 2 instances × 86400 rows/day, no drops

    # loadbalancer: should remain dimensionless (anonymous Instance)
    with open(out / "loadbalancer.csv") as f:
        lb_header = f.readline().rstrip().split(",")
    assert "id" not in lb_header
    assert lb_header[0] == "timestamp"


# ---------------------------------------------------------------------------
# Mutual exclusion: --instance-config vs --instances-per-component
# ---------------------------------------------------------------------------

def test_mutually_exclusive_with_instances_per_component(amc, tmp_path):
    """--instance-config and --instances-per-component are mutually exclusive."""
    cfg = _write_yaml(tmp_path, "components:\n  authservice:\n    - {id: a1}\n")
    with pytest.raises(SystemExit):
        _parse(amc, [
            "--instance-config", str(cfg),
            "--instances-per-component", "2",
        ], tmp_path / "out")


# ---------------------------------------------------------------------------
# Validation: unknown component
# ---------------------------------------------------------------------------

def test_unknown_component_raises(amc, tmp_path):
    cfg = _write_yaml(tmp_path, """
components:
  nonexistent_service:
    - {id: x1}
""")
    with pytest.raises(ValueError, match="unknown component"):
        amc._load_instance_config(cfg)


# ---------------------------------------------------------------------------
# Validation: unknown Instance field
# ---------------------------------------------------------------------------

def test_unknown_field_raises(amc, tmp_path):
    cfg = _write_yaml(tmp_path, """
components:
  authservice:
    - {id: a1, datacenter: dc1}
""")
    with pytest.raises(ValueError, match="unknown field"):
        amc._load_instance_config(cfg)


# ---------------------------------------------------------------------------
# Validation: empty instance list
# ---------------------------------------------------------------------------

def test_empty_instance_list_raises(amc, tmp_path):
    cfg = _write_yaml(tmp_path, """
components:
  authservice: []
""")
    with pytest.raises(ValueError, match="empty instance list"):
        amc._load_instance_config(cfg)


# ---------------------------------------------------------------------------
# Validation: missing 'components' key
# ---------------------------------------------------------------------------

def test_missing_components_key_raises(amc, tmp_path):
    cfg = _write_yaml(tmp_path, "instances:\n  authservice:\n    - {id: a1}\n")
    with pytest.raises(ValueError, match="missing required top-level key"):
        amc._load_instance_config(cfg)


# ---------------------------------------------------------------------------
# Validation: duplicate id within a component
# ---------------------------------------------------------------------------

def test_duplicate_id_raises(amc, tmp_path):
    cfg = _write_yaml(tmp_path, """
components:
  authservice:
    - {id: dup}
    - {id: dup}
""")
    with pytest.raises(ValueError, match="duplicate"):
        amc._load_instance_config(cfg)


# ---------------------------------------------------------------------------
# Validation: count exceeds MAX_INSTANCES_PER_COMPONENT
# ---------------------------------------------------------------------------

def test_exceeds_max_instances_raises(amc, tmp_path):
    n = amc.MAX_INSTANCES_PER_COMPONENT + 1
    entries = [{"id": f"i{k}"} for k in range(n)]
    cfg = _write_json(tmp_path, {"components": {"authservice": entries}})
    with pytest.raises(ValueError, match="MAX_INSTANCES_PER_COMPONENT"):
        amc._load_instance_config(cfg)


# ---------------------------------------------------------------------------
# Validation: non-existent file path
# ---------------------------------------------------------------------------

def test_nonexistent_file_rejected(amc, tmp_path):
    with pytest.raises(SystemExit):
        _parse(amc, ["--instance-config", str(tmp_path / "no_such_file.yaml")],
               tmp_path / "out")


# ---------------------------------------------------------------------------
# Validation: unsupported file extension
# ---------------------------------------------------------------------------

def test_unsupported_extension_rejected(amc, tmp_path):
    f = tmp_path / "cfg.toml"
    f.write_text("")
    with pytest.raises(SystemExit):
        _parse(amc, ["--instance-config", str(f)], tmp_path / "out")


# ---------------------------------------------------------------------------
# DST mutual exclusion with --instance-config
# ---------------------------------------------------------------------------

def test_instance_config_dst_mutually_exclusive(amc, tmp_path):
    cfg = _write_yaml(tmp_path, """
components:
  authservice:
    - {id: auth-1, pod: pod-0}
""")
    with pytest.raises(SystemExit):
        _parse(amc, [
            "--instance-config", str(cfg),
            "--inject-dst-artifact-day", "1",
        ], tmp_path / "out")


# ---------------------------------------------------------------------------
# Default behavior unchanged when flag is absent
# ---------------------------------------------------------------------------

def test_default_no_instance_config_unchanged(amc, tmp_path):
    """With no instance flags, parse_args has instance_config=None and instances_per_component=1."""
    args = _parse(amc, [], tmp_path / "out")
    assert args.instance_config is None
    assert args.instances_per_component == 1

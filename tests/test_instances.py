"""Phase 1 multi-instance fan-out — Instance dataclass, INSTANCES
registry, and the generate_component() ``instances`` kwarg.

This phase is intentionally a foundational refactor with no CSV change and no
behavior change. The locked SHA-256 golden hash tests in test_correctness.py,
test_scenarios.py, and test_gauges_file.py guard the byte-level invariant; the
tests below pin the new public surface (dataclass shape, registry coverage,
generate_component kwarg, import-time validator failure modes).
"""

import dataclasses

import numpy as np
import pytest

from conftest import sha256_path


# ---------------------------------------------------------------------------
# Instance dataclass
# ---------------------------------------------------------------------------

def test_instance_constructs_with_all_none_fields(amc):
    """``Instance()`` must construct with every field defaulting to None."""
    inst = amc.Instance()
    assert inst.id is None
    assert inst.host is None
    assert inst.pod is None
    assert inst.az is None
    assert inst.region is None
    assert inst.tenant is None


def test_instance_is_frozen_dataclass(amc):
    """Phase 2 will share Instance objects across the per-run instance map; if
    they were mutable, an anomaly handler could silently mutate the shared
    instance and leak state across components."""
    assert dataclasses.is_dataclass(amc.Instance)
    inst = amc.Instance(id="a")
    with pytest.raises(dataclasses.FrozenInstanceError):
        inst.id = "b"


def test_instance_accepts_all_declared_fields(amc):
    """Locks the Phase-1 dimension vocabulary: id, host, pod, az, region,
    tenant. Phase 2 dimension columns and Phase 3 OTEL attributes both pull
    from this exact set, so any field rename or drop here would silently break
    a downstream phase."""
    inst = amc.Instance(
        id="i-1", host="h-1", pod="p-1", az="us-east-1a",
        region="us-east-1", tenant="tenant-1",
    )
    assert inst.id == "i-1"
    assert inst.host == "h-1"
    assert inst.pod == "p-1"
    assert inst.az == "us-east-1a"
    assert inst.region == "us-east-1"
    assert inst.tenant == "tenant-1"


# ---------------------------------------------------------------------------
# INSTANCES registry
# ---------------------------------------------------------------------------

def test_instances_keys_match_components_exactly(amc):
    """INSTANCES must cover every key of COMPONENTS and only those keys, so
    every generated component has exactly one (or more) instance entry and no
    orphan entries exist for components the run does not emit."""
    assert set(amc.INSTANCES.keys()) == set(amc.COMPONENTS.keys())


def test_instances_default_to_single_anonymous_instance(amc):
    """Phase 1 must preserve today's behavior: each component emits a single
    anonymous Instance() so CSV output is byte-identical."""
    for component, instances in amc.INSTANCES.items():
        assert instances == [amc.Instance()], (
            f"INSTANCES[{component!r}] must be [Instance()] by default; got {instances!r}"
        )


def test_instances_lists_are_non_empty(amc):
    """An empty list would mean the component emits nothing, which is the
    Phase-2 fan-out path's most subtle bug."""
    for component, instances in amc.INSTANCES.items():
        assert len(instances) >= 1, (
            f"INSTANCES[{component!r}] is empty"
        )


# ---------------------------------------------------------------------------
# generate_component() kwarg threading
# ---------------------------------------------------------------------------

def test_generate_component_accepts_instances_kwarg(amc, tmp_path):
    """``generate_component(..., instances=[Instance(id="a")])`` must accept
    the kwarg without raising. Phase 1 does not change CSV output, so the only
    assertion is the call completes and produces the per-component CSV at the
    same path as today."""
    specs = amc.COMPONENTS["authservice"][:2]
    out = tmp_path / "instances_kwarg"
    out.mkdir()
    ts_array, ts_strings = amc._build_timestamp_arrays(5, 1.0)
    ctx = amc.RunContext(rng=np.random.RandomState(42))
    amc.generate_component(
        "authservice",
        specs,
        [],
        base_dir=out,
        total_seconds=5,
        drop_rate=0.0,
        interval=1.0,
        ts_array=ts_array,
        ts_strings=ts_strings,
        ctx=ctx,
        instances=[amc.Instance(id="a")],
    )
    assert (out / "authservice.csv").exists()


def test_generate_component_default_instances_matches_explicit_anonymous(amc, tmp_path):
    """The default value of ``instances`` must produce the same CSV bytes as
    explicitly passing ``[Instance()]``. This pins the Phase-1 promise that
    callers (including main()) get today's output without naming the kwarg."""
    specs = amc.COMPONENTS["authservice"][:2]
    ts_array, ts_strings = amc._build_timestamp_arrays(5, 1.0)

    out_default = tmp_path / "default"
    out_default.mkdir()
    amc.generate_component(
        "authservice", specs, [],
        base_dir=out_default, total_seconds=5, drop_rate=0.0, interval=1.0,
        ts_array=ts_array, ts_strings=ts_strings,
        ctx=amc.RunContext(rng=np.random.RandomState(42)),
    )

    out_explicit = tmp_path / "explicit"
    out_explicit.mkdir()
    amc.generate_component(
        "authservice", specs, [],
        base_dir=out_explicit, total_seconds=5, drop_rate=0.0, interval=1.0,
        ts_array=ts_array, ts_strings=ts_strings,
        ctx=amc.RunContext(rng=np.random.RandomState(42)),
        instances=[amc.Instance()],
    )

    assert sha256_path(out_default / "authservice.csv") == sha256_path(
        out_explicit / "authservice.csv"
    )


# ---------------------------------------------------------------------------
# RunContext.instances field
# ---------------------------------------------------------------------------

def test_run_context_has_instances_field_defaulting_to_empty_dict(amc):
    """Phase 2 plugs the resolved per-run instance map onto RunContext; the
    Phase-1 default must be an empty dict so existing constructions
    ``RunContext(rng=...)`` keep working unchanged."""
    ctx = amc.RunContext(rng=np.random.RandomState(42))
    assert ctx.instances == {}


def test_run_context_instances_is_independent_per_instance(amc):
    """The default_factory must produce a fresh dict per RunContext so two
    runs do not share state via mutation of a class-level default."""
    a = amc.RunContext(rng=np.random.RandomState(42))
    b = amc.RunContext(rng=np.random.RandomState(42))
    a.instances["authservice"] = [amc.Instance(id="ignored")]
    assert b.instances == {}


# ---------------------------------------------------------------------------
# Import-time validator failure modes
# ---------------------------------------------------------------------------

def test_validator_rejects_unknown_component_key(amc, monkeypatch):
    """The validator must reject an INSTANCES key that is not a COMPONENTS key
    so a typo cannot silently drop a real component out of the registry."""
    patched = dict(amc.INSTANCES)
    patched["definitely_not_a_real_component"] = [amc.Instance()]
    monkeypatch.setattr(amc, "INSTANCES", patched)
    with pytest.raises(ValueError, match="definitely_not_a_real_component"):
        amc._validate_instances_registry()


def test_validator_rejects_missing_component_key(amc, monkeypatch):
    """Symmetric to the unknown-key check: every COMPONENTS key must be in
    INSTANCES, so a forgotten entry does not silently turn into an empty
    instance list (which Phase 2's fan-out would interpret as "emit nothing")."""
    patched = {name: insts for name, insts in amc.INSTANCES.items()
               if name != "authservice"}
    monkeypatch.setattr(amc, "INSTANCES", patched)
    with pytest.raises(ValueError, match="authservice"):
        amc._validate_instances_registry()


def test_validator_rejects_empty_instance_list(amc, monkeypatch):
    """An empty list silently produces zero rows for the component under
    Phase-2's fan-out semantics."""
    patched = dict(amc.INSTANCES)
    patched["authservice"] = []
    monkeypatch.setattr(amc, "INSTANCES", patched)
    with pytest.raises(ValueError, match="authservice"):
        amc._validate_instances_registry()


def test_validator_rejects_duplicate_non_none_instance_ids(amc, monkeypatch):
    """Instance ``id``s within one component's list must be unique when set so
    Phase-2 anomaly ``instance_filter`` selectors target a single instance."""
    patched = dict(amc.INSTANCES)
    patched["authservice"] = [amc.Instance(id="dup"), amc.Instance(id="dup")]
    monkeypatch.setattr(amc, "INSTANCES", patched)
    with pytest.raises(ValueError, match="authservice"):
        amc._validate_instances_registry()


def test_validator_allows_one_anonymous_among_named(amc, monkeypatch):
    """``id=None`` is the Phase-1 default, and the spec allows at most one
    anonymous instance per component (multiple Nones would be indistinguishable
    when Phase-2 anomalies route by id). This test pins the at-most-one rule
    so duplicate Nones don't slip through the de-dup logic."""
    patched = dict(amc.INSTANCES)
    patched["authservice"] = [amc.Instance(), amc.Instance(id="a")]
    monkeypatch.setattr(amc, "INSTANCES", patched)
    # one None + one named id is fine
    amc._validate_instances_registry()

    patched["authservice"] = [amc.Instance(), amc.Instance()]
    monkeypatch.setattr(amc, "INSTANCES", patched)
    with pytest.raises(ValueError, match="authservice"):
        amc._validate_instances_registry()


def test_validator_rejects_non_instance_entry(amc, monkeypatch):
    """A non-Instance entry (e.g. a bare dict slipped into the list) must
    raise a clear ValueError naming the component, not a bare AttributeError
    from ``.id`` access mid-validation."""
    patched = dict(amc.INSTANCES)
    patched["authservice"] = [{"id": "not-a-dataclass"}]
    monkeypatch.setattr(amc, "INSTANCES", patched)
    with pytest.raises(ValueError, match="authservice.*non-Instance"):
        amc._validate_instances_registry()


def test_validator_rejects_non_string_instance_id(amc, monkeypatch):
    """An ``Instance.id`` that is neither None nor a string must raise a
    clear ValueError naming the component, not a bare TypeError from set
    membership lookup. ``instance_filter`` Phase 4 looks up ids by string
    equality, so non-string ids would silently never match."""
    patched = dict(amc.INSTANCES)
    patched["authservice"] = [amc.Instance(id=42)]
    monkeypatch.setattr(amc, "INSTANCES", patched)
    with pytest.raises(ValueError, match="authservice.*id must be None or a string"):
        amc._validate_instances_registry()


# ---------------------------------------------------------------------------
# generate_component() instances kwarg per-entry validation
# ---------------------------------------------------------------------------

def _gc_kwargs(amc, tmp_path):
    """Common kwargs for a 5-row authservice run (parametric inputs only —
    each call must still pass its own ``instances`` and a fresh ``ctx``)."""
    out = tmp_path / "instances_validation"
    out.mkdir(exist_ok=True)
    ts_array, ts_strings = amc._build_timestamp_arrays(5, 1.0)
    return dict(
        component_name="authservice",
        specs=amc.COMPONENTS["authservice"][:2],
        anomaly_specs=[],
        base_dir=out,
        total_seconds=5,
        drop_rate=0.0,
        interval=1.0,
        ts_array=ts_array,
        ts_strings=ts_strings,
    )


def test_generate_component_rejects_non_instance_entry(amc, tmp_path):
    """A non-Instance entry in the ``instances`` kwarg must raise a clear
    ValueError naming the call site, not a bare AttributeError once Phases
    2–4 start consuming ``.id`` / dimension fields."""
    kw = _gc_kwargs(amc, tmp_path)
    ctx = amc.RunContext(rng=np.random.RandomState(42))
    with pytest.raises(ValueError, match="generate_component.*non-Instance"):
        amc.generate_component(
            kw["component_name"], kw["specs"], kw["anomaly_specs"],
            base_dir=kw["base_dir"], total_seconds=kw["total_seconds"],
            drop_rate=kw["drop_rate"], interval=kw["interval"],
            ts_array=kw["ts_array"], ts_strings=kw["ts_strings"],
            ctx=ctx,
            instances=[{"id": "not-a-dataclass"}],
        )


def test_generate_component_rejects_non_string_instance_id(amc, tmp_path):
    """An Instance with a non-None, non-string id must be rejected by
    ``generate_component`` with the same message convention the registry
    validator uses."""
    kw = _gc_kwargs(amc, tmp_path)
    ctx = amc.RunContext(rng=np.random.RandomState(42))
    with pytest.raises(ValueError, match="generate_component.*id must be None or a string"):
        amc.generate_component(
            kw["component_name"], kw["specs"], kw["anomaly_specs"],
            base_dir=kw["base_dir"], total_seconds=kw["total_seconds"],
            drop_rate=kw["drop_rate"], interval=kw["interval"],
            ts_array=kw["ts_array"], ts_strings=kw["ts_strings"],
            ctx=ctx,
            instances=[amc.Instance(id=42)],
        )


def test_generate_component_rejects_duplicate_instance_ids(amc, tmp_path):
    """Two Instances sharing the same non-None id must be rejected at the
    call site — Phase 4's ``instance_filter`` lookup by id would silently
    target multiple rows otherwise."""
    kw = _gc_kwargs(amc, tmp_path)
    ctx = amc.RunContext(rng=np.random.RandomState(42))
    with pytest.raises(ValueError, match="generate_component.*duplicate"):
        amc.generate_component(
            kw["component_name"], kw["specs"], kw["anomaly_specs"],
            base_dir=kw["base_dir"], total_seconds=kw["total_seconds"],
            drop_rate=kw["drop_rate"], interval=kw["interval"],
            ts_array=kw["ts_array"], ts_strings=kw["ts_strings"],
            ctx=ctx,
            instances=[amc.Instance(id="dup"), amc.Instance(id="dup")],
        )


def test_generate_component_rejects_multiple_anonymous_instances(amc, tmp_path):
    """Two anonymous (``id=None``) Instances must be rejected — Phase 4's
    id-based anomaly routing cannot distinguish them."""
    kw = _gc_kwargs(amc, tmp_path)
    ctx = amc.RunContext(rng=np.random.RandomState(42))
    with pytest.raises(ValueError, match="generate_component.*anonymous"):
        amc.generate_component(
            kw["component_name"], kw["specs"], kw["anomaly_specs"],
            base_dir=kw["base_dir"], total_seconds=kw["total_seconds"],
            drop_rate=kw["drop_rate"], interval=kw["interval"],
            ts_array=kw["ts_array"], ts_strings=kw["ts_strings"],
            ctx=ctx,
            instances=[amc.Instance(), amc.Instance()],
        )

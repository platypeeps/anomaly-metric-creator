"""Restart continuity for the simulator mutation overlay (`--persist-mutations`).

The overlay is normally in-memory and dies with the process. With the flag set
it round-trips through a JSON file, so these tests pin the three things that
can go wrong: a commit that does not reach disk, a file that half-hydrates,
and a persisted entry that no longer matches the run it is being restored into.
"""

import ast
import dataclasses
import datetime as _dt
import inspect
import json
import re

import pytest

from anomaly_metric_creator import server, server_mutations
from anomaly_metric_creator.server_mutations import (
    MUTATION_STATE_SCHEMA_VERSION,
    SimulationMutations,
    load_persisted_mutations,
)

NOW = _dt.datetime(2026, 8, 26, 12, 0, 0)

KNOWN = frozenset({"apiserver", "cacheservice"})


def _mutate(overlay: SimulationMutations) -> None:
    """Apply one of each mutation shape the overlay models."""
    overlay.set_workload("apiserver", now=NOW, replicas=5, deployment_status="Scaled")
    overlay.delete_pod("apiserver-7d9f-abcde", now=NOW)
    overlay.put_resource(
        "configmap", "feature-flags", {"data": "on"}, now=NOW, namespace="saas-prod"
    )
    overlay.delete_resource("configmap", "legacy-flags", now=NOW)
    overlay.set_revisions([{"revision": 2, "status": "deployed"}], now=NOW)
    overlay.set_release_values({"replicaCount": "5"}, now=NOW)


def test_mutations_survive_a_restart(tmp_path):
    """The whole point: a mutation made in one process is visible in the next."""
    path = tmp_path / "mutations.json"
    first = load_persisted_mutations(path, known_components=KNOWN)
    _mutate(first)
    before = first.summary()

    second = load_persisted_mutations(path, known_components=KNOWN)

    assert second.summary() == before
    assert second.version == first.version


def test_every_commit_reaches_disk_not_only_the_last(tmp_path):
    """Each mutation persists as it commits -- no flush-on-shutdown assumption."""
    path = tmp_path / "mutations.json"
    overlay = load_persisted_mutations(path, known_components=KNOWN)
    overlay.set_workload("apiserver", now=NOW, replicas=3)

    on_disk = json.loads(path.read_text(encoding="utf-8"))

    assert on_disk["schema_version"] == MUTATION_STATE_SCHEMA_VERSION
    assert on_disk["mutations"]["workloads"]["apiserver"]["replicas"] == 3


def test_resource_version_is_never_published_stale(tmp_path):
    """set_workload writes after it stamps resource_version, not before.

    The commit hook runs last in the locked block precisely so the file can
    never record the bumped `version` alongside the previous
    `resource_version` -- a Kubernetes client watching that field would see a
    change it could not account for.
    """
    path = tmp_path / "mutations.json"
    overlay = load_persisted_mutations(path, known_components=KNOWN)
    mutation = overlay.set_workload("apiserver", now=NOW, replicas=3)

    on_disk = json.loads(path.read_text(encoding="utf-8"))

    assert (
        on_disk["mutations"]["workloads"]["apiserver"]["resource_version"]
        == mutation.resource_version
    )


def test_resource_version_matches_the_unpersisted_path(tmp_path):
    """The reordered stamp is byte-identical to the flag-off computation."""
    persisted = load_persisted_mutations(
        tmp_path / "mutations.json", known_components=KNOWN
    )
    in_memory = SimulationMutations()
    for replicas in (3, 4, 5):
        left = persisted.set_workload("apiserver", now=NOW, replicas=replicas)
        right = in_memory.set_workload("apiserver", now=NOW, replicas=replicas)
        assert left.resource_version == right.resource_version
        assert persisted.version == in_memory.version


def test_reset_truncates_the_file_as_well_as_memory(tmp_path):
    """Reset means baseline in both places, or the next restart resurrects it."""
    path = tmp_path / "mutations.json"
    overlay = load_persisted_mutations(path, known_components=KNOWN)
    _mutate(overlay)

    overlay.reset()

    on_disk = json.loads(path.read_text(encoding="utf-8"))["mutations"]
    assert on_disk["workloads"] == {}
    assert on_disk["deleted_pods"] == []
    assert on_disk["extra_events"] == []
    assert on_disk["release"]["revisions"] is None
    assert load_persisted_mutations(path, known_components=KNOWN).summary() == overlay.summary()


def test_flag_off_writes_nothing_and_behaves_identically(tmp_path):
    """The default path must not acquire a filesystem dependency."""
    overlay = SimulationMutations()
    assert overlay.persist_path is None

    _mutate(overlay)

    assert list(tmp_path.iterdir()) == []


def test_unknown_schema_version_is_refused_naming_the_file(tmp_path):
    path = tmp_path / "mutations.json"
    path.write_text(json.dumps({"schema_version": 99, "mutations": {}}), encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        load_persisted_mutations(path, known_components=KNOWN)

    message = str(excinfo.value)
    assert str(path) in message
    assert "99" in message


def test_corrupt_json_is_refused_rather_than_half_hydrated(tmp_path):
    path = tmp_path / "mutations.json"
    path.write_text('{"schema_version": 1, "mutations": {', encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        load_persisted_mutations(path, known_components=KNOWN)

    assert str(path) in str(excinfo.value)


def test_unknown_top_level_key_is_refused(tmp_path):
    """A newer build's field must not be silently dropped on downgrade."""
    path = tmp_path / "mutations.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": MUTATION_STATE_SCHEMA_VERSION,
                "mutations": {"workloads": {}, "future_field": []},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="future_field"):
        load_persisted_mutations(path, known_components=KNOWN)


def test_unknown_workload_field_is_refused(tmp_path):
    path = tmp_path / "mutations.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": MUTATION_STATE_SCHEMA_VERSION,
                "mutations": {"workloads": {"apiserver": {"replicas": 1, "nope": 2}}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="nope"):
        load_persisted_mutations(path, known_components=KNOWN)


def test_stale_component_is_dropped_with_a_warning_naming_it(tmp_path, capsys):
    """A narrowed --components run drops ghosts loudly instead of refusing."""
    path = tmp_path / "mutations.json"
    seed = load_persisted_mutations(path, known_components=KNOWN)
    seed.set_workload("cacheservice", now=NOW, replicas=2)
    seed.delete_pod("cacheservice-1234-abcde", now=NOW)
    capsys.readouterr()

    restored = load_persisted_mutations(path, known_components=frozenset({"apiserver"}))

    stderr = capsys.readouterr().err
    assert "cacheservice" in stderr
    assert "WARNING" in stderr
    assert restored.workloads == {}
    assert restored.deleted_pods == set()


def test_dropped_entries_do_not_survive_a_second_restart(tmp_path, capsys):
    """The post-drop overlay is written back, so the ghost is gone for good."""
    path = tmp_path / "mutations.json"
    seed = load_persisted_mutations(path, known_components=KNOWN)
    seed.set_workload("cacheservice", now=NOW, replicas=2)
    load_persisted_mutations(path, known_components=frozenset({"apiserver"}))
    capsys.readouterr()

    again = load_persisted_mutations(path, known_components=frozenset({"apiserver"}))

    assert again.workloads == {}
    assert "cacheservice" not in capsys.readouterr().err


def test_missing_file_is_the_normal_first_run(tmp_path):
    path = tmp_path / "nested" / "mutations.json"
    path.parent.mkdir()

    overlay = load_persisted_mutations(path, known_components=KNOWN)

    assert overlay.summary() == SimulationMutations().summary()
    assert path.exists()


def test_unclassified_overlay_field_fails_loudly(monkeypatch):
    """A new SimulationMutations field must be classified, not silently lost.

    Serialization is driven by an explicit partition rather than
    `dataclasses.asdict`, so nothing stops a future field from being omitted
    except this check. Exercised by monkeypatching the partition rather than
    the dataclass, which is the same assertion from the other side.
    """
    overlay = SimulationMutations()
    monkeypatch.setattr(
        server_mutations, "_PERSISTED_MUTATION_FIELDS", frozenset({"version"})
    )

    with pytest.raises(RuntimeError, match="not classified"):
        overlay.envelope()


def test_field_partition_matches_the_live_dataclass():
    """The stored partition and the dataclass agree today, without monkeypatching."""
    declared = {f.name for f in dataclasses.fields(SimulationMutations)}
    classified = (
        server_mutations._PERSISTED_MUTATION_FIELDS
        | server_mutations._UNPERSISTED_MUTATION_FIELDS
    )
    assert declared == classified


def test_every_commit_routes_through_the_hook():
    """No mutator may bump `version` by hand and skip the disk write.

    Source-level rather than behavioral: a forgotten hook in a new mutator
    would not fail any of the tests above, because none of them know the new
    mutator exists.
    """
    tree = ast.parse(inspect.getsource(SimulationMutations))
    bumps = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.AugAssign)
                and isinstance(inner.target, ast.Attribute)
                and inner.target.attr == "version"
                and isinstance(inner.target.value, ast.Name)
                and inner.target.value.id == "self"
            ):
                bumps.append(node.name)
    assert bumps == ["_commit_locked"], (
        "every overlay commit must go through _commit_locked so the version "
        f"bump and the persistence write stay together; found bumps in {bumps}"
    )


def test_persistence_uses_the_shared_atomic_writer():
    """CLAUDE.md forbids open(final_path, 'w') for any published artifact."""
    source = inspect.getsource(server_mutations)
    assert "_atomic_write_text(self.persist_path" in source
    assert not re.search(r"open\(\s*self\.persist_path", source)


def test_persist_mutations_is_reachable_from_the_config_allowlist():
    """A serve flag absent from the allowlist is rejected by --config."""
    assert "persist_mutations" in server._SERVE_CONFIG_SERVER_KEYS


def test_persist_mutations_flag_parses_to_a_path(tmp_path):
    serve_args, _ = server._parse_serve_args(
        ["--persist-mutations", str(tmp_path / "m.json"), "--no-generate"],
        server._build_serve_parser(),
    )
    assert serve_args.persist_mutations == tmp_path / "m.json"


def test_persist_mutations_defaults_to_off():
    serve_args, _ = server._parse_serve_args(
        ["--no-generate"], server._build_serve_parser()
    )
    assert serve_args.persist_mutations is None

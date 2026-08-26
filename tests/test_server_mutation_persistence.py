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
from pathlib import Path

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


def test_flag_off_writes_nothing_and_behaves_identically(tmp_path, monkeypatch):
    """The default path must not acquire a filesystem dependency.

    Asserting only that ``tmp_path`` stayed empty would pass vacuously: the
    flag-off overlay never names that directory, so a regression writing
    anywhere else would go unseen. Watch the writer itself, and run from
    ``tmp_path`` so a relative write has somewhere observable to land.
    """
    monkeypatch.chdir(tmp_path)
    writes: list[Path] = []
    monkeypatch.setattr(
        server_mutations,
        "_atomic_write_text",
        lambda path, text: writes.append(Path(path)),
    )

    overlay = SimulationMutations()
    assert overlay.persist_path is None

    _mutate(overlay)

    # Two independent observations, so neither alone has to be trusted: the
    # shared writer is never reached, and nothing appeared on disk by any
    # other route either.
    assert writes == []
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


def test_unknown_mutations_key_is_refused(tmp_path):
    """A newer build's overlay field must not be silently dropped on downgrade.

    Named for where the key actually sits. This one is inside ``mutations``;
    the envelope's own top level is a separate surface with its own check,
    covered below -- conflating them once left that second surface untested
    while reading as though it were covered.
    """
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


def test_unknown_envelope_key_is_refused(tmp_path):
    """The envelope's top level must refuse too, not only ``mutations``."""
    path = tmp_path / "mutations.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": MUTATION_STATE_SCHEMA_VERSION,
                "mutations": {},
                "future_envelope_field": 1,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as excinfo:
        load_persisted_mutations(path, known_components=KNOWN)

    message = str(excinfo.value)
    assert str(path) in message
    assert "future_envelope_field" in message


def test_unsupported_version_wins_over_an_unknown_envelope_key(tmp_path):
    """A future file trips both checks; the version message is the useful one.

    It names what to do about the file. Leading with whichever unknown key
    sorted first would bury that.
    """
    path = tmp_path / "mutations.json"
    path.write_text(
        json.dumps({"schema_version": 99, "mutations": {}, "future_envelope_field": 1}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="schema_version"):
        load_persisted_mutations(path, known_components=KNOWN)


def test_envelope_keys_match_the_declared_set():
    """The writer and the downgrade check must not drift apart."""
    overlay = SimulationMutations()

    assert set(overlay.envelope()) == server_mutations._PERSISTED_ENVELOPE_KEYS


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


def _write_overlay(path: Path, mutations: dict) -> None:
    path.write_text(
        json.dumps({"schema_version": MUTATION_STATE_SCHEMA_VERSION, "mutations": mutations}),
        encoding="utf-8",
    )


# Each wrong type here is iterable, which is the whole hazard: unguarded, a
# dict would be read as its keys and a string as its characters, so the file
# would load and quietly mean something else.
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("deleted_pods", {"apiserver-7d9f-abcde": True}),
        ("deleted_pods", "apiserver-7d9f-abcde"),
        ("extra_events", {"a": 1}),
    ],
)
def test_array_fields_refuse_a_non_array(tmp_path, field, value):
    path = tmp_path / "mutations.json"
    _write_overlay(path, {field: value})

    with pytest.raises(ValueError) as excinfo:
        load_persisted_mutations(path, known_components=KNOWN)

    message = str(excinfo.value)
    assert str(path) in message
    assert f"mutations.{field}" in message
    assert "JSON array" in message


def test_deleted_resource_names_refuse_a_non_array(tmp_path):
    path = tmp_path / "mutations.json"
    _write_overlay(path, {"deleted_resources": {"configmap": {"legacy-flags": True}}})

    with pytest.raises(ValueError, match="JSON array"):
        load_persisted_mutations(path, known_components=KNOWN)


# `int()` coerces rather than validates, and `bool` is a subclass of `int`,
# so `True` would have loaded as version 1 and `3.9` as version 3.
@pytest.mark.parametrize("value", [True, 3.9, "5", None, [1]])
def test_version_refuses_anything_but_a_plain_integer(tmp_path, value):
    path = tmp_path / "mutations.json"
    _write_overlay(path, {"version": value})

    with pytest.raises(ValueError) as excinfo:
        load_persisted_mutations(path, known_components=KNOWN)

    message = str(excinfo.value)
    assert str(path) in message
    assert "mutations.version must be an integer" in message


def test_version_refuses_a_negative_integer(tmp_path):
    path = tmp_path / "mutations.json"
    _write_overlay(path, {"version": -1})

    with pytest.raises(ValueError, match="must not be negative"):
        load_persisted_mutations(path, known_components=KNOWN)


def test_version_accepts_a_plain_integer(tmp_path):
    """The guard must not reject the value it exists to protect."""
    path = tmp_path / "mutations.json"
    _write_overlay(path, {"version": 7})

    assert load_persisted_mutations(path, known_components=KNOWN).version == 7


# The loader arms persistence on both routes, and each writes immediately.
# The missing-file first run is the likelier operator error of the two --
# `--persist-mutations /no/such/dir/mutations.json` -- so neither is enough
# on its own.
@pytest.mark.parametrize("existing", [False, True], ids=["first-run", "after-hydration"])
def test_unwritable_target_refuses_naming_the_file_rather_than_tracing(
    tmp_path, monkeypatch, existing
):
    """Arming persistence writes immediately, and that write can fail.

    `serve_main` refuses on ValueError alone, so an OSError escaping the
    loader would reach the operator as a traceback instead of the documented
    refusal naming the path.
    """
    path = tmp_path / "mutations.json"
    if existing:
        _write_overlay(path, {"version": 3})

    def _refuse(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(server_mutations, "_atomic_write_text", _refuse)

    with pytest.raises(ValueError) as excinfo:
        load_persisted_mutations(path, known_components=KNOWN)

    message = str(excinfo.value)
    assert str(path) in message
    assert "could not be written" in message


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


def _is_self_version(target: ast.expr) -> bool:
    return (
        isinstance(target, ast.Attribute)
        and target.attr == "version"
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    )


def _version_bump_functions(source: str) -> list[str]:
    """Names of functions in ``source`` that assign ``self.version``.

    Every assignment form counts. Matching only ``+=`` would let
    ``self.version = self.version + 1`` -- the same hand-rolled bump, written
    the long way -- walk straight past a guard whose whole purpose is to catch
    it. Shared with the meta-test below so the guard's own blind spots are
    testable rather than assumed.
    """
    tree = ast.parse(source)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.AugAssign) and _is_self_version(inner.target):
                found.append(node.name)
            elif isinstance(inner, ast.Assign) and any(
                # Walk into the target rather than testing it directly:
                # `self.version, other = ...` presents one `ast.Tuple`, so a
                # direct test sees the tuple and never the element inside it.
                _is_self_version(sub)
                for target in inner.targets
                for sub in ast.walk(target)
            ):
                found.append(node.name)
            elif (
                isinstance(inner, ast.AnnAssign)
                and inner.value is not None
                and _is_self_version(inner.target)
            ):
                found.append(node.name)
    return found


@pytest.mark.parametrize(
    "statement",
    [
        "self.version += 1",
        "self.version = self.version + 1",
        "self.version: int = self.version + 1",
        "self.version, other = self.version + 1, 2",
    ],
)
def test_the_commit_guard_sees_every_bump_form(statement):
    """The guard must not be evadable by rewriting the bump.

    `+=` was once the only form it matched, so the long-hand equivalent went
    unseen -- a guard with a blind spot reads exactly like a guard without
    one.
    """
    source = f"class Fake:\n    def sneaky(self):\n        {statement}\n"

    assert _version_bump_functions(source) == ["sneaky"]


def test_the_commit_guard_ignores_unrelated_assignments():
    """It must stay specific, or it would flag every nearby write."""
    source = (
        "class Fake:\n"
        "    def innocent(self):\n"
        "        self.other = 1\n"
        "        other.version = 2\n"
        "        version = 3\n"
    )

    assert _version_bump_functions(source) == []


def test_every_commit_routes_through_the_hook():
    """No mutator may bump `version` by hand and skip the disk write.

    Source-level rather than behavioral: a forgotten hook in a new mutator
    would not fail any of the tests above, because none of them know the new
    mutator exists.
    """
    bumps = _version_bump_functions(inspect.getsource(SimulationMutations))
    assert bumps == ["_commit_locked"], (
        "every overlay commit must go through _commit_locked so the version "
        f"bump and the persistence write stay together; found bumps in {bumps}"
    )


def test_persistence_uses_the_shared_atomic_writer():
    """CLAUDE.md forbids open(final_path, 'w') for any published artifact."""
    source = inspect.getsource(server_mutations)
    # Tolerate line breaks between the call and its first argument. The fact
    # under guard is that the shared writer is what receives `persist_path`,
    # not how the call happens to be wrapped -- a formatter moving the
    # argument to the next line is not a violation, and a guard that treats
    # it as one gets edited away the first time it cries wolf.
    assert re.search(r"_atomic_write_text\(\s*self\.persist_path", source)
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

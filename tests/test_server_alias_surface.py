"""Coverage for the ``server`` -> ``server_ops`` compatibility surface.

``server.py`` used to publish the historic ``anomaly_metric_creator.server``
import surface with 227 hand-written ``NAME = _server_ops.NAME`` lines, one per
name, which every ``server_ops`` extraction step had to append to. That block is
now a module ``__getattr__`` plus an explicit import of the names that cannot be
delegated. These tests pin the three properties that swap depends on.
"""

from __future__ import annotations

import ast
import builtins
import inspect
from pathlib import Path

import pytest

from anomaly_metric_creator import server, server_ops


def _server_source() -> str:
    source_file = inspect.getsourcefile(server)
    assert source_file is not None
    return Path(source_file).read_text(encoding="utf-8")


def _ops_public_and_private_names() -> list[str]:
    """Every ``server_ops`` attribute the compatibility surface should carry.

    Derived from the live module rather than a stored list: ``server_ops`` and
    its ``__all__`` disagree by seven names in each direction, so a frozen copy
    of either would be wrong the moment an extraction lands.
    """
    return [name for name in dir(server_ops) if not name.startswith("__")]


def test_every_server_ops_name_resolves_through_server():
    names = _ops_public_and_private_names()
    # Sanity-check the derivation itself: a `dir()` that came back nearly empty
    # would make every assertion below vacuously true.
    assert len(names) > 200, f"suspiciously small server_ops surface: {len(names)}"
    for name in names:
        assert getattr(server, name) is getattr(server_ops, name), name


def test_unknown_attribute_raises_attribute_error():
    with pytest.raises(AttributeError) as excinfo:
        server.definitely_not_a_real_ops_name
    message = str(excinfo.value)
    assert "anomaly_metric_creator.server" in message
    assert "definitely_not_a_real_ops_name" in message


def test_dunder_names_are_not_delegated():
    """``server`` must not inherit ``server_ops.__all__``.

    ``server.py`` defines no ``__all__``, so ``from ... import *`` publishes its
    public globals. An unguarded delegation would silently replace that with
    ``server_ops``'s 227-name list, which is a star-import contract change that
    no test would otherwise notice.
    """
    assert hasattr(server_ops, "__all__")
    assert not hasattr(server, "__all__")
    with pytest.raises(AttributeError):
        server.__all__


def test_dir_includes_delegated_and_explicit_names():
    listed = dir(server)
    # `build_state` is bound explicitly; `_render_helm_status` is delegated.
    assert "build_state" in listed
    assert "_render_helm_status" in listed
    assert listed == sorted(listed)


def test_explicit_binds_cover_every_internal_use():
    """The guard against a ``NameError`` on a request path.

    PEP 562's module ``__getattr__`` answers attribute access on the module
    object; it is never consulted for global-name resolution inside the module.
    So any ``server_ops`` name that ``server.py`` reads as a bare global has to
    be a real global here. Delegating one instead fails at request time, on the
    single path that reads it, rather than at import.
    """
    tree = ast.parse(_server_source())

    assigned: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            assigned.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            assigned.add(node.name)
        elif isinstance(node, ast.arg):
            assigned.add(node.arg)
        elif isinstance(node, ast.alias):
            assigned.add((node.asname or node.name).split(".")[0])

    builtin_names = set(dir(builtins))
    module_globals = vars(server)

    unbound = sorted(
        {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id not in assigned
            and node.id not in builtin_names
            and hasattr(server_ops, node.id)
            and node.id not in module_globals
        }
    )
    assert unbound == [], (
        "server.py reads these server_ops names as bare globals but they are "
        f"only delegated: {unbound}. Import them explicitly in the "
        "compatibility block."
    )

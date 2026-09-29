#!/usr/bin/env python3
"""Guard the local merge-gate contract.

The merge gate runs on this machine, not on GitHub Actions. ``sd-ship merge``
runs ``make check`` and posts ``sd/local-gate``, the one required check. The
gate is spread across a few files:

* ``Makefile`` routes ``make check`` to ``scripts/check.sh``.
* ``scripts/check.sh`` holds every gate step, in order.
* ``.pre-commit-config.yaml`` holds the hooks ``check.sh`` runs with
  ``pre-commit run --all-files``.

This checker catches accidental removal of the gate's named anchors, and a
``tools/check_*.py`` lint that nothing runs. A lint counts as reachable when
``.pre-commit-config.yaml`` names it, or when another file under ``scripts/``
or ``tools/`` invokes it (``check.sh``, ``pr_comment.sh``). The inventory is
enumerated from disk, never from a hand-maintained list.

It is deliberately text-based and stdlib-only so pre-commit can run it without
installing project dependencies or parsing YAML.

Exit codes:

* ``0`` - contract anchors are present and every lint is reachable.
* ``1`` - at least one contract violation was found.
* ``2`` - argument or I/O error.
"""

from __future__ import annotations

import sys
from pathlib import Path


REQUIRED_FILES = {
    "makefile": Path("Makefile"),
    "check": Path("scripts/check.sh"),
    "precommit": Path(".pre-commit-config.yaml"),
}

TOOLS_DIR = Path("tools")
INVOKER_DIRS = (Path("scripts"), Path("tools"))

# Shell syntax coverage is derived, not pinned: check.sh enumerates from the
# git index, while pre-commit matches a path pattern and is handed the
# filenames. Both share the parse anchor, because the selection alone would
# still pass if the `bash -n` consuming it were deleted.
_SHELL_SYNTAX_PARSE = 'bash -n "$script"'
# scripts/update_repomix is bash with no .sh suffix, so both selectors name it.
_CHECK_SHELL_SYNTAX_GLOB = "tracked 'scripts/*.sh' scripts/update_repomix"
_PRECOMMIT_SHELL_SYNTAX_FILES = r"files: ^scripts/(.*\.sh|update_repomix)$"
# `set -e` is load-bearing in pre-commit: the entry is a plain `bash -c`, so
# without it the loop exits with the status of the last `bash -n` and a broken
# early script is masked.
_PRECOMMIT_SHELL_SYNTAX_FAILFAST = "bash -c 'set -e;"
_PRECOMMIT_PYTHON_SYNTAX_FILES = (
    r"files: ^(scripts|src|tests|tools|\.codex/hooks|\.github/copilot/hooks|"
    r"\.gemini/hooks)/.*\.py$"
)


def _read(path: Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"
    except UnicodeError as exc:
        return None, f"cannot decode {path}: {exc}"


def _require_contains(
    text: str,
    needle: str,
    *,
    path: Path,
    label: str,
    violations: list[str],
) -> None:
    normalized_text = " ".join(text.split())
    normalized_needle = " ".join(needle.split())
    if needle not in text and normalized_needle not in normalized_text:
        violations.append(f"{path}: missing {label}: {needle!r}")


def _check_makefile(path: Path, text: str, violations: list[str]) -> None:
    _require_contains(
        text,
        "bash scripts/check.sh",
        path=path,
        label="make check entry",
        violations=violations,
    )


def _check_gate_script(path: Path, text: str, violations: list[str]) -> None:
    for label, needle in [
        ("fail-fast shell options", "set -euo pipefail"),
        ("locked dependency sync", "uv sync --extra dev --locked"),
        ("shell syntax coverage", _CHECK_SHELL_SYNTAX_GLOB),
        ("shell syntax parse", _SHELL_SYNTAX_PARSE),
        ("Python syntax guard", "tools/check_python_syntax.py"),
        ("ruff lockstep guard", "tools/check_ruff_lockstep.py"),
        ("clean-module mypy gate", "tools/check_mypy_gate.py"),
        ("heavy test partition", "-m heavy"),
        ("light test partition", '-m "not heavy"'),
        ("coverage threshold", "coverage report --fail-under="),
        ("pre-commit over the whole tree", "pre-commit run --all-files"),
        ("review preflight", "node scripts/check-review-preflight.mjs"),
        ("real client smoke opt-in", "AMC_RUN_REAL_CLIENT_SMOKE=1"),
        ("real kubectl smoke", "test_real_kubectl_binary_smoke_when_available"),
        ("real Helm smoke", "test_real_helm4_binary_smoke_when_available"),
        ("Socket dependency scan", "socketcli --target-path"),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)


def _check_precommit(path: Path, text: str, violations: list[str]) -> None:
    for label, needle in [
        ("local gate contract hook", "id: local-gate-contract"),
        ("local gate contract entry", "python tools/check_local_gate_contract.py"),
        ("test-resource-cost hook", "id: test-resource-cost"),
        (
            "test-resource-cost hook entry",
            "python tools/check_test_resource_cost.py",
        ),
        ("Copilot instruction contract hook", "id: copilot-instruction-contract"),
        ("Copilot instruction contract entry", "tools/check_copilot_instruction_contract.py"),
        ("role-name commit-message hook", "id: role-name-commit-message"),
        ("commit-message hook stage", "stages: [commit-msg]"),
        ("pre-commit shell syntax coverage", _PRECOMMIT_SHELL_SYNTAX_FILES),
        ("pre-commit shell syntax parse entry", _SHELL_SYNTAX_PARSE),
        ("pre-commit shell syntax fail-fast", _PRECOMMIT_SHELL_SYNTAX_FAILFAST),
        ("pre-commit Python syntax coverage", _PRECOMMIT_PYTHON_SYNTAX_FILES),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)


def _check_lint_reachability(
    root: Path, precommit: str, violations: list[str], errors: list[str]
) -> None:
    lints = sorted((root / TOOLS_DIR).glob("check_*.py"))
    invokers: dict[Path, str] = {}
    for directory in INVOKER_DIRS:
        if not (root / directory).is_dir():
            continue
        for path in sorted((root / directory).iterdir()):
            if not path.is_file():
                continue
            text, error = _read(path)
            if error is not None:
                errors.append(error)
            elif text is not None:
                invokers[path] = text
    for lint in lints:
        name = f"{TOOLS_DIR.as_posix()}/{lint.name}"
        if name in precommit:
            continue
        if any(name in text for path, text in invokers.items() if path != lint):
            continue
        violations.append(
            f"{root / name}: lint runs nowhere; add a pre-commit hook or a "
            "step in scripts/check.sh"
        )


def check(root: Path) -> tuple[int, list[str]]:
    texts: dict[str, str] = {}
    errors: list[str] = []
    for key, relative in REQUIRED_FILES.items():
        text, error = _read(root / relative)
        if error is not None:
            errors.append(error)
        elif text is not None:
            texts[key] = text

    if errors:
        return 2, errors

    violations: list[str] = []
    _check_makefile(root / REQUIRED_FILES["makefile"], texts["makefile"], violations)
    _check_gate_script(root / REQUIRED_FILES["check"], texts["check"], violations)
    _check_precommit(root / REQUIRED_FILES["precommit"], texts["precommit"], violations)
    _check_lint_reachability(root, texts["precommit"], violations, errors)

    if errors:
        return 2, errors
    if violations:
        return 1, violations
    return 0, []


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        print("usage: check_local_gate_contract.py [repo-root]", file=sys.stderr)
        return 2

    root = Path(argv[1]) if len(argv) == 2 else Path.cwd()
    root = root.resolve()
    status, messages = check(root)
    if messages:
        print("\n".join(messages), file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv))

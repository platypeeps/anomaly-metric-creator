"""Acceptance tests for ``tools/check_scope_heading_mirrors.py``.

The synthetic trees copy the real authority script rather than a stub, so the
alias and Markdown-prefix cases exercise the same ``_body_has_heading`` the
guard uses in production. A stub would let this file drift from the matcher it
claims to test -- the exact failure the lint exists to prevent.

Since the thin conversion that script is not in this repository; it lives
wherever the machine keeps the pack install. These tests ask the same layout
resolver the guard asks, and skip when nothing answers -- a CI runner has no
install, and there is no real authority to copy into a synthetic tree there.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_scope_heading_mirrors.py"
LAYOUT_RESOLVER = REPO_ROOT / ".sd-ai-command-pack/bin/sd-ai-command-pack-review-layout.py"


def _installed(name: str) -> Path | None:
    """The pack file the machine install provides, or None if there is none."""
    if not LAYOUT_RESOLVER.is_file():
        return None
    result = subprocess.run(
        [sys.executable, str(LAYOUT_RESOLVER), "--resolve", name],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    try:
        resolved = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    path = resolved.get("path") if isinstance(resolved, dict) else None
    if not isinstance(path, str) or not path:
        return None
    candidate = Path(path)
    return candidate if candidate.is_file() else None


AUTHORITY = _installed("sd-ai-command-pack-pr-body-scope.py")
AUTHORITY_LIB = _installed("sd_ai_command_pack_lib.py")
SCOPE_CONFIG = REPO_ROOT / ".sd-ai-command-pack" / "pr-body-scope.json"

pytestmark = pytest.mark.skipif(
    AUTHORITY is None or AUTHORITY_LIB is None,
    reason="no resolvable sd-ai-command-pack install provides the PR-body scope authority",
)

MIRRORS = (
    ".github/PULL_REQUEST_TEMPLATE.md",
    "docs/DEVELOPMENT_CYCLE.md",
    ".github/copilot-instructions.md",
    ".github/instructions/anomaly-metric-creator.instructions.md",
    ".trellis/spec/amc/backend/documentation-review.md",
)

CANONICAL = (
    "Tooling/generated scope:",
    "Automation scope:",
    "CI/review scope:",
    "Docs/user-facing scope:",
    "Runtime/server scope:",
)

ALL_FIVE = "\n".join(CANONICAL) + "\n"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tree(tmp_path: Path) -> Path:
    """A minimal repository the lint can run against end to end."""
    root = tmp_path / "repo"
    _write(root / "scripts" / AUTHORITY.name, AUTHORITY.read_text(encoding="utf-8"))
    _write(
        root / "scripts" / AUTHORITY_LIB.name,
        AUTHORITY_LIB.read_text(encoding="utf-8"),
    )
    _write(
        root / ".sd-ai-command-pack" / SCOPE_CONFIG.name,
        SCOPE_CONFIG.read_text(encoding="utf-8"),
    )
    for mirror in MIRRORS:
        _write(root / mirror, ALL_FIVE)
    return root


def test_live_repository_mirrors_are_current() -> None:
    """Every real mirror names every heading the real authority recognizes."""
    result = _run("--root", str(REPO_ROOT))
    assert result.returncode == 0, result.stderr


def test_live_repository_list_reports_every_mirror() -> None:
    result = _run("--root", str(REPO_ROOT), "--list")
    assert result.returncode == 0, result.stdout + result.stderr
    for mirror in MIRRORS:
        assert mirror in result.stdout


def test_live_repository_mirror_list_matches_this_file() -> None:
    """The lint's MIRRORS and this file's copy cannot drift apart silently."""
    listed = (REPO_ROOT / "tools" / "check_scope_heading_mirrors.py").read_text(
        encoding="utf-8"
    )
    for mirror in MIRRORS:
        assert f'"{mirror}"' in listed


def test_complete_tree_is_clean(tmp_path: Path) -> None:
    result = _run("--root", str(_tree(tmp_path)))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("heading", CANONICAL)
def test_missing_canonical_heading_fails(tmp_path: Path, heading: str) -> None:
    root = _tree(tmp_path)
    remaining = [line for line in CANONICAL if line != heading]
    _write(root / "docs/DEVELOPMENT_CYCLE.md", "\n".join(remaining) + "\n")

    result = _run("--root", str(root))
    assert result.returncode == 1
    assert "docs/DEVELOPMENT_CYCLE.md" in result.stderr
    assert heading in result.stderr


def test_every_mirror_is_checked(tmp_path: Path) -> None:
    """Dropping a heading from any mirror is caught, not just the first."""
    for mirror in MIRRORS:
        root = _tree(tmp_path / mirror.replace("/", "_"))
        _write(root / mirror, "\n".join(CANONICAL[:-1]) + "\n")
        result = _run("--root", str(root))
        assert result.returncode == 1, f"{mirror} was not checked"
        assert mirror in result.stderr


def test_invented_heading_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    path = root / ".github/copilot-instructions.md"
    _write(path, ALL_FIVE + "Start with `Explicit doc scope:` and you are fine.\n")

    result = _run("--root", str(root))
    assert result.returncode == 1
    assert "Explicit doc scope:" in result.stderr
    assert "does not recognize it" in result.stderr


@pytest.mark.parametrize(
    "token",
    [
        "Docs scope:",  # documented alias
        "Workflow scope:",  # documented alias of a different rule
        "> Docs scope:",  # blockquote prefix
        "### Runtime/server scope:",  # heading prefix
        "- Automation scope:",  # list prefix
        "## Tooling/generated scope",  # colon is optional
        "docs/user-facing SCOPE:",  # case-insensitive
    ],
)
def test_authority_accepted_forms_pass(tmp_path: Path, token: str) -> None:
    root = _tree(tmp_path)
    _write(root / ".github/copilot-instructions.md", ALL_FIVE + f"See `{token}`.\n")

    result = _run("--root", str(root))
    assert result.returncode == 0, result.stderr


def test_exemption_is_keyed_to_its_file(tmp_path: Path) -> None:
    """The DEVELOPMENT_CYCLE.md counter-example does not exempt other files."""
    root = _tree(tmp_path)
    _write(root / "docs/DEVELOPMENT_CYCLE.md", ALL_FIVE + "Bad: `Explicit doc scope`\n")
    clean = _run("--root", str(root))
    assert clean.returncode == 0, clean.stderr

    _write(
        root / ".github/copilot-instructions.md",
        ALL_FIVE + "Bad: `Explicit doc scope`\n",
    )
    result = _run("--root", str(root))
    assert result.returncode == 1
    assert ".github/copilot-instructions.md" in result.stderr
    assert "docs/DEVELOPMENT_CYCLE.md" not in result.stderr


def test_named_paths_select_only_those_mirrors(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root / "docs/DEVELOPMENT_CYCLE.md", "\n".join(CANONICAL[:-1]) + "\n")

    ignored = _run("--root", str(root), ".github/copilot-instructions.md")
    assert ignored.returncode == 0, ignored.stderr

    selected = _run("--root", str(root), "docs/DEVELOPMENT_CYCLE.md")
    assert selected.returncode == 1
    assert "docs/DEVELOPMENT_CYCLE.md" in selected.stderr


def test_non_mirror_paths_are_ignored(tmp_path: Path) -> None:
    """The pre-commit hook may pass any changed file; only mirrors are read."""
    root = _tree(tmp_path)
    _write(root / "docs/DEVELOPMENT_CYCLE.md", "\n".join(CANONICAL[:-1]) + "\n")

    result = _run("--root", str(root), "src/anomaly_metric_creator/legacy.py")
    assert result.returncode == 0, result.stderr


def test_tree_without_an_authority_is_skipped_not_failed(tmp_path: Path) -> None:
    """A tree that provides no authority reports a skip, not a violation.

    This is what a CI runner looks like since the thin conversion: no vendored
    copy under ``scripts/`` and no layout resolver to point at a machine
    install. Failing there would fail on a file nobody shipped to the runner.
    """
    root = _tree(tmp_path)
    (root / "scripts" / AUTHORITY.name).unlink()

    result = _run("--root", str(root))
    assert result.returncode == 0, result.stderr
    assert "skipped" in result.stdout


def test_missing_mirror_is_structural(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "docs/DEVELOPMENT_CYCLE.md").unlink()

    result = _run("--root", str(root))
    assert result.returncode == 2
    assert "mirror not found" in result.stderr


def test_unparsable_scope_config_is_structural(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root / ".sd-ai-command-pack" / SCOPE_CONFIG.name, "{ not json")

    result = _run("--root", str(root))
    assert result.returncode == 2
    assert "configuration error" in result.stderr


def test_config_added_category_must_reach_the_mirrors(tmp_path: Path) -> None:
    """A new category in the config is a violation until the mirrors say so.

    This is the drift the guard exists for: the authority gains a heading and
    every prose description silently keeps enumerating the old set.
    """
    root = _tree(tmp_path)
    _write(
        root / ".sd-ai-command-pack" / SCOPE_CONFIG.name,
        '{"rules": [{"label": "Security scope",'
        ' "headings": ["Security scope:"],'
        ' "patterns": ["SECURITY.md"]}]}\n',
    )

    result = _run("--root", str(root))
    assert result.returncode == 1
    assert "Security scope:" in result.stderr
    # Every mirror is behind, not just the one that happens to be read first.
    for mirror in MIRRORS:
        assert mirror in result.stderr

    for mirror in MIRRORS:
        _write(root / mirror, ALL_FIVE + "Security scope:\n")
    assert _run("--root", str(root)).returncode == 0

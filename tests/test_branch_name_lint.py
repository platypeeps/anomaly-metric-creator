"""Acceptance tests for `tools/check_branch_name.py`.

The lint forbids branch names that leak the project's ticket
literal (``ver-NNN`` / ``VER-NNN``) so feature branches do not
republish internal ticket identifiers through PR head refs the way
PRs #47–#77 and #86 did. The structural fix is a pre-push hook (or
equivalent guard) that rejects any branch whose name matches
``(?i)(^|\\b)ver-\\d+``.

Pin the behaviors the script promises in its docstring so a future
edit cannot silently weaken the guardrail:

- regex anchored to start-of-string OR a ``\\b`` word boundary so
  ``fever-pitch`` and ``discover-foo`` stay legal;
- case-insensitive so ``ver-655``, ``VER-655``, and ``Ver-655`` are
  all rejected;
- requires a digit immediately after the dash so generic ``ver-``
  prefixes without a ticket number (e.g. ``verify-thing``) stay
  legal;
- positional-arg mode accepts one or more literal branch names;
- ``--current`` mode reads the current branch via
  ``git symbolic-ref --short HEAD``;
- ``-`` stdin mode parses git's pre-push protocol lines
  ``<local-ref> <local-sha> <remote-ref> <remote-sha>`` and checks
  each non-deleted local ref's short name;
- exit codes ``0`` clean / ``1`` leaked branch / ``2`` argument or
  I/O error;
- only the "Branch names must not embed" footer prints when an
  actual match fires.

Mirrors the layout of ``tests/test_role_name_leaks_lint.py`` and
``tests/test_amc_module_load_lint.py`` so the three guardrail
lints stay structurally parallel.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_branch_name.py"


def _run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
    )


def test_script_exists():
    assert SCRIPT.is_file(), f"{SCRIPT} not found"


# ---------------------------------------------------------------------------
# Positional-arg mode: literal branch names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "ver-655-refresh-mermaid-diagrams",
        "ver-1",
        "VER-655-foo",
        "Ver-42",
        "sdelmas/ver-655-refresh-mermaid-diagrams",
        "feature/VER-7-foo",
        "user/ver-100",
    ],
)
def test_leaking_branch_name_exits_one(name: str):
    result = _run(name)
    assert result.returncode == 1, result.stderr
    assert name in result.stderr


@pytest.mark.parametrize(
    "name",
    [
        "main",
        "develop",
        "feature/role-name-lint",
        "reject-leaking-branch-names",
        "fever-pitch",  # 'ver' inside a word — no boundary before
        "discover-feature-7",  # 'ver-' inside a word — no boundary
        "verify-something",  # 'ver' as prefix but no '-' then digit
        "version-bump",  # 'ver' prefix; not 'ver-' + digit
        "ver-test",  # 'ver-' but no digit follows
        "feat-foo-ver-",  # trailing 'ver-' with no digit
    ],
)
def test_clean_branch_name_exits_zero(name: str):
    result = _run(name)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_footer_prints_on_match():
    result = _run("ver-42")
    assert result.returncode == 1
    assert "Branch names must not embed" in result.stderr


def test_footer_does_not_print_on_clean_run():
    result = _run("feature/clean-name")
    assert "Branch names must not embed" not in result.stderr


def test_multiple_branches_aggregate():
    """Reports every leaking branch in a single run; does not
    short-circuit on the first violation."""
    result = _run("ver-1", "clean-name", "VER-77")
    assert result.returncode == 1
    assert "ver-1" in result.stderr
    assert "VER-77" in result.stderr
    assert "clean-name" not in result.stderr


def test_no_args_returns_2():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, result.stderr
    assert "usage" in result.stderr.lower()


# ---------------------------------------------------------------------------
# --current mode (reads `git symbolic-ref --short HEAD`)
# ---------------------------------------------------------------------------


def _init_repo_with_branch(tmp_path: Path, branch: str) -> Path:
    """Bootstrap a throwaway git repo on ``branch`` so ``--current``
    has a real ref to read. The initial-commit + ``checkout -b`` dance
    works regardless of the host's ``init.defaultBranch`` setting."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--allow-empty", "-q", "-m", "init"],
        cwd=repo, check=True,
    )
    subprocess.run(["git", "checkout", "-q", "-b", branch], cwd=repo, check=True)
    return repo


def test_current_mode_rejects_leaking_branch(tmp_path: Path):
    repo = _init_repo_with_branch(tmp_path, "ver-702-foo")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--current"],
        cwd=repo, capture_output=True, text=True,
    )
    assert result.returncode == 1, result.stderr
    assert "ver-702-foo" in result.stderr


def test_current_mode_accepts_clean_branch(tmp_path: Path):
    repo = _init_repo_with_branch(tmp_path, "feature/clean")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--current"],
        cwd=repo, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_current_mode_detached_head_exits_zero(tmp_path: Path):
    """A detached HEAD has no symbolic ref. The lint cannot lint a
    branch name that does not exist; it must not crash — a detached
    HEAD is treated as "nothing to check" (exit 0) since there is no
    branch about to be pushed to GitHub."""
    repo = _init_repo_with_branch(tmp_path, "feature/clean")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "checkout", "-q", "--detach", sha],
        cwd=repo, check=True,
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--current"],
        cwd=repo, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# stdin mode: parse git pre-push protocol
# ---------------------------------------------------------------------------


def test_stdin_mode_pre_push_protocol_clean():
    # local-ref local-sha remote-ref remote-sha
    body = "refs/heads/feature/clean abc123 refs/heads/feature/clean def456\n"
    result = _run("-", stdin=body)
    assert result.returncode == 0, result.stderr


def test_stdin_mode_pre_push_protocol_leak():
    body = (
        "refs/heads/ver-655-foo abc123 refs/heads/ver-655-foo def456\n"
    )
    result = _run("-", stdin=body)
    assert result.returncode == 1, result.stderr
    assert "ver-655-foo" in result.stderr


def test_stdin_mode_pre_push_protocol_mixed():
    """A single push can include multiple refs; each is checked."""
    body = (
        "refs/heads/main abc 0000 def\n"
        "refs/heads/VER-7-bar 111 0000 222\n"
        "refs/heads/feature/ok 333 0000 444\n"
    )
    result = _run("-", stdin=body)
    assert result.returncode == 1, result.stderr
    assert "VER-7-bar" in result.stderr
    assert "main" not in result.stderr
    assert "feature/ok" not in result.stderr


def test_stdin_mode_pre_push_deletion_ignored():
    """A pre-push deletion has the local sha set to all zeros — there
    is no local branch to lint. Skip the line."""
    zero = "0" * 40
    body = (
        f"(delete) {zero} refs/heads/ver-old-thing abc123\n"
    )
    result = _run("-", stdin=body)
    assert result.returncode == 0, result.stderr


def test_stdin_mode_empty_input_exits_zero():
    """``git push --no-verify`` paths or empty pre-push input must
    not crash the hook."""
    result = _run("-", stdin="")
    assert result.returncode == 0, result.stderr


def test_stdin_mode_pre_push_non_branch_ref_ignored():
    """Tag pushes (``refs/tags/...``) are not branches; the lint
    silently skips them so ``git push origin v1.2.3`` keeps working."""
    body = (
        "refs/tags/v1.0.0 abc123 refs/tags/v1.0.0 def456\n"
    )
    result = _run("-", stdin=body)
    assert result.returncode == 0, result.stderr


def test_stdin_mode_garbage_line_is_silent_skip():
    """A malformed line (fewer than 4 whitespace-separated tokens) is
    skipped — git's pre-push protocol guarantees the 4-token shape
    but real-world hooks see CR/LF and trailing-newline edge cases."""
    body = "not a valid line\n\n"
    result = _run("-", stdin=body)
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Exit-code priority
# ---------------------------------------------------------------------------


def test_invalid_flag_returns_2():
    result = _run("--no-such-flag")
    assert result.returncode == 2, result.stderr


# ---------------------------------------------------------------------------
# Real-repo sanity check
# ---------------------------------------------------------------------------


def test_real_repo_main_branch_is_clean():
    """``main`` itself must not match the leak pattern. Cheap
    regression so a future change to the regex (e.g. dropping the
    digit requirement) trips immediately."""
    result = _run("main")
    assert result.returncode == 0, result.stderr

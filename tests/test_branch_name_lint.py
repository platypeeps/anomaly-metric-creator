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
  both the local and the remote ref short names per non-deleted
  line (de-duped when equal), so a refspec push that publishes a
  leaking remote ref name from a clean local ref is still
  rejected;
- ``-`` stdin mode accepts and ignores any extra positional
  arguments after the ``-`` so a real ``.git/hooks/pre-push``
  hook can pass git's ``<remote-name> <remote-url>`` argv
  through without breaking the lint;
- exit codes ``0`` clean / ``1`` leaked branch / ``2`` argument or
  I/O error;
- only the "Branch names must not embed" footer prints when an
  actual match fires.

Mirrors the layout of ``tests/test_role_name_leaks_lint.py`` and
``tests/test_amc_module_load_lint.py`` so the three guardrail
lints stay structurally parallel.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_branch_name.py"

# The ``--current`` mode shells out to ``git symbolic-ref``; the
# stdin pre-push tests bootstrap a fresh repo via ``git init``. A
# minimal source-distribution environment without git on PATH should
# skip those tests with a clear reason rather than crash with a raw
# ``FileNotFoundError``. The shared marker keeps the skip reason
# uniform across every test that needs git.
_requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git executable not on PATH; skipping --current / pre-push tests",
)


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
    # Assert the full clean-path contract: exit 0 AND empty stderr.
    # A bare "Branch names must not embed" not in result.stderr check
    # would pass vacuously if the script ever exited 2 with a usage
    # error in stderr (which also lacks the footer substring), so we
    # pin the success path on three independent observable surfaces.
    result = _run("feature/clean-name")
    assert result.returncode == 0, result.stderr
    assert result.stderr == "", result.stderr
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


@_requires_git
def test_current_mode_rejects_leaking_branch(tmp_path: Path):
    repo = _init_repo_with_branch(tmp_path, "ver-42-foo")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--current"],
        cwd=repo, capture_output=True, text=True,
    )
    assert result.returncode == 1, result.stderr
    assert "ver-42-foo" in result.stderr


@_requires_git
def test_current_mode_accepts_clean_branch(tmp_path: Path):
    repo = _init_repo_with_branch(tmp_path, "feature/clean")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--current"],
        cwd=repo, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


@_requires_git
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


def test_stdin_mode_rejects_leaking_remote_ref_with_clean_local():
    """``git push origin clean:ver-42`` pushes a leaking remote ref
    while the local ref is clean. The hook must reject the *remote*
    side so a refspec push cannot smuggle a leaking head ref past
    the guard."""
    body = (
        "refs/heads/feature/clean abc123 refs/heads/ver-42 def456\n"
    )
    result = _run("-", stdin=body)
    assert result.returncode == 1, result.stderr
    assert "ver-42" in result.stderr
    # The local side is clean, so its name should not appear in
    # the violation report.
    assert "feature/clean" not in result.stderr


def test_stdin_mode_rejects_leaking_local_with_clean_remote():
    """Conversely, a leaking *local* ref pushed to a clean remote
    ref name is still a violation — the local branch is the one
    the developer has been working on, and a future plain
    ``git push`` would publish it under that name."""
    body = (
        "refs/heads/ver-7-foo abc123 refs/heads/feature/published def456\n"
    )
    result = _run("-", stdin=body)
    assert result.returncode == 1, result.stderr
    assert "ver-7-foo" in result.stderr


def test_stdin_mode_dedupes_same_local_and_remote_ref():
    """When local and remote ref names are equal (the common
    plain-``git push`` case), the line emits exactly one
    violation entry, not two duplicates of the same name."""
    body = (
        "refs/heads/ver-42 abc123 refs/heads/ver-42 def456\n"
    )
    result = _run("-", stdin=body)
    assert result.returncode == 1, result.stderr
    # Count occurrences of the bracketed branch-name diagnostic
    # ("branch name 'ver-42' embeds …"); should fire exactly once.
    assert result.stderr.count("branch name 'ver-42'") == 1


def test_stdin_mode_accepts_pre_push_argv_clean():
    """Git invokes ``.git/hooks/pre-push`` with two argv entries
    (``<remote-name> <remote-url>``) and pipes the protocol lines on
    stdin. The documented hand-rolled hook forwards its own argv
    (``exec python3 tools/check_branch_name.py - "$@"``), so the
    script must accept and ignore extra args after the ``-`` token.
    Regression for the second-round Copilot finding that
    ``len(args) != 1`` would silently break every push from a real
    pre-push hook even when the branch name is clean."""
    body = "refs/heads/feature/clean abc123 refs/heads/feature/clean def456\n"
    result = _run("-", "origin", "git@github.com:example/repo.git", stdin=body)
    assert result.returncode == 0, result.stderr


def test_stdin_mode_accepts_pre_push_argv_leak():
    """The hardened ``-`` mode must still detect a leak even when
    git's two extra argv entries are present — accepting the tail
    args cannot collapse into "ignore the whole invocation"."""
    body = "refs/heads/ver-42 abc123 refs/heads/ver-42 def456\n"
    result = _run("-", "origin", "git@github.com:example/repo.git", stdin=body)
    assert result.returncode == 1, result.stderr
    assert "ver-42" in result.stderr


def test_stdin_mode_remote_ref_deletion_skipped():
    """A refspec deletion (``git push origin :ver-old``) has the
    local sha set to all zeros — there is no live local branch
    to publish, so the line is skipped on both sides."""
    zero = "0" * 40
    body = (
        f"(delete) {zero} refs/heads/ver-old abc123\n"
    )
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

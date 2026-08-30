"""Acceptance tests for `tools/check_role_name_leaks.py`.

The lint forbids internal role-name labels (canonical list in the
script's ``_FORBIDDEN_LABELS`` tuple) from leaking into text-bearing
files and ad-hoc comment bodies destined for GitHub.

Pin the behaviors the script promises in its docstring so a future
edit cannot silently weaken the guardrail:

- whole-word, case-sensitive matching;
- multi-label and multi-file aggregation in a single run;
- ``-`` stdin mode for pre-flighting ``gh ... --body-file`` payloads;
- the *trailing* ``# role-name-lint: allow`` escape hatch (mid-line
  occurrences do NOT exempt the line);
- exit codes ``0`` clean / ``1`` label leak / ``2`` argument or I/O
  error;
- only the "internal role names" footer prints when an actual match
  fires (I/O errors print their own diagnostic but skip the footer);
- binary inputs are a silent skip;
- skip-dirs (``.git``, ``.venv``, ``node_modules``) and ``*.lock`` are
  silently ignored.

Mirrors the layout of ``tests/test_amc_module_load_lint.py`` so the
two test-hygiene lints stay structurally parallel.

Every Python source line below that bakes a forbidden label into a
test fixture or assertion carries the trailing
``# role-name-lint: allow`` marker so the repo-tree scan
(``test_real_repo_tree_is_clean``) stays clean. The temp files the
tests write into ``tmp_path`` are independent from the Python source
and carry the marker (or not) according to the behavior the test is
pinning.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_role_name_leaks.py"


def _run(*paths: Path, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, paths)],
        input=stdin,
        capture_output=True,
        text=True,
    )


def test_script_exists():
    assert SCRIPT.is_file(), f"{SCRIPT} not found"


def test_clean_file_exits_zero(tmp_path: Path):
    clean = tmp_path / "ok.md"
    clean.write_text("Nothing internal here. The reviewer signed off.\n")
    result = _run(clean)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_label_match_exits_one(tmp_path: Path):
    bad = tmp_path / "leak.md"
    bad.write_text("Handoff to Lead Engineer for merge.\n")  # role-name-lint: allow
    result = _run(bad)
    assert result.returncode == 1, result.stderr
    assert "Lead Engineer" in result.stderr  # role-name-lint: allow
    assert str(bad) in result.stderr


def test_footer_prints_on_label_match(tmp_path: Path):
    bad = tmp_path / "leak.md"
    bad.write_text("Handoff to Lead Engineer for merge.\n")  # role-name-lint: allow
    result = _run(bad)
    assert result.returncode == 1
    assert "Internal role names must not appear" in result.stderr


def test_footer_does_not_print_on_clean_run(tmp_path: Path):
    clean = tmp_path / "ok.md"
    clean.write_text("All good.\n")
    result = _run(clean)
    assert "Internal role names must not appear" not in result.stderr


def test_whole_word_matching_substring_not_flagged(tmp_path: Path):
    """``boardroom`` and ``CEO123`` are not the standalone labels and
    must not trip the lint. The script uses ``\\b`` word boundaries
    specifically to allow these incidental substrings."""
    ok = tmp_path / "substring.md"
    ok.write_text("Visit the boardroom. User CEO123 logged in.\n")
    result = _run(ok)
    assert result.returncode == 0, result.stderr


def test_case_sensitive_lowercase_board_not_flagged(tmp_path: Path):
    # The label name appears inside the docstring line below; the
    # trailing marker on that source line exempts it from the lint
    # (the line ends with the marker, mid-line occurrences of the
    # label inside the prose are fine).
    """Lowercase ``board`` (the generic noun) is free to use; only the capitalized label ``Board`` is forbidden. The case-sensitive pattern is explicitly documented in the script's source comments."""  # role-name-lint: allow
    ok = tmp_path / "lowercase.md"
    ok.write_text("The board of directors meets quarterly.\n")
    result = _run(ok)
    assert result.returncode == 0, result.stderr


def test_capitalized_board_is_flagged(tmp_path: Path):
    bad = tmp_path / "uppercase.md"
    bad.write_text("Awaiting Board review.\n")  # role-name-lint: allow
    result = _run(bad)
    assert result.returncode == 1, result.stderr
    assert "Board" in result.stderr  # role-name-lint: allow


@pytest.mark.parametrize(
    "label",
    ["Lead Engineer", "Code Reviewer", "Release Engineer", "CEO", "Board"],  # role-name-lint: allow
)
def test_each_canonical_label_is_flagged(tmp_path: Path, label: str):
    """Every entry in ``_FORBIDDEN_LABELS`` must be flagged when it
    appears as a standalone word in user-authored text."""
    bad = tmp_path / "leak.md"
    bad.write_text(f"Status: {label} is on call.\n")
    result = _run(bad)
    assert result.returncode == 1, result.stderr
    assert label in result.stderr


def test_multiple_matches_aggregate_in_one_run(tmp_path: Path):
    """The script reports every match in a single run; it does not
    short-circuit on the first violation. Docstring contract: "reports
    every match it finds in a single run"."""
    bad = tmp_path / "multi.md"
    # Use a single source line per string literal so the trailing
    # marker exempts each line; an adjacent-string concatenation
    # spread across multiple source lines would leave the middle
    # lines without a marker.
    bad.write_text("Lead Engineer assigned.\nCEO approved.\nBoard signed off.\n")  # role-name-lint: allow
    result = _run(bad)
    assert result.returncode == 1
    assert "Lead Engineer" in result.stderr  # role-name-lint: allow
    assert "CEO" in result.stderr  # role-name-lint: allow
    assert "Board" in result.stderr  # role-name-lint: allow


def test_stdin_mode_clean(tmp_path: Path):
    result = _run(Path("-"), stdin="All approvals in order.\n")
    assert result.returncode == 0, result.stderr


def test_stdin_mode_label_match():
    result = _run(Path("-"), stdin="Handoff to CEO.\n")  # role-name-lint: allow
    assert result.returncode == 1, result.stderr
    assert "<stdin>" in result.stderr
    assert "CEO" in result.stderr  # role-name-lint: allow


def test_trailing_allow_marker_exempts_line(tmp_path: Path):
    """A line whose *trailing* content is the
    ``# role-name-lint: allow`` marker is skipped wholesale. This is
    the documented behavior and the same pattern the script's own
    ``_FORBIDDEN_LABELS`` tuple relies on."""
    ok = tmp_path / "allow.py"
    ok.write_text('CEO_LABEL = "CEO"  # role-name-lint: allow\n')  # role-name-lint: allow
    result = _run(ok)
    assert result.returncode == 0, result.stderr


def test_marker_followed_by_trailing_whitespace_still_exempts(tmp_path: Path):
    """``rstrip()``-equivalent: trailing whitespace after the marker
    must not defeat the exemption (otherwise text editors that strip
    or preserve trailing spaces would flip the lint state)."""
    ok = tmp_path / "allow_trail.py"
    ok.write_text('LABEL = "Board"  # role-name-lint: allow   \n')  # role-name-lint: allow
    result = _run(ok)
    assert result.returncode == 0, result.stderr


def test_midline_marker_does_NOT_exempt_line(tmp_path: Path):
    """The docstring says the escape hatch is the *trailing* marker.
    A marker that appears in the middle of a line (with forbidden
    content still appearing after it) must NOT silence the lint —
    otherwise the marker could be (accidentally or intentionally)
    used to disable the check on a line that still leaks. Copilot
    PR #89 round-1."""
    bad = tmp_path / "midline.md"
    bad.write_text("# role-name-lint: allow but actually Lead Engineer leaks\n")  # role-name-lint: allow
    result = _run(bad)
    assert result.returncode == 1, result.stderr
    assert "Lead Engineer" in result.stderr  # role-name-lint: allow


def test_no_args_returns_2(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, result.stderr
    assert "usage" in result.stderr.lower()


def test_unreadable_path_returns_2(tmp_path: Path):
    """Docstring contract: exit code ``2`` for argument or I/O error.
    A read failure on an existing path (simulated by making the file
    unreadable) must exit 2, distinct from the label-leak exit 1, so
    callers can distinguish "I broke something" from "you leaked a
    label". Copilot PR #89 round-1."""
    if sys.platform.startswith("win"):
        pytest.skip("POSIX-only chmod semantics")
    target = tmp_path / "unreadable.md"
    target.write_text("Some text.\n")
    target.chmod(0o000)
    try:
        result = _run(target)
    finally:
        target.chmod(0o644)
    assert result.returncode == 2, result.stderr


def test_unreadable_path_does_not_print_role_footer(tmp_path: Path):
    """When the only diagnostic is "unreadable" (no label match), the
    "Internal role names must not appear ..." footer is misleading —
    no role name was found. The script must omit it. Copilot PR #89
    round-1."""
    if sys.platform.startswith("win"):
        pytest.skip("POSIX-only chmod semantics")
    target = tmp_path / "unreadable.md"
    target.write_text("Some text.\n")
    target.chmod(0o000)
    try:
        result = _run(target)
    finally:
        target.chmod(0o644)
    assert "Internal role names must not appear" not in result.stderr


def test_unreadable_plus_label_returns_2(tmp_path: Path):
    """Mixed inputs: I/O error takes priority over the label-leak
    exit so the caller sees the structural failure first."""
    if sys.platform.startswith("win"):
        pytest.skip("POSIX-only chmod semantics")
    unreadable = tmp_path / "blocked.md"
    unreadable.write_text("\n")
    unreadable.chmod(0o000)
    leaky = tmp_path / "leak.md"
    leaky.write_text("Awaiting Board sign-off.\n")  # role-name-lint: allow
    try:
        result = _run(unreadable, leaky)
    finally:
        unreadable.chmod(0o644)
    assert result.returncode == 2, result.stderr
    assert "Board" in result.stderr  # role-name-lint: allow


def test_directory_is_silent_skip(tmp_path: Path):
    """``_scan_path`` early-returns on non-files. A directory path is
    silently ignored — pre-commit only passes files, but ad-hoc CLI
    runs sometimes pass directories and the script should not crash."""
    sub = tmp_path / "subdir"
    sub.mkdir()
    result = _run(sub)
    assert result.returncode == 0, result.stderr


def test_binary_file_silent_skip(tmp_path: Path):
    """A UTF-8 decode failure marks the file as binary and the script
    skips it silently. This keeps pre-commit-driven runs from emitting
    noisy diagnostics on images, fonts, or other binary blobs."""
    binary = tmp_path / "image.bin"
    binary.write_bytes(b"\xff\xfe\x00\x01\x02")
    result = _run(binary)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_lock_file_silent_skip(tmp_path: Path):
    """``*.lock`` files (poetry / npm / cargo) are not in scope —
    contents are machine-generated and may contain forbidden
    substrings outside the author's control. The body intentionally
    embeds a forbidden label so the test would fail (exit 1, label in
    stderr) if the ``.lock`` suffix skip ever regressed and the file
    were scanned normally. Copilot PR #89 round-2."""
    lock = tmp_path / "package-lock.json.lock"
    lock.write_text("Handoff to CEO for sign-off.\n")  # role-name-lint: allow
    result = _run(lock)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


@pytest.mark.parametrize("skip_dir", [".git", ".venv", "node_modules"])
def test_skip_dirs_are_silent_skip(tmp_path: Path, skip_dir: str):
    sub = tmp_path / skip_dir
    sub.mkdir()
    leaky = sub / "leak.md"
    leaky.write_text("CEO approved.\n")  # role-name-lint: allow
    result = _run(leaky)
    assert result.returncode == 0, result.stderr


def test_multiple_files_partial_violation(tmp_path: Path):
    """Mixed clean/dirty inputs: the dirty file's path appears in
    diagnostics, the clean file's path does not, and the exit code
    reflects the leak."""
    clean = tmp_path / "ok.md"
    clean.write_text("All approvals tracked.\n")
    dirty = tmp_path / "leak.md"
    dirty.write_text("Lead Engineer assigned.\n")  # role-name-lint: allow
    result = _run(clean, dirty)
    assert result.returncode == 1, result.stderr
    assert str(dirty) in result.stderr
    assert str(clean) not in result.stderr


def test_real_repo_tree_is_clean():
    """The real repo must pass the lint at HEAD. Globs every text-y
    path the pre-commit hook would feed: Python, Markdown, YAML, TOML
    in the top-level tracked tree, plus the ``docs/`` tree and the
    root-level Markdown / dotfiles (``AGENTS.md``, ``CHANGELOG.md``,
    ``.gitignore``) that Copilot PR #89 round-2 flagged as gaps.
    Equivalent to the ``test_real_test_tree_is_clean`` regression in
    ``tests/test_amc_module_load_lint.py``."""
    candidate_dirs = [
        REPO_ROOT / "src",
        REPO_ROOT / "scripts",
        REPO_ROOT / ".agents",
        REPO_ROOT / "tools",
        REPO_ROOT / "tests",
        REPO_ROOT / ".github",
        REPO_ROOT / "docs",
    ]
    candidate_files = [
        REPO_ROOT / "CLAUDE.md",
        REPO_ROOT / "README.md",
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT / "CHANGELOG.md",
        REPO_ROOT / ".gitignore",
        REPO_ROOT / "pyproject.toml",
        REPO_ROOT / ".pre-commit-config.yaml",
        REPO_ROOT / "anomaly-metric-creator.py",
    ]
    files: list[Path] = [p for p in candidate_files if p.is_file()]
    for d in candidate_dirs:
        if d.is_dir():
            for ext in ("*.py", "*.md", "*.yaml", "*.yml", "*.toml"):
                files.extend(d.rglob(ext))
    assert files, "no candidate files found for repo-tree scan"
    # Absolute paths across the expanded live tree exceed Windows'
    # CreateProcess command-line limit in this repository. Bounded chunks keep
    # the same complete-tree assertion portable as the task/archive set grows.
    for start in range(0, len(files), 100):
        result = _run(*files[start : start + 100])
        assert result.returncode == 0, (
            "lint failed against current repo tree chunk "
            f"{start // 100 + 1}:\nstderr:\n{result.stderr}"
        )


def test_nonexistent_path_exits_two(tmp_path: Path):
    """A typo'd path argument must exit 2, not 0: the documented ad-hoc
    pre-flight chains this script with ``&&`` before ``gh pr comment``,
    so a silently-clean exit on a missing body file would let an
    unchecked body post."""
    missing = tmp_path / "definitely-missing-body.md"
    result = _run(missing)
    assert result.returncode == 2, (
        f"expected exit 2 for nonexistent path, got {result.returncode}; "
        f"stderr: {result.stderr}"
    )
    assert "no such file" in result.stderr


def test_nonexistent_path_takes_precedence_over_clean_files(tmp_path: Path):
    """Mixing a clean file with a missing one still exits 2 — the I/O
    error must not be masked by the clean scan."""
    clean = tmp_path / "clean.md"
    clean.write_text("nothing to see here\n", encoding="utf-8")
    result = _run(clean, tmp_path / "missing.md")
    assert result.returncode == 2, result.stderr


def test_directory_argument_still_silently_skipped(tmp_path: Path):
    """An existing directory keeps the historic silent skip (exit 0):
    pre-commit only passes files, and a directory has no text to leak —
    only *nonexistent* paths are the typo hazard."""
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_nonexistent_path_matching_skip_rule_still_exits_two(tmp_path: Path):
    """The existence check must run *before* the skip rules: a typo'd
    path with a skip-listed suffix (``.lock``) or a skip-listed
    directory component (``.venv``) is still a typo, and the ``&&``
    pre-flight chain must stay blocked. (Copilot review on PR #97: the
    original ordering let ``_should_skip`` silently pass nonexistent
    skip-rule matches.)"""
    for missing in (
        tmp_path / "missing-body.lock",
        tmp_path / ".venv" / "missing-body.md",
    ):
        result = _run(missing)
        assert result.returncode == 2, (
            f"{missing}: expected exit 2, got {result.returncode}; "
            f"stderr: {result.stderr}"
        )
        assert "no such file" in result.stderr


def test_existing_skip_rule_path_still_silently_skipped(tmp_path: Path):
    """Reordering the existence check must not change skip-rule
    semantics for paths that DO exist: an on-disk ``.lock`` file —
    even one containing a forbidden label that would exit 1 if it
    were scanned — stays a silent exit-0 skip."""
    lock = tmp_path / "deps.lock"
    lock.write_text("Handoff to Lead Engineer for merge.\n")  # role-name-lint: allow
    result = _run(lock)
    assert result.returncode == 0, result.stderr

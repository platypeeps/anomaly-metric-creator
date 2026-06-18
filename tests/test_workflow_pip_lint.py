"""Acceptance tests for `tools/check_workflow_pip.py`.

The lint forbids bare `pip install` in GitHub Actions workflows (the robust
form is `python -m pip install`, which targets the interpreter
`actions/setup-python` selected). `uv pip install` is also allowed. Direct
third-party installs must use exact `==` pins so security tooling and other
workflow dependencies are reproducible. PR #118 shipped a bare `pip install`
in `socket.yml`; this guard catches the pattern structurally.

Pin the behaviors the script promises in its docstring:

- exact-pinned `python -m pip install` and `uv pip install` pass (exit 0);
- unpinned or `--upgrade` installs are rejected (exit 1);
- bare `pip install` and `pip3 install` are rejected (exit 1) with a
  diagnostic naming the line;
- `pipx install` is not matched (different tool);
- a line mixing a good and a bare invocation is still flagged;
- a trailing `# pip-lint: allow` exempts the line;
- exit codes 0 clean / 1 violation / 2 argument-or-IO error (no args,
  missing path);
- the repo's own `.github/workflows/*.yml` are clean (regression guard on
  the live files).

Mirrors the layout of `tests/test_branch_name_lint.py` and the other
`*_lint.py` guardrail tests.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_workflow_pip.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def _wf(tmp_path: Path, run_line: str) -> str:
    """Write a minimal one-step workflow whose run command is `run_line`."""
    path = tmp_path / "wf.yml"
    path.write_text(
        "name: t\non: [push]\njobs:\n  j:\n    runs-on: ubuntu-latest\n"
        "    steps:\n"
        f"      - run: {run_line}\n",
        encoding="utf-8",
    )
    return str(path)


def test_python_m_pip_passes(tmp_path: Path) -> None:
    result = _run(_wf(tmp_path, "python -m pip install socketsecurity==2.1.0"))
    assert result.returncode == 0, result.stderr


def test_combined_python_flags_pass(tmp_path: Path) -> None:
    # `python -Im pip` (isolated mode + module, combined short flags) still
    # targets the selected interpreter — must not be flagged (Copilot, #124).
    result = _run(_wf(tmp_path, "python -Im pip install requests==2.32.5"))
    assert result.returncode == 0, result.stderr


def test_extra_whitespace_after_m_passes(tmp_path: Path) -> None:
    # Extra spaces between `-m` and `pip` must not break the exemption.
    result = _run(_wf(tmp_path, "python -m  pip install requests==2.32.5"))
    assert result.returncode == 0, result.stderr


def test_uv_pip_passes(tmp_path: Path) -> None:
    result = _run(_wf(tmp_path, "uv pip install pytest==8.4.2"))
    assert result.returncode == 0, result.stderr


def test_python_m_pip_unpinned_package_rejected(tmp_path: Path) -> None:
    result = _run(_wf(tmp_path, "python -m pip install socketsecurity"))
    assert result.returncode == 1
    assert "exact '==' pin" in result.stderr


def test_python_m_pip_wildcard_pin_rejected(tmp_path: Path) -> None:
    # `==2.*` contains `==` but is a wildcard, not a reproducible exact pin
    # — must be rejected (Copilot, #125).
    result = _run(_wf(tmp_path, "python -m pip install socketsecurity==2.*"))
    assert result.returncode == 1
    assert "wildcard" in result.stderr


def test_pinned_install_with_trailing_comment_passes(tmp_path: Path) -> None:
    # A trailing shell comment must not be tokenized as extra "package" args
    # and falsely flagged as unpinned (Copilot, #125).
    result = _run(_wf(tmp_path, "python -m pip install requests==2.32.5  # pinned"))
    assert result.returncode == 0, result.stderr


def test_comment_only_pip_mention_passes(tmp_path: Path) -> None:
    # A comment that merely mentions "pip install" is not a real invocation.
    result = _run(_wf(tmp_path, "echo done  # later: pip install something"))
    assert result.returncode == 0, result.stderr


def test_python_m_pip_upgrade_rejected(tmp_path: Path) -> None:
    result = _run(_wf(tmp_path, "python -m pip install --upgrade socketsecurity"))
    assert result.returncode == 1
    assert "--upgrade" in result.stderr


def test_bare_pip_rejected(tmp_path: Path) -> None:
    wf = _wf(tmp_path, "pip install requests")
    result = _run(wf)
    assert result.returncode == 1
    assert "bare 'pip install'" in result.stderr
    assert "wf.yml" in result.stderr


def test_pip3_rejected(tmp_path: Path) -> None:
    result = _run(_wf(tmp_path, "pip3 install requests"))
    assert result.returncode == 1


def test_pipx_not_matched(tmp_path: Path) -> None:
    result = _run(_wf(tmp_path, "pipx install ruff"))
    assert result.returncode == 0, result.stderr


def test_mixed_line_still_flagged(tmp_path: Path) -> None:
    # A good invocation earlier on the line must not mask a bare one later.
    result = _run(_wf(tmp_path, "python -m pip install pip==25.3 && pip install x"))
    assert result.returncode == 1


def test_allow_marker_exempts(tmp_path: Path) -> None:
    result = _run(_wf(tmp_path, "pip install legacy-thing  # pip-lint: allow"))
    assert result.returncode == 0, result.stderr


def test_no_args_exits_two() -> None:
    result = _run()
    assert result.returncode == 2


def test_missing_path_exits_two(tmp_path: Path) -> None:
    result = _run(str(tmp_path / "does-not-exist.yml"))
    assert result.returncode == 2


def test_non_utf8_file_exits_two(tmp_path: Path) -> None:
    # A decode failure on a non-UTF-8 file must honor the exit-2 contract,
    # not escape as a traceback (Copilot, #124).
    bad = tmp_path / "bad.yml"
    bad.write_bytes(b"\xff\xfe run: pip install x\n")
    result = _run(str(bad))
    assert result.returncode == 2


def test_real_repo_workflows_clean() -> None:
    # Regression guard on the live workflow files: they must already use
    # `python -m pip` / `uv` with exact pins, so the lint passes today and
    # only the introduction of a bare or unpinned `pip install` makes it fail.
    # Glob both `.yml` and `.yaml` to match the hook's `*.ya?ml` scope
    # (Copilot, #124).
    wf_dir = REPO_ROOT / ".github" / "workflows"
    workflows = sorted([*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")])
    assert workflows, "expected at least one workflow file to guard"
    result = _run(*(str(w) for w in workflows))
    assert result.returncode == 0, result.stderr

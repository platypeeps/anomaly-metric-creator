"""Focused tests for ``scripts/trellis-housekeeping.sh`` auto-finalize safety."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "trellis-housekeeping.sh"


def _housekeeping_library(tmp_path: Path) -> Path:
    script_text = SCRIPT.read_text(encoding="utf-8")
    main_call = 'main "$@"'
    assert script_text.rstrip().endswith(main_call)
    library_text = script_text.rsplit(main_call, 1)[0]
    library = tmp_path / "trellis-housekeeping-lib.sh"
    library.write_text(library_text, encoding="utf-8")
    return library


def _run_harness(tmp_path: Path, body: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    library = _housekeeping_library(tmp_path)
    harness = tmp_path / "harness.sh"
    harness.write_text(
        textwrap.dedent(
            f"""
            #!/usr/bin/env bash
            source "{library}"
            set +e
            {body}
            """
        ).lstrip(),
        encoding="utf-8",
    )
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        ["bash", str(harness)],
        capture_output=True,
        env=run_env,
        text=True,
    )


def test_wait_for_pr_head_readiness_accepts_clean_finalize_head(tmp_path: Path) -> None:
    result = _run_harness(
        tmp_path,
        """
        DEFAULT_BRANCH=main
        FINALIZE_CHECK_TIMEOUT_SECONDS=0
        FINALIZE_CHECK_POLL_SECONDS=1

        view_open_pr_readiness_for_branch() {
          printf '153\\tOPEN\\tfalse\\thttps://example.test/pr/153\\t%s\\tafter\\tmain\\tCLEAN\\t0\\t3\\n' "$1"
        }
        unresolved_review_thread_count() {
          printf '0\\n'
        }

        if wait_for_pr_head_readiness feature 153 after; then
          printf 'status=ok\\n'
        else
          printf 'status=fail\\n'
        fi
        printf 'actions=%s\\n' "${ACTIONS[*]}"
        printf 'anomalies=%s\\n' "${ANOMALIES[*]-}"
        """,
    )

    assert result.returncode == 0, result.stderr
    assert "status=ok" in result.stdout
    assert "waiting up to 0s for PR #153 checks on finalize head after" in result.stdout
    assert "PR #153 finalize head is green and comment-clean" in result.stdout
    assert "anomalies=" in result.stdout


def test_wait_for_pr_head_readiness_times_out_when_finalize_checks_pending(tmp_path: Path) -> None:
    result = _run_harness(
        tmp_path,
        """
        DEFAULT_BRANCH=main
        FINALIZE_CHECK_TIMEOUT_SECONDS=0
        FINALIZE_CHECK_POLL_SECONDS=1

        view_open_pr_readiness_for_branch() {
          printf '153\\tOPEN\\tfalse\\thttps://example.test/pr/153\\t%s\\tafter\\tmain\\tBLOCKED\\t1\\t3\\n' "$1"
        }
        unresolved_review_thread_count() {
          printf '0\\n'
        }

        if wait_for_pr_head_readiness feature 153 after; then
          printf 'status=ok\\n'
        else
          printf 'status=fail\\n'
        fi
        printf 'anomalies=%s\\n' "${ANOMALIES[*]-}"
        """,
    )

    assert result.returncode == 0, result.stderr
    assert "status=fail" in result.stdout
    assert "timed out after 0s waiting for PR #153 finalize head after" in result.stdout
    assert "merge state BLOCKED, non-green checks 1/3" in result.stdout


def test_auto_finalize_does_not_merge_when_finalize_head_is_not_ready(tmp_path: Path) -> None:
    events_file = tmp_path / "events.txt"
    result = _run_harness(
        tmp_path,
        """
        DEFAULT_BRANCH=main
        START_BRANCH=feature
        MERGE_STRATEGY=merge
        GITHUB_REPO_SLUG=owner/repo

        working_tree_is_clean() {
          return 0
        }
        have() {
          return 0
        }
        view_open_pr_readiness_for_branch() {
          printf '153\\tOPEN\\tfalse\\thttps://example.test/pr/153\\tfeature\\tbefore\\tmain\\tCLEAN\\t0\\t3\\n'
        }
        remote_branch_head_oid() {
          printf 'before\\n'
        }
        unresolved_review_thread_count() {
          printf '0\\n'
        }
        git() {
          case "$*" in
            "rev-parse --verify refs/heads/feature^{commit}")
              printf 'before\\n'
              ;;
            *)
              printf 'unexpected git call: %s\\n' "$*" >&2
              return 1
              ;;
          esac
        }
        run_finalize_command() {
          printf 'finalize\\n' >> "$EVENTS_FILE"
          FINALIZE_PUSHED_HEAD=after
          return 0
        }
        wait_for_pr_head_readiness() {
          printf 'wait:%s\\n' "$3" >> "$EVENTS_FILE"
          return 1
        }
        merge_open_pr_after_finalize() {
          printf 'merge\\n' >> "$EVENTS_FILE"
          return 0
        }

        maybe_finalize_ready_open_pr feature
        cat "$EVENTS_FILE"
        """,
        env={"EVENTS_FILE": str(events_file)},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["finalize", "wait:after"]

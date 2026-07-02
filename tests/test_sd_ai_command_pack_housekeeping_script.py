"""Focused tests for ``scripts/sd-ai-command-pack-housekeeping.sh`` merge safety."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "sd-ai-command-pack-housekeeping.sh"


def _housekeeping_library(tmp_path: Path) -> Path:
    script_text = SCRIPT.read_text(encoding="utf-8")
    main_call = 'main "$@"'
    assert script_text.rstrip().endswith(main_call)
    library_text = script_text.rsplit(main_call, 1)[0]
    library = tmp_path / "sd-ai-command-pack-housekeeping-lib.sh"
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


def test_valid_github_repo_slug_accepts_strict_owner_repo_and_normalized_remote(
    tmp_path: Path,
) -> None:
    result = _run_harness(
        tmp_path,
        """
        if valid_github_repo_slug platypeeps/anomaly-metric-creator; then
          printf 'direct=ok\\n'
        else
          printf 'direct=fail\\n'
        fi
        printf 'remote=%s\\n' "$(github_repo_from_remote_url https://github.com/platypeeps/anomaly-metric-creator.git)"
        """,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "direct=ok",
        "remote=platypeeps/anomaly-metric-creator",
    ]


def test_valid_github_repo_slug_rejects_invalid_override_shapes(
    tmp_path: Path,
) -> None:
    result = _run_harness(
        tmp_path,
        """
        for slug in \
          /owner/repo \
          owner/repo/extra \
          owner/ \
          "owner repo/name"
        do
          if valid_github_repo_slug "$slug"; then
            printf 'accepted=%s\\n' "$slug"
          fi
        done
        """,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_ready_open_pr_merges_when_heads_checks_and_threads_match(tmp_path: Path) -> None:
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
            "rev-parse --verify HEAD")
              printf 'before\\n'
              ;;
            *)
              printf 'unexpected git call: %s\\n' "$*" >&2
              return 1
              ;;
          esac
        }
        gh_pr_merge() {
          printf 'merge:%s\\n' "$*" >> "$EVENTS_FILE"
          return 0
        }

        maybe_merge_ready_open_pr feature
        cat "$EVENTS_FILE"
        """,
        env={"EVENTS_FILE": str(events_file)},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["merge:153 --merge --match-head-commit before"]


def test_ready_open_pr_does_not_merge_when_checks_are_not_green(tmp_path: Path) -> None:
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
          printf '153\\tOPEN\\tfalse\\thttps://example.test/pr/153\\tfeature\\tbefore\\tmain\\tCLEAN\\t1\\t3\\n'
        }
        remote_branch_head_oid() {
          printf 'before\\n'
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
        gh_pr_merge() {
          printf 'merge\\n' >> "$EVENTS_FILE"
          return 0
        }

        maybe_merge_ready_open_pr feature
        printf 'events=%s\\n' "$(cat "$EVENTS_FILE" 2>/dev/null || true)"
        printf 'anomalies=%s\\n' "${ANOMALIES[*]-}"
        """,
        env={"EVENTS_FILE": str(events_file)},
    )

    assert result.returncode == 0, result.stderr
    assert "events=\n" in result.stdout
    assert "non-green checks" in result.stdout


def test_auto_merge_skips_when_no_check_executed_successfully(tmp_path: Path) -> None:
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
          printf '153\\tOPEN\\tfalse\\thttps://example.test/pr/153\\tfeature\\tbefore\\tmain\\tCLEAN\\t0\\t0\\n'
        }
        remote_branch_head_oid() {
          printf 'before\\n'
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
        gh_pr_merge() {
          printf 'merge\\n' >> "$EVENTS_FILE"
          return 0
        }

        maybe_merge_ready_open_pr feature
        printf 'events=%s\\n' "$(cat "$EVENTS_FILE" 2>/dev/null || true)"
        printf 'anomalies=%s\\n' "${ANOMALIES[*]-}"
        """,
        env={"EVENTS_FILE": str(events_file)},
    )

    assert result.returncode == 0, result.stderr
    assert "events=\n" in result.stdout
    assert "no successful executed checks" in result.stdout


def test_auto_merge_skips_when_check_counts_are_undeterminable(tmp_path: Path) -> None:
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
          printf '153\\tOPEN\\tfalse\\thttps://example.test/pr/153\\tfeature\\tbefore\\tmain\\tCLEAN\\tunknown\\tunknown\\n'
        }
        remote_branch_head_oid() {
          printf 'before\\n'
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
        gh_pr_merge() {
          printf 'merge\\n' >> "$EVENTS_FILE"
          return 0
        }

        maybe_merge_ready_open_pr feature
        printf 'events=%s\\n' "$(cat "$EVENTS_FILE" 2>/dev/null || true)"
        printf 'anomalies=%s\\n' "${ANOMALIES[*]-}"
        """,
        env={"EVENTS_FILE": str(events_file)},
    )

    assert result.returncode == 0, result.stderr
    assert "events=\n" in result.stdout
    assert "undeterminable check counts" in result.stdout

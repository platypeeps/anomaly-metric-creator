"""Acceptance tests for ``tools/check_approval_duplicate.py``.

The gate refuses to post a duplicate APPROVED-shape PR comment from
the same author against the same commit OID, and refuses to post a
self-correction as a new comment (it should be an edit to the existing
one). Wires into the same ``gh pr comment --body-file …`` pre-flight
slot as the role-name lint.

Pin every behavior the script promises in its docstring so a future
edit cannot silently weaken the guardrail:

- approval-shape detection (first non-blank line starts with the
  literal upper-case token ``APPROVED``);
- self-correction prefix detection (case-insensitive
  ``Correction to previous comment`` anywhere on the first non-blank
  line, or ``Correction:`` at line start);
- duplicate detection against prior same-author comments whose
  ``created_at`` is at or after the head commit's committer
  timestamp;
- the fixture-driven mode (``--head-commit-oid``,
  ``--head-commit-date``, ``--author``, ``--prior-comments-json``)
  used by tests and offline CI;
- exit codes ``0`` clean / ``1`` refusal / ``2`` argument or I/O
  error.

Mirrors the layout of ``tests/test_role_name_leaks_lint.py`` so the
two PR-thread-hygiene lints stay structurally parallel.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_approval_duplicate.py"

# A head commit SHA + timestamp shared by the fixture-driven tests.
# The timestamp is the "boundary" the duplicate gate uses: any
# same-author APPROVED comment with ``created_at >= HEAD_DATE`` is
# treated as a duplicate against ``HEAD_OID``.
HEAD_OID = "f1321e4575e32887c72163a994076dde98bda75d"
HEAD_DATE = "2026-06-01T00:10:00Z"
AUTHOR = "sdelmas"


def _write_prior(tmp_path: Path, comments: list[dict]) -> Path:
    """Serialize a prior-comments JSON fixture and return its path."""
    target = tmp_path / "prior.json"
    target.write_text(json.dumps(comments), encoding="utf-8")
    return target


def _run_fixture(
    body: str,
    *,
    prior_comments: list[dict],
    tmp_path: Path,
    head_oid: str = HEAD_OID,
    head_date: str = HEAD_DATE,
    author: str = AUTHOR,
) -> subprocess.CompletedProcess:
    prior_path = _write_prior(tmp_path, prior_comments)
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--head-commit-oid",
            head_oid,
            "--head-commit-date",
            head_date,
            "--author",
            author,
            "--prior-comments-json",
            str(prior_path),
        ],
        input=body,
        capture_output=True,
        text=True,
    )


def test_script_exists():
    assert SCRIPT.is_file(), f"{SCRIPT} not found"


def test_clean_non_approval_body_passes(tmp_path: Path):
    """A body that is not an APPROVED-shape comment is never gated —
    the script only intercepts the approval-write path. A plain
    informational comment goes through unchanged."""
    result = _run_fixture(
        "Just a status note, no approval here.\n",
        prior_comments=[],
        tmp_path=tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_approval_with_no_prior_comments_passes(tmp_path: Path):
    """First approval on a fresh PR: nothing to gate against."""
    result = _run_fixture(
        "APPROVED\n\nVerified.\n",
        prior_comments=[],
        tmp_path=tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_duplicate_same_author_same_commit_is_refused(tmp_path: Path):
    """Same author posted APPROVED for the current HEAD already; refuse."""
    prior = [
        {
            "id": 4588669761,
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:16:25Z",
            "body": "APPROVED\n\nFirst pass.\n",
        }
    ]
    result = _run_fixture(
        "APPROVED\n\nSecond pass — same commit.\n",
        prior_comments=prior,
        tmp_path=tmp_path,
    )
    assert result.returncode == 1, result.stderr
    assert "4588669761" in result.stderr
    assert "edit" in result.stderr.lower()


def test_duplicate_includes_existing_comment_id_in_diagnostic(tmp_path: Path):
    """The diagnostic must name the existing comment ID so the caller
    can immediately turn the new-write into a PATCH on the existing
    comment without a second round-trip to the API."""
    prior = [
        {
            "id": 123456789,
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:16:25Z",
            "body": "APPROVED\n\nFirst pass.\n",
        }
    ]
    result = _run_fixture(
        "APPROVED\n\nSecond pass.\n",
        prior_comments=prior,
        tmp_path=tmp_path,
    )
    assert result.returncode == 1
    assert "123456789" in result.stderr


def test_prior_approval_before_head_does_not_count(tmp_path: Path):
    """If a prior APPROVED comment exists but was created BEFORE the
    current HEAD commit's timestamp, it was for an earlier commit OID
    and doesn't gate a fresh approval against the new HEAD."""
    prior = [
        {
            "id": 1,
            "user": {"login": AUTHOR},
            "created_at": "2026-05-30T12:00:00Z",  # before HEAD_DATE
            "body": "APPROVED\n\nOld commit.\n",
        }
    ]
    result = _run_fixture(
        "APPROVED\n\nNew commit.\n",
        prior_comments=prior,
        tmp_path=tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_prior_approval_from_different_author_does_not_count(tmp_path: Path):
    """Different reviewers can approve in parallel; the gate is per
    ``(reviewer, commit OID)``. Another author's APPROVED on the same
    commit does not block the current author's approval."""
    prior = [
        {
            "id": 1,
            "user": {"login": "someone-else"},
            "created_at": "2026-06-01T00:16:25Z",
            "body": "APPROVED\n\nLGTM.\n",
        }
    ]
    result = _run_fixture(
        "APPROVED\n\nAlso LGTM.\n",
        prior_comments=prior,
        tmp_path=tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_self_correction_prefix_is_refused(tmp_path: Path):
    """PR #86 actual case: ``APPROVED (Correction to previous comment:
    removing internal role reference)`` — this should be an edit on
    the existing comment, not a new comment. Refuse and recommend
    editing."""
    body = (
        "APPROVED (Correction to previous comment: removing internal role reference)\n"
        "\n"
        "Body of the correction.\n"
    )
    result = _run_fixture(body, prior_comments=[], tmp_path=tmp_path)
    assert result.returncode == 1, result.stderr
    assert "correction" in result.stderr.lower()
    assert "edit" in result.stderr.lower()


def test_self_correction_case_insensitive(tmp_path: Path):
    """The ``Correction to previous comment`` detector is
    case-insensitive — agents may produce ``correction``, ``Correction``,
    or ``CORRECTION`` over time."""
    body = "APPROVED (correction to previous comment)\n\nBody.\n"
    result = _run_fixture(body, prior_comments=[], tmp_path=tmp_path)
    assert result.returncode == 1, result.stderr


def test_self_correction_prefix_form(tmp_path: Path):
    """A first line shaped ``Correction: ...`` (no ``APPROVED`` prefix)
    is also a self-correction signal — the body is announcing it
    revises something already on the thread."""
    body = "Correction: the previous note had the wrong commit OID.\n\nBody.\n"
    result = _run_fixture(body, prior_comments=[], tmp_path=tmp_path)
    assert result.returncode == 1, result.stderr


def test_self_correction_message_takes_precedence_over_duplicate(tmp_path: Path):
    """When the body is BOTH a duplicate AND a self-correction, the
    self-correction diagnostic must appear so the caller hears the
    "this should be an edit" message rather than a generic
    "duplicate" error. The duplicate diagnostic may also appear; the
    self-correction one MUST appear."""
    prior = [
        {
            "id": 1,
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:16:25Z",
            "body": "APPROVED\n\nFirst pass.\n",
        }
    ]
    body = "APPROVED (Correction to previous comment: typo)\n\nBody.\n"
    result = _run_fixture(body, prior_comments=prior, tmp_path=tmp_path)
    assert result.returncode == 1
    assert "correction" in result.stderr.lower()


def test_approved_lowercase_not_an_approval(tmp_path: Path):
    """``APPROVED`` is case-sensitive — a body that starts with the
    English word ``approved`` (lower case) is treated as prose, not
    an approval comment. PR #86's approvals all used the upper-case
    token, and only that shape is the convention this lint guards."""
    body = "approved this morning, see notes.\n"
    prior = [
        {
            "id": 1,
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:16:25Z",
            "body": "APPROVED\n\nFirst pass.\n",
        }
    ]
    result = _run_fixture(body, prior_comments=prior, tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr


def test_approved_with_trailing_punctuation_recognized(tmp_path: Path):
    """``APPROVED.\\n``, ``APPROVED:\\n``, ``APPROVED (rationale)\\n``,
    and ``APPROVED — note\\n`` are all approval-shape — the token
    followed by punctuation or whitespace counts. A token preceded by
    text on the same line (``Almost APPROVED``) does not."""
    for trailer in ("", ":", ".", " (rationale)", " — note"):
        body = f"APPROVED{trailer}\n\nDetails.\n"
        prior = [
            {
                "id": 1,
                "user": {"login": AUTHOR},
                "created_at": "2026-06-01T00:16:25Z",
                "body": "APPROVED\n\nFirst pass.\n",
            }
        ]
        result = _run_fixture(body, prior_comments=prior, tmp_path=tmp_path)
        assert result.returncode == 1, f"trailer={trailer!r}: {result.stderr}"


def test_approved_token_not_at_line_start_is_not_an_approval(tmp_path: Path):
    """``Almost APPROVED`` or ``Status: APPROVED`` are prose, not the
    convention-shape approval write. The lint only intercepts comments
    whose first non-blank line *starts* with the token."""
    body = "Status: APPROVED on the second pass.\n"
    prior = [
        {
            "id": 1,
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:16:25Z",
            "body": "APPROVED\n\nFirst pass.\n",
        }
    ]
    result = _run_fixture(body, prior_comments=prior, tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr


def test_leading_blank_lines_tolerated(tmp_path: Path):
    """Editors and template systems often emit a leading blank line.
    The lint considers the first non-blank line, so a body that starts
    with ``\\n\\nAPPROVED\\n`` is still an approval-shape."""
    body = "\n\nAPPROVED\n\nDetails.\n"
    prior = [
        {
            "id": 1,
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:16:25Z",
            "body": "APPROVED\n\nFirst pass.\n",
        }
    ]
    result = _run_fixture(body, prior_comments=prior, tmp_path=tmp_path)
    assert result.returncode == 1, result.stderr


def test_empty_body_passes(tmp_path: Path):
    """An empty stdin is not an approval; the gate passes through."""
    result = _run_fixture("", prior_comments=[], tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr


def test_missing_required_flags_returns_2(tmp_path: Path):
    """Fixture mode requires all four flags. A missing flag is an
    argument error (exit 2), distinct from a refusal (exit 1)."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--head-commit-oid", HEAD_OID],
        input="APPROVED\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, result.stderr


def test_no_args_returns_2(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, result.stderr
    assert "usage" in result.stderr.lower()


def test_unreadable_prior_json_returns_2(tmp_path: Path):
    """A missing or unreadable prior-comments fixture is an I/O error
    (exit 2), distinct from the refusal exit code (1)."""
    missing = tmp_path / "does_not_exist.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--head-commit-oid",
            HEAD_OID,
            "--head-commit-date",
            HEAD_DATE,
            "--author",
            AUTHOR,
            "--prior-comments-json",
            str(missing),
        ],
        input="APPROVED\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, result.stderr


def test_malformed_prior_json_returns_2(tmp_path: Path):
    """A malformed JSON fixture is an I/O error, not a refusal."""
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--head-commit-oid",
            HEAD_OID,
            "--head-commit-date",
            HEAD_DATE,
            "--author",
            AUTHOR,
            "--prior-comments-json",
            str(bad),
        ],
        input="APPROVED\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, result.stderr


def test_diagnostic_names_head_commit_oid(tmp_path: Path):
    """When the gate refuses on a duplicate, the diagnostic includes
    the head commit OID so the caller has the context to either edit
    the existing comment or push a new commit to invalidate the
    duplicate-check window."""
    prior = [
        {
            "id": 1,
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:16:25Z",
            "body": "APPROVED\n\nFirst pass.\n",
        }
    ]
    result = _run_fixture(
        "APPROVED\n\nDuplicate.\n",
        prior_comments=prior,
        tmp_path=tmp_path,
    )
    assert result.returncode == 1
    # Short-form (first 8 chars) is enough for humans; the full OID
    # may also appear and that's fine.
    assert HEAD_OID[:8] in result.stderr


def test_pr_86_actual_thread_flags_subsequent_duplicates(tmp_path: Path):
    """Replay of the PR #86 thread that motivated VER-704. The first
    APPROVED comment is the baseline; comments 2, 3, and 4 are
    duplicates against the same commit and must all trip the gate.
    Comment 3 also carries the self-correction prefix and must trip
    that arm too."""
    pr86_comments = [
        {
            "id": 4588669761,
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:16:25Z",
            "body": "APPROVED\n\nVerified PR #86 docs-only refresh.\n",
        }
    ]
    # Comment 2 (would-be-new): duplicate-only.
    r2 = _run_fixture(
        "APPROVED\n\nThe documentation refresh in PR #86 …\n",
        prior_comments=pr86_comments,
        tmp_path=tmp_path,
    )
    assert r2.returncode == 1, r2.stderr
    assert "4588669761" in r2.stderr
    # Comment 3 (would-be-new): self-correction prefix wins.
    r3 = _run_fixture(
        "APPROVED (Correction to previous comment: removing internal role reference)\n"
        "\n"
        "The documentation refresh …\n",
        prior_comments=pr86_comments,
        tmp_path=tmp_path,
    )
    assert r3.returncode == 1, r3.stderr
    assert "correction" in r3.stderr.lower()
    # Comment 4 (would-be-new): duplicate-only.
    r4 = _run_fixture(
        "APPROVED\n\nDocs-only diagram refresh satisfies all review criteria.\n",
        prior_comments=pr86_comments,
        tmp_path=tmp_path,
    )
    assert r4.returncode == 1, r4.stderr


def test_millisecond_precision_prior_timestamp_recognized(tmp_path: Path):
    """Real-world fixtures occasionally carry millisecond precision
    (``…T00:16:25.123Z``) on the prior comment. A naive lex compare
    against the canonical ``…T00:10:00Z`` head_date would mis-rank the
    timestamps because ``.`` < ``Z`` in the ASCII table; the gate
    must parse both sides as datetimes so the duplicate is still
    flagged."""
    prior = [
        {
            "id": 9001,
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:16:25.123Z",
            "body": "APPROVED\n\nFirst pass.\n",
        }
    ]
    result = _run_fixture(
        "APPROVED\n\nSecond pass.\n",
        prior_comments=prior,
        tmp_path=tmp_path,
    )
    assert result.returncode == 1, result.stderr
    assert "9001" in result.stderr


def test_offset_form_prior_timestamp_recognized(tmp_path: Path):
    """``+00:00`` UTC offset is the RFC-3339 equivalent of ``Z``;
    fixtures that emit the offset form must still gate as duplicates.
    A lex compare would have rejected this because ``+`` (0x2b) sorts
    before ``Z`` (0x5a)."""
    prior = [
        {
            "id": 9002,
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:16:25+00:00",
            "body": "APPROVED\n\nFirst pass.\n",
        }
    ]
    result = _run_fixture(
        "APPROVED\n\nSecond pass.\n",
        prior_comments=prior,
        tmp_path=tmp_path,
    )
    assert result.returncode == 1, result.stderr


def test_correction_colon_alone_on_first_line_is_refused(tmp_path: Path):
    """``Correction:`` alone on the first non-blank line (no further
    text on the same line) is a self-correction announcement and must
    trip the gate. The body content of the correction follows on the
    next line."""
    body = "Correction:\n\nThe previous note had the wrong commit OID.\n"
    result = _run_fixture(body, prior_comments=[], tmp_path=tmp_path)
    assert result.returncode == 1, result.stderr
    assert "correction" in result.stderr.lower()


def test_correction_emdash_alone_on_first_line_is_refused(tmp_path: Path):
    """``Correction —`` with no trailing space (em-dash at end of
    first line) is also a self-correction announcement."""
    body = "Correction —\n\nDetails.\n"
    result = _run_fixture(body, prior_comments=[], tmp_path=tmp_path)
    assert result.returncode == 1, result.stderr


def test_fixture_mode_missing_author_returns_2(tmp_path: Path):
    """Fixture mode requires ``--author`` (no ``gh api user`` fallback,
    because no network in fixture mode). A run without ``--author`` is
    an argument error (exit 2), distinct from a refusal (exit 1)."""
    prior_path = _write_prior(tmp_path, [])
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--head-commit-oid",
            HEAD_OID,
            "--head-commit-date",
            HEAD_DATE,
            "--prior-comments-json",
            str(prior_path),
        ],
        input="APPROVED\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, result.stderr
    assert "--author" in result.stderr


def test_unparseable_head_commit_date_returns_2(tmp_path: Path):
    """A malformed --head-commit-date is an I/O / argument error,
    not a refusal — the gate cannot decide what's a duplicate without
    a valid boundary timestamp."""
    prior_path = _write_prior(tmp_path, [])
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--head-commit-oid",
            HEAD_OID,
            "--head-commit-date",
            "not-a-timestamp",
            "--author",
            AUTHOR,
            "--prior-comments-json",
            str(prior_path),
        ],
        input="APPROVED\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, result.stderr
    assert "head-commit-date" in result.stderr.lower()


def test_pr_mixed_with_fixture_flags_returns_2(tmp_path: Path):
    """``--pr`` is mutually exclusive with every fixture-mode flag.
    Mixing them is an argument error, not a refusal."""
    prior_path = _write_prior(tmp_path, [])
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--pr",
            "92",
            "--head-commit-oid",
            HEAD_OID,
            "--head-commit-date",
            HEAD_DATE,
            "--author",
            AUTHOR,
            "--prior-comments-json",
            str(prior_path),
        ],
        input="APPROVED\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, result.stderr


def test_pr_mode_help_text_documents_invocation():
    """``--help`` must document both modes so the agent driving the
    pre-flight chain can see at a glance that ``--pr <N>`` exists
    alongside the fixture flags. (Help is the discovery surface.)"""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--pr" in result.stdout
    assert "--head-commit-oid" in result.stdout

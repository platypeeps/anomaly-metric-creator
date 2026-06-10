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


def test_correction_no_space_after_colon_is_refused(tmp_path: Path):
    """``Correction:foo`` (no whitespace between the colon and the
    body text) is a common terse form. The regex is documented as
    whitespace-flexible — a missing space must not let the body
    sneak past the gate. Covers Copilot review feedback that the
    earlier ``(?:\\s|$)`` trailer was inconsistent with the
    docstring's whitespace-flexible claim."""
    body = "Correction:typo in previous comment about the OID.\n\nDetails.\n"
    result = _run_fixture(body, prior_comments=[], tmp_path=tmp_path)
    assert result.returncode == 1, result.stderr
    assert "correction" in result.stderr.lower()


def test_correction_no_space_after_dash_is_refused(tmp_path: Path):
    """Mirror of the no-space-after-colon case for ``Correction-foo``
    and ``Correction—foo``. Same lockstep coverage rationale."""
    for body in (
        "Correction-typo in previous comment.\n",
        "Correction—typo in previous comment.\n",
    ):
        result = _run_fixture(body, prior_comments=[], tmp_path=tmp_path)
        assert result.returncode == 1, f"body={body!r}: {result.stderr}"


def test_duplicate_login_compare_is_case_insensitive(tmp_path: Path):
    """GitHub logins are case-insensitive — distinct accounts cannot
    differ only by case. The gate must treat ``--author Sdelmas``
    against a prior ``user.login = sdelmas`` as a same-author
    duplicate, not let the casing mismatch sneak the new comment
    through. Covers Copilot review feedback that the earlier
    case-sensitive compare contradicted GitHub's identity model."""
    prior = [
        {
            "id": 8001,
            "user": {"login": "sdelmas"},
            "created_at": "2026-06-01T00:16:25Z",
            "body": "APPROVED\n\nFirst.\n",
        }
    ]
    result = _run_fixture(
        "APPROVED\n\nSecond.\n",
        prior_comments=prior,
        tmp_path=tmp_path,
        author="Sdelmas",  # different casing on input
    )
    assert result.returncode == 1, result.stderr
    assert "8001" in result.stderr


def test_partial_prior_missing_id_does_not_crash(tmp_path: Path):
    """If a prior fixture entry is missing the ``id`` field,
    ``_diagnose`` would have indexed ``prior['id']`` and crashed with
    KeyError → exit 2. The "partial fixtures don't crash" guarantee
    in the ``_collect_duplicate_priors`` docstring requires those
    entries to be filtered out up front. Confirm by feeding a
    fixture that has one well-formed prior and one ``id``-less
    prior: the gate must refuse on the well-formed prior and ignore
    the partial one (exit 1, not exit 2)."""
    prior = [
        {
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:30:00Z",
            "body": "APPROVED\n\nPartial — no id.\n",
        },
        {
            "id": 9101,
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:16:25Z",
            "body": "APPROVED\n\nWell-formed.\n",
        },
    ]
    result = _run_fixture(
        "APPROVED\n\nSecond.\n",
        prior_comments=prior,
        tmp_path=tmp_path,
    )
    # Refusal (exit 1), not crash (exit 2 with KeyError traceback).
    assert result.returncode == 1, result.stderr
    assert "9101" in result.stderr
    # The partial-fixture entry must not surface in the diagnostic.
    assert "no id" not in result.stderr.lower()


def test_partial_prior_non_string_login_does_not_crash(tmp_path: Path):
    """Defense-in-depth: a prior whose ``user`` is missing entirely
    or whose ``user.login`` is not a string must not crash the gate.
    This is the type-shape backstop for unexpected API drift."""
    prior = [
        {
            "id": 9201,
            # No user key.
            "created_at": "2026-06-01T00:30:00Z",
            "body": "APPROVED\n\nNo user.\n",
        },
        {
            "id": 9202,
            "user": {"login": None},  # wrong type
            "created_at": "2026-06-01T00:30:00Z",
            "body": "APPROVED\n\nNo login string.\n",
        },
        {
            "id": 9203,
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:16:25Z",
            "body": "APPROVED\n\nWell-formed.\n",
        },
    ]
    result = _run_fixture(
        "APPROVED\n\nSecond.\n",
        prior_comments=prior,
        tmp_path=tmp_path,
    )
    assert result.returncode == 1, result.stderr
    assert "9203" in result.stderr


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


def test_multiple_priors_collapse_to_one_diagnostic_line(tmp_path: Path):
    """The PR #86 shape had 5 same-author APPROVED priors. Emitting
    one "edit comment X" line per prior was confusing (N copies of
    the same advice, each pointing at a different id). The gate now
    emits one summary line naming the most recent prior + the count
    of others — the user picks the most recent as the edit target."""
    prior = [
        {
            "id": 1001,
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:16:25Z",
            "body": "APPROVED\n\nFirst pass.\n",
        },
        {
            "id": 1002,
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:30:00Z",
            "body": "APPROVED\n\nSecond pass.\n",
        },
        {
            "id": 1003,
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:45:00Z",
            "body": "APPROVED\n\nThird pass.\n",
        },
    ]
    result = _run_fixture(
        "APPROVED\n\nFourth pass.\n",
        prior_comments=prior,
        tmp_path=tmp_path,
    )
    assert result.returncode == 1
    # Only one "duplicate APPROVED-shape comment" diagnostic line, not three.
    duplicate_lines = [
        line for line in result.stderr.splitlines()
        if "duplicate APPROVED-shape" in line
    ]
    assert len(duplicate_lines) == 1, (
        f"expected one consolidated diagnostic, got {len(duplicate_lines)}:\n"
        + "\n".join(duplicate_lines)
    )
    # The summary names the MOST RECENT prior (1003) and counts the others (2).
    assert "1003" in result.stderr
    assert "2 earlier same-author approval" in result.stderr


def test_multiple_priors_most_recent_independent_of_input_order(tmp_path: Path):
    """``_collect_duplicate_priors`` returns priors in the input
    order (``gh api`` ascending ``created_at``), but the diagnostic's
    "most recent" pick must depend on parsed timestamps, not on
    list-position. Pass priors in REVERSE order and assert the
    diagnostic still names the chronologically-latest prior — proves
    the sort actually fires rather than coincidentally lining up
    with the natural API ordering."""
    prior_reverse = [
        {
            "id": 7003,
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:45:00Z",  # latest
            "body": "APPROVED\n\nThird pass.\n",
        },
        {
            "id": 7002,
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:30:00Z",
            "body": "APPROVED\n\nSecond pass.\n",
        },
        {
            "id": 7001,
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:16:25Z",  # earliest
            "body": "APPROVED\n\nFirst pass.\n",
        },
    ]
    result = _run_fixture(
        "APPROVED\n\nFourth pass.\n",
        prior_comments=prior_reverse,
        tmp_path=tmp_path,
    )
    assert result.returncode == 1
    # 7003 is chronologically latest (00:45) regardless of list position.
    assert "7003" in result.stderr
    # 7001 should NOT be cited as the edit target.
    duplicate_lines = [
        line for line in result.stderr.splitlines()
        if "duplicate APPROVED-shape" in line
    ]
    assert len(duplicate_lines) == 1
    assert "comments/7003" in duplicate_lines[0]
    assert "comments/7001" not in duplicate_lines[0]


def test_single_prior_does_not_say_others(tmp_path: Path):
    """With exactly one prior, the diagnostic should NOT mention any
    "earlier same-author approval" parenthetical — there are no
    others to mention."""
    prior = [
        {
            "id": 2001,
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
    assert "2001" in result.stderr
    assert "earlier same-author approval" not in result.stderr


def test_owner_repo_placeholder_used_in_fixture_mode(tmp_path: Path):
    """Fixture mode doesn't know the real owner/repo, so the
    diagnostic's suggested ``gh api …`` command falls back to the
    ``<owner>/<repo>`` placeholder. (The ``--pr`` production path
    threads the real slug in via ``_fetch_via_gh``; that path is
    structurally tied to the slug being in the return tuple, so the
    placeholder fixture-mode assertion is the test surface that
    distinguishes the two modes.)"""
    prior = [
        {
            "id": 3001,
            "user": {"login": AUTHOR},
            "created_at": "2026-06-01T00:16:25Z",
            "body": "APPROVED\n\nFirst.\n",
        }
    ]
    result = _run_fixture(
        "APPROVED\n\nSecond.\n",
        prior_comments=prior,
        tmp_path=tmp_path,
    )
    assert result.returncode == 1
    assert "<owner>/<repo>" in result.stderr


def test_tty_stdin_exits_2(tmp_path: Path):
    """Running the script with stdin attached to a TTY (no pipe)
    would block on ``sys.stdin.read()`` forever. The gate refuses
    up front with exit 2 and a clear message naming the intended
    pipe pattern.

    Uses the POSIX-only ``pty`` module to fabricate a TTY; the test
    is skipped on Windows because ``pty.openpty`` is unavailable
    there. The production-side guard (``sys.stdin.isatty()``) is
    cross-platform — Windows still benefits from the guard, the
    test just can't fabricate the input shape from there.
    """
    if sys.platform == "win32":
        import pytest
        pytest.skip("pty.openpty is POSIX-only")
    import os
    import pty
    prior_path = _write_prior(tmp_path, [])
    parent_fd, child_fd = pty.openpty()
    try:
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
                str(prior_path),
            ],
            stdin=child_fd,
            capture_output=True,
            text=True,
            timeout=5,
        )
    finally:
        os.close(parent_fd)
        os.close(child_fd)
    assert result.returncode == 2, result.stderr
    assert "TTY" in result.stderr


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


# ---------------------------------------------------------------------------
# --paginate handling: gh emits concatenated JSON arrays for multi-page
# array endpoints ([...][...]), which a single json.loads rejects with
# "Extra data". The gate must keep working on exactly the long comment
# threads it exists for (PR #86 exceeded one page), so the production
# comments fetch parses page-by-page and flattens.
# ---------------------------------------------------------------------------


def _load_tool_module():
    """Import the script as a module so the paginated-array parser and
    ``_gh_pr_comments`` can be unit-tested without a live ``gh``."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(  # noqa: amc-load
        "check_approval_duplicate", SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_paginated_single_page_array():
    tool = _load_tool_module()
    raw = '[{"id": 1}, {"id": 2}]'
    assert tool._parse_paginated_json_arrays(raw, context="t") == [
        {"id": 1},
        {"id": 2},
    ]


def test_parse_paginated_concatenated_pages_flatten_in_order():
    """The exact ``--paginate`` failure shape: two pages concatenated
    back-to-back (with and without whitespace between them) must
    flatten in page order instead of raising ``Extra data``."""
    tool = _load_tool_module()
    for sep in ("", "\n", "  \n  "):
        raw = f'[{{"id": 1}}, {{"id": 2}}]{sep}[{{"id": 3}}]'
        items = tool._parse_paginated_json_arrays(raw, context="t")
        assert [item["id"] for item in items] == [1, 2, 3], repr(raw)


def test_parse_paginated_empty_output_raises():
    """Empty stdout stays fail-closed (exit-2 path), matching the
    pre-existing ``json.loads("")`` behavior — a malformed gh response
    must block the post, not read as 'no prior comments'."""
    import pytest

    tool = _load_tool_module()
    for raw in ("", "   \n"):
        with pytest.raises(OSError):
            tool._parse_paginated_json_arrays(raw, context="t")


def test_parse_paginated_non_list_page_raises():
    import pytest

    tool = _load_tool_module()
    with pytest.raises(OSError):
        tool._parse_paginated_json_arrays('{"id": 1}', context="t")


def test_parse_paginated_trailing_junk_raises():
    import pytest

    tool = _load_tool_module()
    with pytest.raises(OSError):
        tool._parse_paginated_json_arrays('[{"id": 1}] garbage', context="t")


def test_gh_pr_comments_flattens_multi_page_paginate_output(monkeypatch):
    """End-to-end through ``_gh_pr_comments`` with ``_gh`` stubbed to
    return a two-page ``--paginate`` payload: the comments list must
    flatten across pages (the pre-fix ``json.loads`` raised and the
    gate exited 2 on every >100-comment thread)."""
    tool = _load_tool_module()
    pages = '[{"id": 1, "body": "APPROVED"}]\n[{"id": 2, "body": "later"}]'
    captured_args = []

    def _fake_gh(args):
        captured_args.append(args)
        return pages

    monkeypatch.setattr(tool, "_gh", _fake_gh)
    comments = tool._gh_pr_comments("owner/repo", 86)
    assert [c["id"] for c in comments] == [1, 2]
    assert captured_args == [
        ["api", "repos/owner/repo/issues/86/comments", "--paginate"]
    ]

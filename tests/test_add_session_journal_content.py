"""Regression tests for Trellis journal session content generation."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TRELLIS_SCRIPTS = REPO_ROOT / ".trellis" / "scripts"

MARKER = "<!-- trellis-session: v=2 fp=0123456789abcdef -->"


def _add_session_module():
    sys.path.insert(0, str(TRELLIS_SCRIPTS))
    try:
        return importlib.import_module("add_session")
    finally:
        sys.path.pop(0)


def test_summary_only_session_content_has_no_template_placeholders() -> None:
    """A session recorded with nothing but a summary must not leak placeholders.

    The vendored generator omits the optional sections outright rather than
    filling them with prompt text, so the check is that neither the placeholder
    strings nor the empty section headings survive into the journal.
    """
    add_session = _add_session_module()

    content = add_session.generate_session_content(
        14,
        "Review follow-up",
        [("abc1234", "Recorded the follow-up.")],
        "Recorded the follow-up.",
        "2026-07-05",
        MARKER,
        package="amc",
        branch="codex/example",
    )

    assert "(Add details)" not in content
    assert "(Add test results)" not in content
    assert "### Main Changes" not in content
    assert "### Testing" not in content
    assert "### Next Steps" not in content
    assert "Recorded the follow-up." in content


def test_explicit_extra_content_is_preserved() -> None:
    add_session = _add_session_module()

    content = add_session.generate_session_content(
        14,
        "Review follow-up",
        [],
        "Recorded the follow-up.",
        "2026-07-05",
        MARKER,
        extra_content="- Replaced placeholder defaults.",
        tests=["pytest tests/test_add_session_journal_content.py"],
    )

    assert "### Main Changes" in content
    assert "- Replaced placeholder defaults." in content
    assert "- [OK] pytest tests/test_add_session_journal_content.py" in content
    assert "(No commits - planning session)" in content

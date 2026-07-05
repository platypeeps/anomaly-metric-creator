"""Regression tests for Trellis journal session content generation."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TRELLIS_SCRIPTS = REPO_ROOT / ".trellis" / "scripts"


def _add_session_module():
    sys.path.insert(0, str(TRELLIS_SCRIPTS))
    try:
        return importlib.import_module("add_session")
    finally:
        sys.path.pop(0)


def test_summary_only_session_content_has_no_template_placeholders() -> None:
    add_session = _add_session_module()

    content = add_session.generate_session_content(
        14,
        "Review follow-up",
        "abc1234",
        "Recorded the follow-up.",
        add_session.DEFAULT_MAIN_CHANGES,
        "2026-07-05",
        package="amc",
        branch="codex/example",
    )

    assert "(Add details)" not in content
    assert "(Add test results)" not in content
    assert "- Detailed change bullets were not supplied" in content
    assert "- Validation was not recorded for this session." in content


def test_explicit_extra_content_is_preserved() -> None:
    add_session = _add_session_module()

    content = add_session.generate_session_content(
        14,
        "Review follow-up",
        "-",
        "Recorded the follow-up.",
        "- Replaced placeholder defaults.",
        "2026-07-05",
        testing_content="- [OK] pytest tests/test_add_session_journal_content.py",
    )

    assert "- Replaced placeholder defaults." in content
    assert "- [OK] pytest tests/test_add_session_journal_content.py" in content
    assert add_session.DEFAULT_MAIN_CHANGES not in content

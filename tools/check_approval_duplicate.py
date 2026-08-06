#!/usr/bin/env python3
"""Gate ``APPROVED``-shaped PR comments on ``(author, commit OID)``.

PR #86 accumulated five ``APPROVED``-shaped comments from the same
author against the same commit OID, including one
``APPROVED (Correction to previous comment: …)`` self-edit that
should have been an in-place edit of the prior comment rather than a
new comment. VER-704 closes that pattern structurally with this
pre-flight gate; callers chain it into the existing
``gh pr comment --body-file …`` slot the same way they already chain
``check_role_name_leaks.py``::

    python tools/check_approval_duplicate.py --pr <N> < /tmp/body.md \
        && gh pr comment <N> --body-file /tmp/body.md

The gate refuses on two distinct conditions:

1. **Duplicate approval against the same commit OID.** A same-author
   issue comment whose body starts with the literal upper-case token
   ``APPROVED`` and whose ``created_at`` timestamp is at or after the
   PR's current head commit's committer timestamp counts as an
   approval for the current commit. The next same-author
   approval-shape write is rejected; the diagnostic names the
   existing comment id so the caller can switch the write to a
   ``gh api …/issues/comments/<id> -X PATCH -f body=@/tmp/body.md``
   edit on the prior comment.

2. **Self-correction prefix.** A body whose first non-blank line
   carries ``Correction to previous comment`` (case-insensitive) or
   starts with ``Correction:`` / ``Correction -`` / ``Correction —``
   (case-insensitive; ``:``, ``-``, or ``—`` as the separator, no
   space required after) is announcing a correction. The structurally
   correct move is to edit the comment being corrected, not to add a
   fresh one. The diagnostic flags the body and tells the caller to
   PATCH instead of POST. This arm fires independently of the
   duplicate check so the per-PR-#86 ``APPROVED (Correction to
   previous comment: …)`` body trips it even when no prior comment
   exists in the fixture (a regression of the prior-fetch path
   should not silently let a self-correction through).

The boundary timestamp is the right proxy for "for the same commit
OID" because issue comments — the spam path on PR #86 — do not
carry a ``commit_id`` field. When a new commit is pushed, the head's
committer timestamp advances and prior approvals fall before it; the
gate then permits a fresh approval against the new commit. PR
*reviews* (``/reviews``) do carry ``commit_id``; the gate does not
currently consult them because PR #86's leak was all on the
``/issues/{n}/comments`` endpoint and the comment-vs-review choice is
an upstream policy decision out of this script's scope.

Two invocation modes:

* ``--pr <N> [--author <login>]`` — production mode. Reads the PR's
  current head SHA + committer date and the prior issue-comments
  thread via ``gh api`` (see ``_fetch_via_gh``). Author defaults to
  ``gh api user --jq .login`` when unspecified. Body is read from
  stdin.
* ``--head-commit-oid <oid> --head-commit-date <ISO-8601>
  --author <login> --prior-comments-json <path>`` — fixture mode for
  tests and offline CI hooks. Skips every network call. Body is
  read from stdin.

The fixture-mode flags are mutually exclusive with ``--pr``. A
``--pr`` invocation that ALSO supplies any fixture-mode flag fails
with exit ``2`` so the two paths cannot silently mix data sources.

Exit codes:

* ``0`` — clean; the caller may chain ``gh pr comment …``.
* ``1`` — refusal (duplicate approval, self-correction prefix, or
  both). One diagnostic line per finding is written to stderr and
  followed by a one-line footer naming the policy.
* ``2`` — argument error, missing required flag, malformed JSON, or
  ``gh`` failure. Distinct from the refusal exit so callers can
  distinguish "I broke the toolchain" from "you would have spammed
  the PR thread".

Mirrors the structure of ``tools/check_role_name_leaks.py`` and
``tools/check_branch_name.py``: a single source of truth for the
detector predicates (``_is_approval_shape``,
``_self_correction_match``, ``_collect_duplicate_priors``), two
clear invocation modes (``--pr <N>`` production / fixture flags),
and an exit-code priority where I/O errors take precedence over
refusals.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Matches the literal upper-case token ``APPROVED`` at the start of a
# line, optionally followed by punctuation (``.``, ``:``, ``(``,
# ``—``, ``-``, space). Case-sensitive on purpose — the convention
# the gate guards is the upper-case marker token, not the English
# word "approved" used as prose. A token preceded by other text on
# the same line (``Status: APPROVED``) does not match because the
# pattern is anchored to the start of the line.
_APPROVAL_PATTERN = re.compile(r"^APPROVED($|[\s.:()\-—])")

# Self-correction signal #1: the literal phrase the agentic system
# emitted on PR #86. Case-insensitive whitespace-flexible so future
# minor wording drift ("correction to the previous comment") still
# trips the gate.
_CORRECTION_PHRASE = re.compile(
    r"correction\s+to\s+(?:the\s+)?previous\s+comment",
    re.IGNORECASE,
)

# Self-correction signal #2: an opening "Correction:", "Correction -",
# or "Correction —" at the start of the first line — the other
# natural shape a self-correction body takes when the author hasn't
# kept the original ``APPROVED`` prefix. The regex deliberately does
# NOT require any specific character after the separator: it matches
# ``Correction:`` alone on the first line (end-of-line case),
# ``Correction: foo`` (space-separated), and ``Correction:foo``
# (no-space case) uniformly. The separator class is ``:``, ``-``, or
# the em-dash ``—`` — anything immediately following is body text.
# A no-separator opener like ``Correctionx`` does not match because
# the separator class is required.
_CORRECTION_PREFIX = re.compile(
    r"^\s*correction\s*[:\-—]",
    re.IGNORECASE,
)

# Forbidden flag combos. ``--pr`` is mutually exclusive with every
# fixture-mode flag; sharing data sources would silently confuse the
# two paths and is a structural mistake worth refusing.
_FIXTURE_FLAGS: tuple[str, ...] = (
    "--head-commit-oid",
    "--head-commit-date",
    "--prior-comments-json",
)


def _first_non_blank_line(text: str) -> str:
    """Return the body's first non-blank line, or ``""`` if every line
    is blank (or the body itself is empty)."""
    for line in text.splitlines():
        if line.strip():
            return line
    return ""


def _is_approval_shape(body: str) -> bool:
    """Return True iff ``body`` is an APPROVED-shape comment.

    Convention: the first non-blank line begins with the literal
    upper-case token ``APPROVED`` optionally followed by punctuation
    or whitespace. See ``_APPROVAL_PATTERN`` for the exact rule.
    """
    line = _first_non_blank_line(body)
    return bool(_APPROVAL_PATTERN.match(line))


def _self_correction_match(body: str) -> str | None:
    """Return the offending substring if ``body`` is a self-correction,
    else ``None``.

    The first non-blank line is checked against two patterns:
    ``_CORRECTION_PHRASE`` for the ``Correction to previous comment``
    phrasing, and ``_CORRECTION_PREFIX`` for the ``Correction:`` /
    ``Correction —`` opener. Whichever fires first wins; the caller
    only needs *a* signal that the body is a correction, not a list of
    every signal it tripped.
    """
    line = _first_non_blank_line(body)
    if not line:
        return None
    match = _CORRECTION_PHRASE.search(line)
    if match is not None:
        return match.group(0)
    match = _CORRECTION_PREFIX.match(line)
    if match is not None:
        return match.group(0).rstrip()
    return None


def _parse_iso_timestamp(raw: str) -> datetime | None:
    """Parse a GitHub ISO-8601 timestamp into a tz-aware ``datetime``.

    GitHub canonically emits ``YYYY-MM-DDTHH:MM:SSZ``, but
    ``fromisoformat`` accepts the broader RFC-3339 superset (offsets,
    millisecond precision, ``+00:00`` instead of ``Z``). Returning
    ``None`` on parse failure lets the caller treat a partial fixture
    as "does not match" without raising.
    """
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _collect_duplicate_priors(
    *,
    prior_comments: list[dict[str, Any]],
    author: str,
    head_date: str,
) -> list[dict[str, Any]]:
    """Return the subset of ``prior_comments`` that count as
    duplicates against the current head commit.

    A comment counts as a duplicate when all four hold:

    - the entry is a dict carrying string ``id`` (or int ``id``) and
      string ``created_at`` fields — ``_diagnose`` later indexes both
      and would raise ``KeyError`` on a partial entry, so we filter
      those out up front to keep the "partial fixtures don't crash"
      guarantee true;
    - its ``user.login`` matches ``author`` case-insensitively —
      GitHub logins are case-insensitive (distinct accounts cannot
      differ only by case), so an ``--author`` passed with
      non-canonical casing must still match the canonical case the
      API returned;
    - its ``created_at`` ISO-8601 timestamp is >= ``head_date``
      (parsed via ``_parse_iso_timestamp`` rather than lex-compared so
      millisecond precision and ``+00:00`` offsets compare correctly
      against the canonical ``Z`` form GitHub emits today); and
    - its ``body`` is an ``_is_approval_shape`` match.

    ``prior_comments`` is the raw shape ``gh api …/issues/{n}/comments``
    returns: a list of ``{id, user: {login}, created_at, body}``
    dicts. Non-dict entries, missing keys, and unparseable timestamps
    are treated as "does not match" rather than raising, so a partial
    fixture doesn't crash the gate.
    """
    head_dt = _parse_iso_timestamp(head_date)
    if head_dt is None:
        raise OSError(
            f"head-commit-date is not a parseable ISO-8601 timestamp: {head_date!r}"
        )
    author_lc = author.lower()
    duplicates: list[dict[str, Any]] = []
    for comment in prior_comments:
        if not isinstance(comment, dict):
            continue
        # Both fields must be present *and* well-typed before
        # _diagnose can reference them. An int id (gh api shape) or
        # str id (some fixtures) is fine; anything else is skipped.
        cid = comment.get("id")
        if not isinstance(cid, (int, str)) or isinstance(cid, bool):
            continue
        created_raw = comment.get("created_at")
        if not isinstance(created_raw, str):
            continue
        user = comment.get("user") or {}
        login = user.get("login") if isinstance(user, dict) else None
        if not isinstance(login, str) or login.lower() != author_lc:
            continue
        created_dt = _parse_iso_timestamp(created_raw)
        if created_dt is None or created_dt < head_dt:
            continue
        body = comment.get("body")
        if not isinstance(body, str) or not _is_approval_shape(body):
            continue
        duplicates.append(comment)
    return duplicates


def _fetch_via_gh(pr: int, author: str | None) -> tuple[str, str, str, str, list[dict[str, Any]]]:
    """Call ``gh api`` to populate the (head_oid, head_date, author,
    owner_repo, prior_comments) tuple the gate needs.

    Four calls (``--author`` skips the fourth):

    - ``gh pr view <pr> --json headRefOid,url`` for the head SHA and
      the ``<owner>/<repo>`` slug needed by the subsequent endpoints.
    - ``gh api repos/<owner>/<repo>/commits/<oid>`` for the head
      commit's ``committer.date``.
    - ``gh api repos/<owner>/<repo>/issues/<pr>/comments`` for the
      prior comments thread.
    - ``gh api user --jq .login`` (only when ``author`` is None) for
      the current user's login.

    ``owner_repo`` flows through into ``_diagnose`` so the suggested
    ``gh api ...`` PATCH command in any refusal message is
    copy-paste-ready in production mode rather than carrying the
    ``<owner>/<repo>`` placeholders fixture mode falls back to.

    Any non-zero ``gh`` exit code or JSON-decode failure is re-raised
    as ``OSError`` so ``main()`` can surface it as exit ``2`` without
    the network failure being mistaken for a refusal.
    """
    head_oid, owner_repo = _gh_pr_head(pr)
    head_date = _gh_commit_date(owner_repo, head_oid)
    if author is None:
        author = _gh_current_user()
    prior_comments = _gh_pr_comments(owner_repo, pr)
    return head_oid, head_date, author, owner_repo, prior_comments


def _gh(args: list[str]) -> str:
    """Run ``gh`` with ``args`` and return stdout. Raise ``OSError`` on
    any non-zero exit so the caller can surface a clean exit-2
    diagnostic.
    """
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise OSError(f"gh not installed: {exc}") from exc
    if result.returncode != 0:
        raise OSError(
            f"gh {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip() or '<no stderr>'}"
        )
    return result.stdout


# ``gh pr view`` does not expose a ``baseRepository`` JSON field at
# the time of writing (only ``headRepository``), so the owner / repo
# slug is parsed off the PR's HTML ``url`` — that string is the
# canonical pointer to the PR on the *base* repo regardless of fork
# topology, and it cannot drift from the PR's identity.
_PR_URL_PATTERN = re.compile(
    r"https?://[^/]+/(?P<owner>[^/]+)/(?P<name>[^/]+)/pull/\d+"
)


def _gh_json(args: list[str], context: str) -> Any:
    """Run ``gh`` and parse stdout as JSON. ``json.JSONDecodeError`` is
    wrapped as ``OSError`` so the script's documented exit-2 contract
    holds for malformed payloads (e.g. a ``gh`` version that adds a
    leading log line). Single-document endpoints only — paginated
    array endpoints go through ``_parse_paginated_json_arrays``."""
    raw = _gh(args)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OSError(f"{context}: gh returned malformed JSON: {exc}") from exc


def _parse_paginated_json_arrays(raw: str, context: str) -> list[Any]:
    """Parse ``gh api --paginate`` stdout for a JSON-*array* endpoint.

    For array endpoints ``--paginate`` concatenates each page's JSON
    array back-to-back (``[...][...]``), which is not a single valid
    JSON document — ``json.loads`` raises ``Extra data`` as soon as a
    thread exceeds one page (100 comments). Decode page-by-page with
    ``raw_decode`` and flatten, preserving page order. The single-page
    common case is one array and decodes in one pass.

    Fail-closed posture matches ``_gh_json``: empty stdout, a non-array
    page, or trailing junk raise ``OSError`` (exit 2 at ``main()``) so
    the ``&&`` chain blocks the ``gh`` write instead of treating a
    malformed response as "no prior comments".
    """
    decoder = json.JSONDecoder()
    items: list[Any] = []
    idx = 0
    length = len(raw)
    decoded_pages = 0
    while True:
        while idx < length and raw[idx].isspace():
            idx += 1
        if idx >= length:
            break
        try:
            page, idx = decoder.raw_decode(raw, idx)
        except json.JSONDecodeError as exc:
            raise OSError(
                f"{context}: gh returned malformed JSON: {exc}"
            ) from exc
        if not isinstance(page, list):
            raise OSError(
                f"{context}: gh --paginate page {decoded_pages + 1} is "
                f"non-list: {type(page).__name__}"
            )
        items.extend(page)
        decoded_pages += 1
    if decoded_pages == 0:
        raise OSError(f"{context}: gh returned empty output")
    return items


def _gh_pr_head(pr: int) -> tuple[str, str]:
    payload = _gh_json(
        ["pr", "view", str(pr), "--json", "headRefOid,url"],
        context=f"gh pr view {pr}",
    )
    oid = payload.get("headRefOid")
    url = payload.get("url")
    if not (isinstance(oid, str) and isinstance(url, str)):
        raise OSError(f"gh pr view returned unexpected payload: {payload!r}")
    match = _PR_URL_PATTERN.match(url)
    if match is None:
        raise OSError(f"gh pr view returned unparseable url: {url!r}")
    return oid, f"{match.group('owner')}/{match.group('name')}"


def _gh_commit_date(owner_repo: str, oid: str) -> str:
    payload = _gh_json(
        ["api", f"repos/{owner_repo}/commits/{oid}"],
        context=f"gh api commits/{oid}",
    )
    committer = (payload.get("commit") or {}).get("committer") or {}
    date = committer.get("date")
    if not isinstance(date, str):
        raise OSError(f"gh api commits/{oid} missing committer.date: {payload!r}")
    return date


def _gh_current_user() -> str:
    raw = _gh(["api", "user", "--jq", ".login"])
    login = raw.strip()
    if not login:
        raise OSError("gh api user returned empty login")
    return login


def _gh_pr_comments(owner_repo: str, pr: int) -> list[dict[str, Any]]:
    raw = _gh(["api", f"repos/{owner_repo}/issues/{pr}/comments", "--paginate"])
    return _parse_paginated_json_arrays(
        raw, context=f"gh api issues/{pr}/comments"
    )


def _load_prior_comments(path: Path) -> list[dict[str, Any]]:
    """Load and shallow-validate the prior-comments fixture. Raises
    ``OSError`` on read failure or malformed JSON so ``main()`` can
    distinguish I/O errors from refusals."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError(f"prior-comments fixture unreadable: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OSError(f"prior-comments fixture is not valid JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise OSError(
            f"prior-comments fixture must be a JSON list of comments, got {type(payload).__name__}"
        )
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_approval_duplicate.py",
        description=(
            "Gate APPROVED-shaped PR comments on (author, commit OID). "
            "Reads the comment body from stdin; exits 0 clean, 1 on a "
            "duplicate or self-correction, 2 on argument/IO error."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--pr",
        type=int,
        default=None,
        help=(
            "PR number to look up via `gh api`. Mutually exclusive "
            "with the fixture-mode flags."
        ),
    )
    parser.add_argument(
        "--author",
        default=None,
        help=(
            "GitHub login of the comment author. Defaults to "
            "`gh api user --jq .login` under --pr; required under "
            "fixture mode."
        ),
    )
    parser.add_argument(
        "--head-commit-oid",
        default=None,
        help="Fixture mode: PR head commit SHA (40 hex).",
    )
    parser.add_argument(
        "--head-commit-date",
        default=None,
        help=(
            "Fixture mode: ISO-8601 UTC committer timestamp of the "
            "head commit (e.g. 2026-06-01T00:10:00Z). Same-author "
            "APPROVED comments at or after this timestamp count as "
            "duplicates against the current head."
        ),
    )
    parser.add_argument(
        "--prior-comments-json",
        default=None,
        type=Path,
        help=(
            "Fixture mode: path to a JSON file with the prior issue "
            "comments thread (`gh api repos/.../issues/<n>/comments` "
            "output)."
        ),
    )
    return parser


def _validate_modes(args: argparse.Namespace) -> str | None:
    """Return a usage error message if the flag set is malformed,
    else ``None``. Single source of truth for the mode dispatch.
    """
    fixture_pairs: tuple[tuple[str, Any], ...] = (
        ("--head-commit-oid", args.head_commit_oid),
        ("--head-commit-date", args.head_commit_date),
        ("--prior-comments-json", args.prior_comments_json),
    )
    fixture_supplied = [flag for flag, value in fixture_pairs if value is not None]
    if args.pr is not None:
        if fixture_supplied:
            return (
                "--pr cannot be combined with fixture-mode flags "
                f"({', '.join(fixture_supplied)}). Use one mode."
            )
        return None
    # Fixture mode requires every fixture flag plus --author.
    missing = [flag for flag, value in fixture_pairs if value is None]
    if missing:
        return (
            "fixture mode requires all of "
            + ", ".join(_FIXTURE_FLAGS)
            + " (and --author); missing: "
            + ", ".join(missing)
        )
    if args.author is None:
        return "fixture mode requires --author"
    return None


_OWNER_REPO_PLACEHOLDER = "<owner>/<repo>"


def _diagnose(
    body: str,
    *,
    head_oid: str,
    head_date: str,
    author: str,
    prior_comments: list[dict[str, Any]],
    owner_repo: str | None = None,
) -> list[str]:
    """Return zero or more diagnostic lines for the body. Empty list
    means the body is safe to post. Single source of truth so both
    invocation modes produce identical diagnostics on identical
    inputs.

    Emits at most one summary line per arm: one for the
    self-correction match (if any), one for the duplicate-prior set
    (if any). When multiple priors collide on the same commit, the
    duplicate summary names the *most recent* prior id (the natural
    edit target) and the total prior count — emitting one diagnostic
    line per prior would be N copies of the same "edit the existing
    comment" advice with N different ids, which is confusing on the
    PR #86 5-prior shape.

    ``owner_repo`` is the ``<owner>/<repo>`` slug to substitute into
    the suggested ``gh api …`` PATCH command. ``--pr`` mode threads
    the real slug in via ``_fetch_via_gh``; fixture mode leaves it
    ``None`` and the placeholder is used.
    """
    repo_slug = owner_repo or _OWNER_REPO_PLACEHOLDER
    diagnostics: list[str] = []
    correction = _self_correction_match(body)
    if correction is not None:
        diagnostics.append(
            f"self-correction prefix detected ({correction!r}) — "
            "edit the existing comment in place via "
            f"`gh api repos/{repo_slug}/issues/comments/<id> "
            "-X PATCH -f body=@<file>` instead of posting a new "
            "comment."
        )
    if _is_approval_shape(body):
        duplicates = _collect_duplicate_priors(
            prior_comments=prior_comments,
            author=author,
            head_date=head_date,
        )
        if duplicates:
            # Sort by parsed timestamp so "most recent" doesn't depend
            # on API ordering. _parse_iso_timestamp returns a
            # tz-aware datetime; the duplicates list is guaranteed
            # parseable because _collect_duplicate_priors already
            # filtered on it.
            sorted_dups = sorted(
                duplicates,
                key=lambda p: _parse_iso_timestamp(p["created_at"]),
            )
            most_recent = sorted_dups[-1]
            others = len(sorted_dups) - 1
            other_phrase = (
                f" ({others} earlier same-author approval"
                f"{'s' if others != 1 else ''} on this commit also)"
                if others
                else ""
            )
            diagnostics.append(
                f"duplicate APPROVED-shape comment by {author!r} "
                f"against head {head_oid[:8]}: most recent prior is "
                f"id {most_recent['id']} at "
                f"{most_recent['created_at']}{other_phrase} — "
                "edit that comment in place via "
                f"`gh api repos/{repo_slug}/issues/comments/"
                f"{most_recent['id']} -X PATCH -f body=@<file>` "
                "instead of posting a new comment."
            )
    return diagnostics


def _print_violations(diagnostics: list[str]) -> None:
    print("\n".join(diagnostics), file=sys.stderr)
    print(
        "\nApproval-comment writes are gated on (author, commit OID). "
        "Edit the existing comment instead of posting a new one, or "
        "push a new commit to invalidate the duplicate-check window. "
        "Policy lives in this script's module docstring.",
        file=sys.stderr,
    )


def main(argv: list[str]) -> int:
    parser = _build_parser()
    if len(argv) <= 1:
        parser.print_usage(sys.stderr)
        return 2
    try:
        args = parser.parse_args(argv[1:])
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    err = _validate_modes(args)
    if err is not None:
        print(f"check_approval_duplicate: {err}", file=sys.stderr)
        return 2

    if sys.stdin.isatty():
        print(
            "check_approval_duplicate: stdin is a TTY; the body must "
            "be piped in. Typical invocation: "
            "`python tools/check_approval_duplicate.py --pr <N> "
            "< /tmp/body.md && gh pr comment <N> --body-file "
            "/tmp/body.md`.",
            file=sys.stderr,
        )
        return 2
    try:
        body = sys.stdin.read()
    except OSError as exc:
        print(f"check_approval_duplicate: stdin unreadable: {exc}", file=sys.stderr)
        return 2

    try:
        if args.pr is not None:
            head_oid, head_date, author, owner_repo, prior_comments = _fetch_via_gh(
                args.pr, args.author
            )
        else:
            head_oid = args.head_commit_oid
            head_date = args.head_commit_date
            author = args.author
            owner_repo = None
            prior_comments = _load_prior_comments(args.prior_comments_json)
        diagnostics = _diagnose(
            body,
            head_oid=head_oid,
            head_date=head_date,
            author=author,
            prior_comments=prior_comments,
            owner_repo=owner_repo,
        )
    except OSError as exc:
        print(f"check_approval_duplicate: {exc}", file=sys.stderr)
        return 2

    if diagnostics:
        _print_violations(diagnostics)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

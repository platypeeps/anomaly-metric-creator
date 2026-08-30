"""Atomic publication of generated artifacts (temp sibling + os.replace).

Extracted verbatim from ``legacy.py`` (decomposition step 4, pulled
forward into step 3 because ``gauges_impl.write_gauges_csv`` depends on
it; see ``docs/work/archive/2026-07/2026-07-02-legacy-monolith-decomposition/design.md``).
A leaf shared by every generated-artifact writer (per-component CSVs,
anomalies.csv, the report log/trace pair, gauges.csv, combine outputs,
schema.json); ``legacy.py`` re-imports each name so the historic
``legacy.<name>`` surface is unchanged.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path


# Suffix of the temp sibling every generated-artifact writer stages before
# publishing via os.replace. _pre_clean_output_dir sweeps stale ones left
# by a crashed run; the suffix keeps them out of discover_components's
# *.csv glob.
_ATOMIC_TMP_SUFFIX = ".tmp"


@contextlib.contextmanager
def _atomic_artifact_open(path, *, encoding="utf-8", newline=""):
    """Write ``path`` atomically: temp sibling + flush + fsync + ``os.replace``.

    Yields a text handle opened on ``<name>.tmp`` beside ``path`` (same
    directory, therefore same filesystem, so the final ``os.replace`` is
    atomic on POSIX and Windows). On clean exit the temp is flushed, fsynced,
    and renamed onto ``path``: a concurrent reader only ever observes the
    complete previous file or the complete new one — never a truncated or
    momentarily-missing artifact, and a reader holding an already-open handle
    keeps reading the old content to its consistent end. On error the temp is
    removed and any existing ``path`` is left untouched.
    """
    path = Path(path)
    tmp_path = path.with_name(path.name + _ATOMIC_TMP_SUFFIX)
    f = open(tmp_path, "w", encoding=encoding, newline=newline)
    try:
        yield f
        f.flush()
        os.fsync(f.fileno())
    except BaseException:
        f.close()
        tmp_path.unlink(missing_ok=True)
        raise
    else:
        f.close()
        os.replace(tmp_path, path)


def _atomic_write_text(path, text, *, encoding="utf-8"):
    """Atomic counterpart of ``Path.write_text`` for generated artifacts.

    Writes ``text`` verbatim (``newline=""``), so output bytes are identical
    across platforms rather than picking up ``os.linesep`` translation.
    """
    with _atomic_artifact_open(path, encoding=encoding) as f:
        f.write(text)

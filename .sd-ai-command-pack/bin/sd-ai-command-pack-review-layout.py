#!/usr/bin/env python3
"""Resolve the installed pack layout for a consumer's own review guard.

Five consumers each reimplemented the same question -- is this changed path
vendored pack payload or authored source -- and each hardcoded the fat layout
it was written against, which is what blocks their thin conversion. The logic
already existed in the pack's review-scope shell guard; what did not exist
was a way to *get the answer as data*, so a Node or Python guard could use it
without shelling out once per path. That gap, not a missing check, is why the
copies exist.

Two queries:

* ``--path P ...`` classifies paths as ``pack-payload`` or ``authored``;
* ``--resolve NAME`` returns where a pack script actually lives right now.

The second matters more than it looks. Under a thin install every
vendored pack script under ``scripts/`` moves out of the consumer entirely
(this docstring deliberately does not spell the literal glob: the plugin
build's residue gate reads any repository-root pack path in a shipped script as
sibling resolution, and it is right to, even here). So a
consumer file naming one by literal path breaks -- 288 such references were
measured across the eight consumers, 112 of them in a single one.

``mode`` is output, never input: callers never branch on fat versus thin, they
read what this resolved. When neither receipt is present the mode is
``unresolved`` and no classification is emitted at all. Returning every path as
``authored`` would be the friendlier failure and the wrong one -- it is
indistinguishable from a healthy run on a consumer that changed no pack files,
so a broken install would look like a clean one forever.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath
from typing import Any

SCHEMA_VERSION = 1

# `sd_ai_command_pack_lib` is deliberately *not* imported, and the import that
# used to stand here was a latent defect rather than a style choice. It was a
# bare sibling import, which works in both installed layouts only because both
# copies travel together: fat keeps them side by side in `scripts/`, thin moves
# both into the agents-bin directory. This file also installs to
# `.sd-ai-command-pack/bin/`, which is `consumer-config` and therefore survives
# thin conversion -- and the library, being `machine-claude`, does not. There
# the import has no sibling to find and raises, in exactly the consumers where
# this script is the only way left to locate the pack.
#
# So the two names it needed are carried here. `CommandError` is a bare
# exception class; `resolve_state_root` is the five-rung ladder from the
# `sd_ai_command_pack_lib` module. (Named without its path on purpose: the
# plugin build refuses repository-root pack paths in shipped `bin/` scripts,
# because a literal that is correct in a source checkout is wrong everywhere
# the file actually runs.) Duplication that a test checks, as with the
# constant mirrors below:
# `tests/test_review_layout.py` asserts this ladder and the library's agree on
# every rung, so drift fails a gate rather than a consumer.
STATE_HOME_ENV = "SD_AI_COMMAND_PACK_STATE_HOME"  # mirrors the pack library


class CommandError(RuntimeError):
    """Raised when a required external command cannot complete cleanly."""


def resolve_state_root(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    os_name: str | None = None,
    state_home: Path | None = None,
) -> Path:
    """Return the user-local private state root shared by every shipped script.

    Carried copy of `sd_ai_command_pack_lib.resolve_state_root`; see the note
    above for why it cannot be imported. The ladder is: explicit ``state_home``,
    ``SD_AI_COMMAND_PACK_STATE_HOME``, ``XDG_STATE_HOME``, the Windows
    local-app-data location, then the home fallback.
    """

    if state_home is not None:
        candidate = state_home.expanduser()
        if not candidate.is_absolute():
            raise CommandError("state home must be an absolute path")
        return candidate
    env = os.environ if environ is None else environ
    override = env.get(STATE_HOME_ENV, "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise CommandError(f"{STATE_HOME_ENV} must be an absolute path")
        return path
    xdg = env.get("XDG_STATE_HOME", "").strip()
    if xdg:
        path = Path(xdg).expanduser()
        if path.is_absolute():
            return path / "sd-ai-command-pack"
    platform_name = os.name if os_name is None else os_name
    if platform_name == "nt":
        local_app_data = env.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            windows_path = PureWindowsPath(local_app_data)
            if windows_path.is_absolute():
                # Path uses Windows semantics on Windows. Normalizing separators
                # also keeps os_name-injected portability tests deterministic.
                path = Path(str(windows_path).replace("\\", "/"))
                return path / "sd-ai-command-pack" / "state"
    resolved_home = (home or Path.home()).expanduser()
    if not resolved_home.is_absolute():
        raise CommandError("home directory must resolve to an absolute path")
    return resolved_home / ".local" / "state" / "sd-ai-command-pack"

# Mirrors of repo-side constants. `installer/` ships zero files -- it is build
# machinery -- so importing from it would work in this checkout and fail in
# every consumer this script is written for. The duplication is deliberate and
# `tests/test_review_layout.py` asserts each value still equals its original.
INSTALLED_TARGETS_RELATIVE = (
    ".sd-ai-command-pack/installed-targets.txt"  # installer/registry.py
)
PACK_MANIFEST_RELATIVE = ".sd-ai-command-pack/manifest.json"  # installer/registry.py
PROVENANCE_RELATIVE = ".sd-ai-command-pack/provenance.json"  # installer/registry.py
MACHINE_STATE_DIR = "machine"  # installer/machinescope.py
MACHINE_RECEIPT_FILE = "machine-receipt.json"  # installer/machinescope.py

THIN_PIN_MODE = "thin"  # installer/conversion.py
# Every key only a conversion writes; a survivor on a receipt that no longer
# says `thin` means the pin was edited rather than reverted.
PIN_KEYS = (  # installer/provenance.py
    "mode",
    "platforms",
    "consumer",
    "settingsAdditions",
    "forced",
    "retired",
)

TARGETS_FILE_ENV = "SD_AI_COMMAND_PACK_TARGETS_FILE"

MODE_FAT = "fat"
MODE_THIN = "thin"
MODE_UNRESOLVED = "unresolved"

CATEGORY_PAYLOAD = "pack-payload"
CATEGORY_AUTHORED = "authored"

# Prefixes that are pack-owned regardless of receipt membership: a pack version
# may add metadata under `.sd-ai-command-pack/` that an older receipt cannot
# list, and Trellis runtime paths are copied content the receipt never covers.
# Matching these by prefix is what keeps a receipt-listed classification from
# silently narrowing when the pack grows.
COPIED_PREFIXES = (
    ".sd-ai-command-pack/",
    ".trellis/scripts/",
    ".trellis/agents/",
)


class LayoutError(Exception):
    """A resolution that failed with a reason worth printing."""


def family_roots(home: Path, environ: dict[str, str]) -> dict[str, Path]:
    """Absolute destination root per machine family.

    Mirror of `installer/machinepayload.py:family_roots`; see the constant block
    above for why this is a copy rather than an import. OpenCode's root is
    XDG-derived rather than a hardcoded `~/.config`, which is the one rung a
    plausible-looking reimplementation gets wrong.
    """

    agents = home / ".agents"
    config_home = home / ".config"
    xdg = environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        candidate = Path(xdg).expanduser()
        if candidate.is_absolute():
            config_home = candidate
    return {
        "agents-skills": agents / "skills",
        "agents-bin": agents / "bin",
        "agents-docs": agents / "docs",
        "gemini-commands": home / ".gemini" / "commands",
        "opencode-commands": config_home / "opencode" / "commands",
    }


def home_from(environ: dict[str, str]) -> Path:
    """The home directory `environ` describes, falling back to the process's.

    `Path.home()` reads the *process* environment, so calling it directly would
    make `environ` a decorative parameter: a caller that passes a HOME would
    still get this process's. The fallback is kept for Windows, where home comes
    from `USERPROFILE` and an absent HOME is normal rather than an error.
    """

    value = environ.get("HOME", "").strip()
    if value:
        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            return candidate
    return Path.home()


class Layout:
    """One resolved installation: its mode, its receipt, and what it holds."""

    def __init__(
        self,
        mode: str,
        *,
        receipt: Path | None = None,
        targets: frozenset[str] = frozenset(),
        machine_files: tuple[dict[str, Any], ...] = (),
        reason: str | None = None,
    ) -> None:
        self.mode = mode
        self.receipt = receipt
        self.targets = targets
        self.machine_files = machine_files
        self.reason = reason

    @property
    def resolved(self) -> bool:
        return self.mode != MODE_UNRESOLVED


def _read_targets(path: Path) -> frozenset[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise LayoutError(
            f"cannot read installed-targets receipt {path}: {error}"
        ) from None
    entries = set()
    for line in text.splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            entries.add(entry)
    return frozenset(entries)


def _read_machine_receipt(path: Path) -> tuple[dict[str, Any], ...]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise LayoutError(f"cannot read machine receipt {path}: {error}") from None
    except json.JSONDecodeError as error:
        raise LayoutError(
            f"machine receipt {path} is not valid JSON: {error}"
        ) from None
    files = document.get("files")
    if not isinstance(files, list):
        raise LayoutError(f"machine receipt {path} has no files array")
    return tuple(entry for entry in files if isinstance(entry, dict))


PIN_STATE_FAT = "fat"
PIN_STATE_THIN = "thin"
PIN_STATE_MALFORMED = "malformed"


def _inside(root: Path, path: Path) -> bool:
    """Whether `path` really lands inside `root` once symlinks are followed.

    Mirror of `installer/manifest.py:validate_resolved_target_path`, which the
    installer applies to exactly these three receipt paths. Joining a relative
    path to a root does not keep it there: a `.sd-ai-command-pack` symlinked
    elsewhere makes every receipt under it another tree's, and this resolver
    hands its answer to a caller whose next move is to execute it. Both sides
    are resolved because a root reached through a symlink is ordinary -- macOS
    `/tmp` is one -- and comparing a resolved child against an unresolved
    parent would reject those.
    """

    try:
        return path.resolve(strict=False).is_relative_to(root.resolve())
    except (OSError, RuntimeError):
        return False


def _receipt_declares_thin(path: Path) -> bool:
    """True only when `path` legibly says `mode: "thin"`.

    Deliberately narrower than `pin_state`: this is the *second* witness, so it
    answers only the question it can answer well. A symlink is refused rather
    than followed, matching the install preflight's refusal on the same paths.
    """

    if path.is_symlink() or not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("mode") == THIN_PIN_MODE


def pin_state(root: Path) -> str:
    """What this consumer's own receipts say it is: fat, thin, or malformed.

    Mirror of `installer/conversion.py:thin_pin_state`, including its two
    witnesses and their order: `manifest.json` is written before
    `provenance.json`, so when only one survives it is the earlier one.
    Unreadable bytes carrying no thin evidence are `fat`, because that is what
    they have always been -- a mangled receipt is a recoverable fat state the
    installer rebuilds. Thin-only pin keys under anything but `mode: "thin"`
    are `malformed`: that receipt was thin and something has since edited it.
    """

    manifest = root / PACK_MANIFEST_RELATIVE
    if _inside(root, manifest) and _receipt_declares_thin(manifest):
        return PIN_STATE_THIN
    provenance = root / PROVENANCE_RELATIVE
    if not _inside(root, provenance):
        return PIN_STATE_FAT
    if provenance.is_symlink() or not provenance.is_file():
        return PIN_STATE_FAT
    try:
        payload = json.loads(provenance.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return PIN_STATE_FAT
    if not isinstance(payload, dict):
        return PIN_STATE_FAT
    if payload.get("mode") == THIN_PIN_MODE:
        return PIN_STATE_THIN
    if any(key in payload for key in PIN_KEYS):
        return PIN_STATE_MALFORMED
    return PIN_STATE_FAT


def resolve_layout(
    root: Path,
    *,
    environ: dict[str, str] | None = None,
) -> Layout:
    """Which installation this consumer has, by the documented ladder.

    Order is override, recorded pin, vendored, machine, nothing -- and the
    override stays first because consumers set it today and this must not
    change what they already get.

    The pin outranks the vendored receipt because that receipt's *existence*
    does not mean what it looks like it means. A conversion does not delete
    `installed-targets.txt`; it rewrites it down to the residual slice the
    repository still holds. Deciding mode by existence therefore called every
    converted consumer fat and then refused to locate any pack script, since
    the names it looked for had just been removed from the very file it was
    reading -- measured on `rwbp-coordinator` at 0.71.14, which resolved `fat`
    while both of its own receipts said `thin`.

    A thin consumer keeps that residual receipt in `targets`, because those
    rows are pack payload the repository genuinely still carries and
    `classify` is right to consult them. Only `resolve_script` branches on
    mode, and it reads the machine receipt.
    """

    env = dict(os.environ if environ is None else environ)

    override = env.get(TARGETS_FILE_ENV, "").strip()
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return Layout(MODE_FAT, receipt=path, targets=_read_targets(path))
        raise LayoutError(f"{TARGETS_FILE_ENV} names a missing file: {path}")

    pinned = pin_state(root)
    if pinned == PIN_STATE_MALFORMED:
        return Layout(
            MODE_UNRESOLVED,
            reason=(
                f"{PROVENANCE_RELATIVE} under {root} carries thin pin keys "
                "without a thin mode; repair or reinstall the pack rather "
                "than resolving against a receipt that contradicts itself"
            ),
        )

    # The same containment rule as the pin, and for the same reason: this file
    # is what `classify` answers from, so a receipt reached through a symlinked
    # `.sd-ai-command-pack` would describe another repository's install as this
    # one's. Out-of-tree means "no vendored receipt here" rather than an error,
    # which leaves the ladder to report `unresolved` with its own reason.
    vendored = root / INSTALLED_TARGETS_RELATIVE
    usable = _inside(root, vendored) and vendored.is_file()
    residual = _read_targets(vendored) if usable else frozenset()
    if pinned == PIN_STATE_FAT and usable:
        return Layout(MODE_FAT, receipt=vendored, targets=residual)

    # `resolve_state_root` is called, never re-derived: expanding
    # `~/.local/state` directly would skip SD_AI_COMMAND_PACK_STATE_HOME,
    # XDG_STATE_HOME, and the Windows rung -- four of its five.
    try:
        state_root = resolve_state_root(environ=env, home=home_from(env))
    except CommandError as error:
        return Layout(MODE_UNRESOLVED, reason=f"cannot resolve state root: {error}")

    machine_receipt = Path(state_root) / MACHINE_STATE_DIR / MACHINE_RECEIPT_FILE
    if machine_receipt.is_file():
        return Layout(
            MODE_THIN,
            receipt=machine_receipt,
            targets=residual,
            machine_files=_read_machine_receipt(machine_receipt),
        )

    if pinned == PIN_STATE_THIN:
        # Falling back to the residual receipt here is exactly the defect this
        # ladder exists to prevent: it would report `fat` and then fail to find
        # a script whose name conversion had already removed from that file.
        return Layout(
            MODE_UNRESOLVED,
            reason=(
                f"{root} is pinned thin but the machine install is missing: "
                f"no {machine_receipt}"
            ),
        )

    return Layout(
        MODE_UNRESOLVED,
        reason=(
            f"no pack installation found: neither {INSTALLED_TARGETS_RELATIVE} "
            f"under {root} nor {machine_receipt}"
        ),
    )


def classify(layout: Layout, path: str) -> str:
    """Whether one repo-relative path is pack payload or the consumer's own."""

    # `lstrip("./")` would take a character *set*, silently turning
    # `.sd-ai-command-pack/x` into `sd-ai-command-pack/x` and classifying every
    # dotfile path as authored.
    normalized = path.strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized in layout.targets:
        return CATEGORY_PAYLOAD
    for prefix in COPIED_PREFIXES:
        if normalized.startswith(prefix):
            return CATEGORY_PAYLOAD
    return CATEGORY_AUTHORED


def command_surface(layout: Layout) -> list[dict[str, Any]]:
    """Installed command and skill paths, grouped by command name.

    Enumerated from the receipt rather than from a literal table, because a
    literal one passes every test written against the pack that produced it and
    then silently describes the wrong install everywhere else.
    """

    grouped: dict[str, list[str]] = {}
    for target in sorted(layout.targets):
        if "/commands/" not in target and "/skills/" not in target:
            continue
        name = _command_name(target)
        if name is None:
            continue
        grouped.setdefault(name, []).append(target)
    return [{"name": name, "paths": paths} for name, paths in sorted(grouped.items())]


def _command_name(target: str) -> str | None:
    """The SD command a receipt path belongs to, or None when it is not one."""

    parts = target.split("/")
    for index, part in enumerate(parts):
        if part in {"commands", "skills"} and index + 1 < len(parts):
            leaf = parts[index + 1]
            if leaf == "sd":  # `.claude/commands/sd/<name>.md`
                if index + 2 < len(parts):
                    return parts[index + 2].rsplit(".", 1)[0]
                return None
            if leaf.startswith("sd-"):
                return leaf[len("sd-") :].rsplit(".", 1)[0]
            return None
    return None


def resolve_script(
    layout: Layout, name: str, *, root: Path, environ: dict[str, str]
) -> Path:
    """Where a pack script lives in this installation.

    Refuses rather than guesses. A synthesized path is worse than an error here
    because the caller's next move is to execute it.
    """

    if layout.mode == MODE_FAT:
        target = f"scripts/{name}"
        if target not in layout.targets:
            raise LayoutError(f"{name} is not listed in {layout.receipt}")
        return root / target

    if layout.mode == MODE_THIN:
        roots = family_roots(home_from(environ), environ)
        for entry in layout.machine_files:
            if entry.get("path") != name:
                continue
            family = entry.get("family")
            family_root = roots.get(str(family))
            if family_root is None:
                raise LayoutError(f"{name} has unknown destination family {family!r}")
            return family_root / name
        raise LayoutError(f"{name} is not listed in {layout.receipt}")

    raise LayoutError(layout.reason or "pack installation is unresolved")


def build_report(
    layout: Layout,
    paths: list[str],
    *,
    root: Path,
) -> dict[str, Any]:
    report: dict[str, Any] = {"schemaVersion": SCHEMA_VERSION, "mode": layout.mode}
    if not layout.resolved:
        report["reason"] = layout.reason
        return report
    report["receipt"] = _render_receipt(layout.receipt, root)
    report["paths"] = [
        {"path": path, "category": classify(layout, path)} for path in paths
    ]
    report["surface"] = {"commands": command_surface(layout)}
    return report


def _render_receipt(receipt: Path | None, root: Path) -> str | None:
    """The receipt path, repo-relative when it is inside the repo.

    Absolute machine paths in output become absolute machine paths in whatever
    the caller writes down, which the pack's own documentation guard rejects.
    """

    if receipt is None:
        return None
    try:
        return str(receipt.relative_to(root))
    except ValueError:
        return str(receipt)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        dest="paths",
        help="repo-relative path to classify; repeatable",
    )
    parser.add_argument("--resolve", help="pack script name to locate")
    parser.add_argument("--json", action="store_true", help="emit JSON (default)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.expanduser().resolve()
    environ = dict(os.environ)

    try:
        layout = resolve_layout(root, environ=environ)
        if args.resolve:
            resolved = resolve_script(layout, args.resolve, root=root, environ=environ)
            document: dict[str, Any] = {
                "schemaVersion": SCHEMA_VERSION,
                "mode": layout.mode,
                "name": args.resolve,
                "path": str(resolved),
            }
        else:
            document = build_report(layout, list(args.paths), root=root)
    except LayoutError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(json.dumps(document, indent=2, sort_keys=True))
    return 0 if layout.resolved else 1


if __name__ == "__main__":
    raise SystemExit(main())

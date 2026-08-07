#!/usr/bin/env python3
"""Guard that every repository lint is actually exercised by CI.

A `tools/check_*.py` lint is only as good as the lanes it runs in. This repo's
CI picks a lane from the changed paths (`scripts/classify-ci-changes.sh`), and
the lanes do not all run the same jobs:

    changes                 unconditional
    lightweight_readiness   needs.changes.outputs.lightweight_only == 'true'
    quick_check/test_*      needs.changes.outputs.app_required == 'true' && ...

CI never runs `pre-commit`, so a hook in `.pre-commit-config.yaml` buys nothing
on a pull request. A lint is exercised on a PR only by an explicit CI step or
by a test that runs it over the live tree from inside a test job.

That produces a failure mode that is invisible by inspection: a lint whose
watched files select a lane in which neither its CI step nor its test job runs.
It happened. `tools/check_task_criteria_commands.py` watches
`.trellis/tasks/**/*.md`; a PR touching only those files is `lightweight_only`,
which skips every test job, so the lint's own `test_live_task_tree_is_clean`
never ran for exactly the PR shape the lint exists to police. It was enforced
in appearance only until the CI step was added.

This guard makes that class mechanically checkable.

Model
-----
Two lanes matter, because they are the two that gate whole jobs:

    LIGHT   the PR is lightweight_only -- every test job is skipped
    APP     the PR is app_required     -- lightweight_readiness is skipped

Every lint can reach APP: adding any application source file to a PR forces it,
whatever else that PR touches. So APP coverage is required of every lint.

A lint reaches LIGHT only if its own watched files, alone, classify as
`lightweight_only`. That is computed by running the real classifier over the
real tracked files the hook's `files:` regex matches -- not by inverting the
regex, which is not generally possible.

A lint is covered in a lane when at least one of these runs in it:

    * a CI step in an unconditionally-executed job (covers both lanes)
    * a CI step in a job gated on that lane
    * a live-tree test (covers APP only -- test jobs do not run in LIGHT)

Live-tree tests are found structurally, not by name. A test file owns a lint
when it assigns that lint's path from `REPO_ROOT`, the repo-wide convention:

    SCRIPT = REPO_ROOT / "tools" / "check_workflow_pip.py"

Within that file a live-tree test is a zero-argument `def test_*` whose body
references `REPO_ROOT` or another `REPO_ROOT`-derived module constant. Zero
arguments is the load-bearing part: a test taking `tmp_path` is building a
synthetic tree, so it proves nothing about this repository. `SCRIPT` itself is
excluded from the constant set, so `test_no_args_exits_two` -- which runs the
lint with no operands and only touches `SCRIPT` -- is correctly not counted.
Naming is deliberately not used: the live tests in this repo are variously
`test_live_*`, `test_real_repo_*`, and `test_real_test_tree_*`.

Escape hatch
------------
Put `# guard-ci-coverage: allow <reason>` on or directly above a hook's
`- id:` line to exempt it. Use this when a lint is genuinely covered by a
mechanism this guard cannot see -- not to silence a real gap. The reason is
required and is printed in `--list` output.

Scope
-----
Only local hooks whose `entry` invokes a `tools/check_*.py` script and which
carry a `files:` pattern are checked. A hook with no `files:` pattern runs
against whatever pre-commit passes it and has no fixed watched set to classify;
a hook whose pattern matches no tracked file is reported and skipped.

Invocation
----------
    python tools/check_guard_ci_coverage.py            # check the repository
    python tools/check_guard_ci_coverage.py --list     # per-lint coverage table
    python tools/check_guard_ci_coverage.py --repo DIR # check another checkout

Exit codes: 0 clean, 1 violations found, 2 structural error (missing or
unparseable input, or a CI job whose `if:` this guard cannot classify --
which is failed loudly rather than assumed safe).
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - exercised via exit code 2
    print(
        "error: PyYAML is required; install the dev extra "
        "(`uv pip install -e '.[dev]'`)",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

PRE_COMMIT = Path(".pre-commit-config.yaml")
WORKFLOW = Path(".github/workflows/ci.yml")
CLASSIFIER = Path("scripts/classify-ci-changes.sh")
TESTS_DIR = Path("tests")

_ALLOW_RE = re.compile(r"#\s*guard-ci-coverage:\s*allow\s+(?P<reason>\S.*?)\s*$")
_TOOL_RE = re.compile(r"tools/check_[a-z_0-9]+\.py")
_SCRIPT_CONST_RE = re.compile(r"'(check_[a-z_0-9]+\.py)'")

# Gate kinds. A job runs in LIGHT when the PR is lightweight_only, in APP when
# it is app_required; ALWAYS runs in both.
ALWAYS = "always"
LIGHT = "light"
APP = "app"

# `classify-ci-changes.sh` caps how many paths are worth passing: the lane only
# depends on which *kinds* of path are present, so a prefix is sufficient and
# keeps the argv well under the platform limit.
_MAX_CLASSIFY_PATHS = 300


@dataclass
class Guard:
    """One pre-commit hook that runs a `tools/check_*.py` lint."""

    hook_id: str
    tool: str
    pattern: str
    allow_reason: str | None = None
    matched: list[str] = field(default_factory=list)
    reaches_light: bool = False
    gates: set[str] = field(default_factory=set)
    live_tests: list[str] = field(default_factory=list)

    @property
    def covered_in_app(self) -> bool:
        return ALWAYS in self.gates or APP in self.gates or bool(self.live_tests)

    @property
    def covered_in_light(self) -> bool:
        if not self.reaches_light:
            return True
        return ALWAYS in self.gates or LIGHT in self.gates


class StructuralError(Exception):
    """Input is missing, unparseable, or cannot be reasoned about."""


def _load_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise StructuralError(f"{path}: not found") from None
    except (OSError, yaml.YAMLError) as exc:
        raise StructuralError(f"{path}: {exc}") from None


def _allow_markers(path: Path) -> dict[str, str]:
    """Map hook id -> exemption reason, read from raw YAML comments.

    `yaml.safe_load` discards comments, so the marker is recovered from the
    text: a marker on the `- id:` line itself, or on the comment lines
    immediately above it.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise StructuralError(f"{path}: {exc}") from None

    markers: dict[str, str] = {}
    for index, line in enumerate(lines):
        id_match = re.match(r"\s*-\s*id:\s*(?P<hook>[\w.-]+)", line)
        if not id_match:
            continue
        hook = id_match.group("hook")
        candidates = [line]
        cursor = index - 1
        while cursor >= 0 and lines[cursor].strip().startswith("#"):
            candidates.append(lines[cursor])
            cursor -= 1
        for candidate in candidates:
            allow = _ALLOW_RE.search(candidate)
            if allow:
                markers[hook] = allow.group("reason")
                break
    return markers


def collect_guards(root: Path) -> list[Guard]:
    config = _load_yaml(root / PRE_COMMIT)
    if not isinstance(config, dict) or "repos" not in config:
        raise StructuralError(f"{PRE_COMMIT}: no 'repos' mapping")
    markers = _allow_markers(root / PRE_COMMIT)

    guards: list[Guard] = []
    for repo in config["repos"]:
        for hook in repo.get("hooks", []):
            entry = hook.get("entry", "")
            tool_match = _TOOL_RE.search(entry)
            if not tool_match:
                continue
            pattern = hook.get("files")
            if not pattern:
                continue
            hook_id = hook.get("id", "<unnamed>")
            guards.append(
                Guard(
                    hook_id=hook_id,
                    tool=tool_match.group(0),
                    pattern=pattern,
                    allow_reason=markers.get(hook_id),
                )
            )
    return guards


def _gate_of(condition: object) -> str:
    """Classify a job-level `if:` into the lane it runs in."""
    if condition is None:
        return ALWAYS
    text = str(condition)
    has_light = "lightweight_only == 'true'" in text
    has_app = "app_required == 'true'" in text
    if has_light and not has_app:
        return LIGHT
    if has_app and not has_light:
        return APP
    raise StructuralError(
        f"{WORKFLOW}: cannot classify job condition into a CI lane: {text!r}"
    )


def collect_job_gates(root: Path) -> dict[str, set[str]]:
    """Map `tools/check_*.py` -> the set of lane gates whose jobs run it."""
    workflow = _load_yaml(root / WORKFLOW)
    if not isinstance(workflow, dict) or "jobs" not in workflow:
        raise StructuralError(f"{WORKFLOW}: no 'jobs' mapping")

    gates: dict[str, set[str]] = {}
    for job in workflow["jobs"].values():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps") or []
        tools = {
            tool
            for step in steps
            if isinstance(step, dict)
            for tool in _TOOL_RE.findall(step.get("run") or "")
        }
        if not tools:
            continue
        # Only classify a condition for a job that actually runs a lint, so an
        # unrelated job with an exotic `if:` is not a structural error.
        gate = _gate_of(job.get("if"))
        for tool in tools:
            gates.setdefault(tool, set()).add(gate)
    return gates


def collect_live_tests(root: Path) -> dict[str, list[str]]:
    """Map `check_*.py` basename -> live-tree test ids that exercise it."""
    tests_dir = root / TESTS_DIR
    if not tests_dir.is_dir():
        raise StructuralError(f"{TESTS_DIR}: not a directory")

    live: dict[str, list[str]] = {}
    for path in sorted(tests_dir.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            raise StructuralError(f"{path}: {exc}") from None

        script_name: str | None = None
        script_const: str | None = None
        derived: set[str] = set()
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            source = ast.unparse(node.value)
            if "REPO_ROOT" not in source:
                continue
            derived.add(target.id)
            match = _SCRIPT_CONST_RE.search(source)
            if match and "'tools'" in source:
                script_name = match.group(1)
                script_const = target.id
        if script_name is None:
            continue

        # The script path itself is not tree evidence: a test that only touches
        # it (`test_no_args_exits_two`) never looks at this repository.
        tree_consts = derived - {script_const}
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if not node.name.startswith("test_") or node.args.args:
                continue
            body = ast.unparse(node)
            if "REPO_ROOT" in body or any(const in body for const in tree_consts):
                live.setdefault(script_name, []).append(f"{path.name}::{node.name}")
    return live


def _tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise StructuralError(f"git ls-files failed: {result.stderr.strip()}")
    return result.stdout.split()


def _classify(root: Path, paths: list[str]) -> dict[str, str]:
    if not (root / CLASSIFIER).is_file():
        raise StructuralError(f"{CLASSIFIER}: not found")
    result = subprocess.run(
        ["bash", str(CLASSIFIER), "--", *paths[:_MAX_CLASSIFY_PATHS]],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise StructuralError(f"{CLASSIFIER} failed: {result.stderr.strip()}")
    return dict(
        line.split("=", 1)
        for line in result.stdout.strip().splitlines()
        if "=" in line
    )


def analyse(root: Path) -> list[Guard]:
    guards = collect_guards(root)
    gates = collect_job_gates(root)
    live = collect_live_tests(root)
    tracked = _tracked_files(root)

    for guard in guards:
        try:
            matcher = re.compile(guard.pattern)
        except re.error as exc:
            raise StructuralError(
                f"{PRE_COMMIT}: hook {guard.hook_id} has an invalid files "
                f"pattern {guard.pattern!r}: {exc}"
            ) from None
        guard.matched = [path for path in tracked if matcher.search(path)]
        guard.gates = gates.get(guard.tool, set())
        guard.live_tests = live.get(Path(guard.tool).name, [])
        if guard.matched:
            outputs = _classify(root, guard.matched)
            guard.reaches_light = outputs.get("lightweight_only") == "true"
    return guards


def _describe(guard: Guard) -> str:
    gates = ",".join(sorted(guard.gates)) or "-"
    live = str(len(guard.live_tests))
    lanes = "LIGHT+APP" if guard.reaches_light else "APP"
    return (
        f"  {guard.hook_id:32s} lanes={lanes:9s} ci_jobs={gates:12s} "
        f"live_tests={live:2s} files={len(guard.matched)}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_guard_ci_coverage.py",
        description="Verify every repository lint runs in the CI lanes its files select.",
    )
    parser.add_argument("--repo", default=".", help="repository root (default: .)")
    parser.add_argument(
        "--list",
        action="store_true",
        help="print the per-lint coverage table and exit 0",
    )
    args = parser.parse_args(argv)
    root = Path(args.repo).resolve()

    try:
        guards = analyse(root)
    except StructuralError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not guards:
        print(
            f"error: {PRE_COMMIT} declares no tools/check_*.py hook with a "
            "files: pattern; this guard has nothing to check and is silently "
            "passing",
            file=sys.stderr,
        )
        return 2

    if args.list:
        for guard in guards:
            suffix = ""
            if guard.allow_reason:
                suffix = f"  [allowed: {guard.allow_reason}]"
            elif not guard.matched:
                suffix = "  [pattern matches no tracked file]"
            print(_describe(guard) + suffix)
        return 0

    violations: list[str] = []
    for guard in guards:
        if guard.allow_reason or not guard.matched:
            continue
        if not guard.covered_in_app:
            violations.append(
                f"{PRE_COMMIT}: {guard.hook_id} ({guard.tool}) is not exercised "
                "on an app-required pull request. Its files can always appear "
                "alongside an application source change, which skips "
                "lightweight_readiness, and it has neither a step in an "
                "unconditional or app-gated CI job nor a live-tree test. Add "
                "the step to the fast-guard block in the `changes` job, or add "
                "a zero-argument test over REPO_ROOT to its test file."
            )
        if not guard.covered_in_light:
            where = (
                f"it runs only in app-gated CI jobs ({','.join(sorted(guard.gates))})"
                if guard.gates
                else "it runs in no CI job at all"
            )
            violations.append(
                f"{PRE_COMMIT}: {guard.hook_id} ({guard.tool}) watches files "
                "that select the lightweight lane, where every test job is "
                f"skipped, but {where}. A pull request touching only those "
                "files would merge unchecked. Add the step to the fast-guard "
                "block in the `changes` job."
            )

    for violation in violations:
        print(violation, file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Guard that every repository lint is actually exercised by CI.

A `tools/check_*.py` lint is only as good as the lanes it runs in. This repo's
CI picks a lane from the changed paths (`scripts/classify-ci-changes.sh`) and
from the event, and the lanes do not all run the same jobs.

CI never runs `pre-commit`, so a hook in `.pre-commit-config.yaml` buys nothing
on a pull request. A lint is exercised on a PR only by an explicit CI step, or
by a test that runs it over the live tree from inside a test job that actually
executes that test file.

That produces a failure mode invisible by inspection: a lint whose watched
files select a lane in which neither its CI step nor its test runs. It
happened. `tools/check_task_criteria_commands.py` watches
`.trellis/tasks/**/*.md`; a PR touching only those files is `lightweight_only`,
which skips every test job, so the lint's own `test_live_task_tree_is_clean`
never ran for exactly the PR shape the lint exists to police. It was enforced
in appearance only until the CI step was added.

This guard makes that class mechanically checkable.

Lanes
-----
A pull request lands in exactly one of three lanes. Two independent `changes`
outputs decide it -- the classified paths, and whether the full matrix was
requested (`full_ci_requested`, set by the event, the `full-ci` label, armed
auto-merge, or a dependency/workflow change):

    LIGHT   lightweight_only == 'true'                        no test job runs
    QUICK   app_required == 'true' && full_ci_requested != 'true'
    FULL    app_required == 'true' && full_ci_requested == 'true'

The QUICK/FULL split is load-bearing and easy to miss: `quick_check` runs an
explicit, hand-written list of test files, while `test_light`/`test_heavy`
partition the whole suite between them. So a live-tree test covers FULL always,
but covers QUICK only if its file is named in that list. Collapsing QUICK and
FULL into one "app" lane -- as this guard originally did -- silently credits a
lint with coverage it does not have on an ordinary `synchronize` push.

Every lint can reach QUICK and FULL: adding any application source file to a PR
forces `app_required`, whatever else that PR touches, and either matrix may
then run. So both are required of every lint.

A lint reaches LIGHT only if its own watched files, alone, classify as
`lightweight_only`. That is computed by running the real classifier over the
real tracked files the hook selects -- not by inverting the regex, which is not
generally possible.

A lint is covered in a lane when at least one of these runs in it:

    * a CI step in a job whose `if:` admits that lane
    * a live-tree test in a file that the lane's test job actually executes

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

Scope
-----
The inventory is enumerated from `tools/check_*.py` on disk, never from a
hand-maintained list, and every lint is accounted for in one of two ways:

*Laned* -- the lint has a pre-commit hook that selects files, either by a
`files:` regex or by `types:`. Its watched set is resolved against the tracked
files and it carries the full per-lane obligation above. A `types:` selector is
approximated as matching every tracked file, since reproducing pre-commit's
`identify` pass is out of scope; the approximation only ever widens the
reachable lanes, so it can ask for coverage that is not strictly needed but can
never hide a gap.

*Unlaned* -- the lint has no file-selecting hook at all: a `pre-push` or
`commit-msg` stage hook, or no hook whatsoever. There is no watched set to
classify, so there is no lane obligation. These are still reported, and each
must be reachable *somehow*: named by a CI job, invoked by another tracked
script, or carrying an explicit allow marker. A lint that runs nowhere at all
is a violation even though it has no lane.

Nothing is skipped silently. `--list` prints both sections in full.

Test freshness
--------------
One further rule, separate from the lanes and easy to confuse with them: a
lint can be fully lane-covered by an unconditional CI step while none of its
own tests ever run on the pull request that edits it. Editing a lint is an
app-required change, and QUICK executes only the test files `quick_check`
names -- so every lint's owning test file must appear in that list, or a logic
regression in the lint merges green. A test file owns a lint by declaring its
`SCRIPT` constant, whether or not it also has a live-tree test; a lint with no
test file at all is not flagged, since this rule polices stale wiring rather
than mandating coverage.

Escape hatch
------------
Put `# guard-ci-coverage: allow <reason>` on or directly above a hook's
`- id:` line to exempt it, or anywhere in an unlaned lint's own source. Use
this when a lint is genuinely covered by a mechanism this guard cannot see --
not to silence a real gap. The reason is required and is printed by `--list`.

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
TOOLS_DIR = Path("tools")

# Directories searched for a tracked script that invokes an unlaned lint.
INVOKER_DIRS = (Path("tools"), Path("scripts"))

_ALLOW_RE = re.compile(r"#\s*guard-ci-coverage:\s*allow\s+(?P<reason>\S.*?)\s*$")
_TOOL_RE = re.compile(r"tools/check_[a-z_0-9]+\.py")
_SCRIPT_CONST_RE = re.compile(r"'(check_[a-z_0-9]+\.py)'")
_TEST_PATH_RE = re.compile(r"tests/test_[a-z_0-9]+\.py")

# The three lanes a pull request can land in. See the module docstring.
LIGHT = "light"
QUICK = "quick"
FULL = "full"
ALL_LANES = frozenset({LIGHT, QUICK, FULL})

# LIGHT reachability is decided one path at a time, so no invocation ever
# approaches the platform argv limit and no path budget is needed. See
# `_reaches_light` for why the whole matched set must not be classified at once.


@dataclass
class Guard:
    """A lint whose pre-commit hook selects files, so it carries lane duties."""

    hook_id: str
    tool: str
    selector: str
    pattern: str | None = None
    allow_reason: str | None = None
    matched: list[str] = field(default_factory=list)
    reaches_light: bool = False
    ci_lanes: set[str] = field(default_factory=set)
    live_tests: list[str] = field(default_factory=list)
    test_lanes: set[str] = field(default_factory=set)

    @property
    def required_lanes(self) -> set[str]:
        """Lanes a PR touching this lint's files can land in.

        QUICK and FULL are unconditional: any PR can add an application source
        file, which forces `app_required`, and either matrix may then run.
        """
        lanes = {QUICK, FULL}
        if self.reaches_light:
            lanes.add(LIGHT)
        return lanes

    @property
    def covered_lanes(self) -> set[str]:
        return self.ci_lanes | self.test_lanes

    @property
    def gaps(self) -> set[str]:
        return self.required_lanes - self.covered_lanes


@dataclass
class Unlaned:
    """A lint with no file-selecting hook: no lane duty, but must run somewhere."""

    tool: str
    reason: str
    ci_jobs: list[str] = field(default_factory=list)
    invokers: list[str] = field(default_factory=list)
    allow_reason: str | None = None

    @property
    def is_reachable(self) -> bool:
        return bool(self.ci_jobs or self.invokers or self.allow_reason)


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


def _tool_inventory(root: Path) -> list[str]:
    """Every `tools/check_*.py` on disk.

    Enumerated at runtime rather than read from a list, so a new lint is
    accounted for the moment it lands instead of when someone remembers to
    register it.
    """
    tools_dir = root / TOOLS_DIR
    if not tools_dir.is_dir():
        raise StructuralError(f"{TOOLS_DIR}: not a directory")
    return sorted(f"{TOOLS_DIR.as_posix()}/{p.name}" for p in tools_dir.glob("check_*.py"))


def collect_guards(root: Path) -> tuple[list[Guard], dict[str, str]]:
    """Return file-selecting guards plus the hook reason for every other lint.

    The second value maps a lint path to why it has no lane obligation, so the
    caller can report it rather than drop it.
    """
    config = _load_yaml(root / PRE_COMMIT)
    if not isinstance(config, dict) or "repos" not in config:
        raise StructuralError(f"{PRE_COMMIT}: no 'repos' mapping")
    markers = _allow_markers(root / PRE_COMMIT)

    guards: list[Guard] = []
    unlaned_reasons: dict[str, str] = {}
    for repo in config["repos"]:
        for hook in repo.get("hooks", []):
            entry = hook.get("entry", "")
            tool_match = _TOOL_RE.search(entry)
            if not tool_match:
                continue
            tool = tool_match.group(0)
            hook_id = hook.get("id", "<unnamed>")
            pattern = hook.get("files")
            types = hook.get("types") or hook.get("types_or")
            if pattern:
                selector = "files"
            elif types:
                selector = "types"
            else:
                stages = hook.get("stages") or ["pre-commit"]
                unlaned_reasons.setdefault(
                    tool, f"hook {hook_id} selects no files (stages: {','.join(stages)})"
                )
                continue
            guards.append(
                Guard(
                    hook_id=hook_id,
                    tool=tool,
                    selector=selector,
                    pattern=pattern,
                    allow_reason=markers.get(hook_id),
                )
            )
    return guards, unlaned_reasons


def _lanes_of(condition: object) -> frozenset[str]:
    """Classify a job-level `if:` into the set of lanes the job runs in."""
    if condition is None:
        return ALL_LANES
    text = str(condition)
    if "lightweight_only == 'true'" in text and "app_required" not in text:
        return frozenset({LIGHT})
    if "app_required == 'true'" in text and "lightweight_only" not in text:
        if "full_ci_requested != 'true'" in text:
            return frozenset({QUICK})
        if "full_ci_requested == 'true'" in text:
            return frozenset({FULL})
        return frozenset({QUICK, FULL})
    raise StructuralError(
        f"{WORKFLOW}: cannot classify job condition into CI lanes: {text!r}"
    )


def _jobs(root: Path) -> dict[str, dict]:
    workflow = _load_yaml(root / WORKFLOW)
    if not isinstance(workflow, dict) or "jobs" not in workflow:
        raise StructuralError(f"{WORKFLOW}: no 'jobs' mapping")
    return {
        name: job
        for name, job in workflow["jobs"].items()
        if isinstance(job, dict)
    }


def _run_commands(job: dict) -> list[str]:
    return [
        step.get("run") or ""
        for step in (job.get("steps") or [])
        if isinstance(step, dict)
    ]


def collect_ci_lanes(root: Path) -> tuple[dict[str, set[str]], dict[str, list[str]]]:
    """Map lint path -> lanes whose jobs run it, and -> the job names."""
    lanes: dict[str, set[str]] = {}
    job_names: dict[str, list[str]] = {}
    for name, job in _jobs(root).items():
        tools = {
            tool
            for command in _run_commands(job)
            for tool in _TOOL_RE.findall(command)
        }
        if not tools:
            continue
        # Only classify a condition for a job that actually runs a lint, so an
        # unrelated job with an exotic `if:` is not a structural error.
        job_lanes = _lanes_of(job.get("if"))
        for tool in tools:
            lanes.setdefault(tool, set()).update(job_lanes)
            job_names.setdefault(tool, []).append(name)
    return lanes, job_names


def collect_test_lanes(root: Path) -> dict[str, set[str]]:
    """Map a test file name -> the lanes whose jobs actually execute it.

    A job that names explicit `tests/*.py` operands runs only those files; one
    that selects by marker with no operands runs the whole suite. `--collect-only`
    executes nothing, so those jobs are ignored entirely -- which also keeps
    their unrelated `if:` out of `_lanes_of`.
    """
    whole_suite: set[str] = set()
    explicit: dict[str, set[str]] = {}
    for job in _jobs(root).values():
        commands = [c for c in _run_commands(job) if re.search(r"\bpytest\b", c)]
        commands = [c for c in commands if "--collect-only" not in c]
        if not commands:
            continue
        job_lanes = _lanes_of(job.get("if"))
        for command in commands:
            named = set(_TEST_PATH_RE.findall(command))
            if named:
                for path in named:
                    explicit.setdefault(Path(path).name, set()).update(job_lanes)
            else:
                whole_suite.update(job_lanes)

    tests_dir = root / TESTS_DIR
    if not tests_dir.is_dir():
        raise StructuralError(f"{TESTS_DIR}: not a directory")
    result: dict[str, set[str]] = {}
    for path in sorted(tests_dir.glob("test_*.py")):
        result[path.name] = set(whole_suite) | explicit.get(path.name, set())
    return result


def collect_live_tests(
    root: Path,
) -> tuple[dict[str, list[tuple[str, str]]], dict[str, list[str]]]:
    """Find which test files own which lint, and which of their tests are live.

    Returns `(live, owners)`. `live` maps a `check_*.py` basename to
    (test file, test name) pairs that exercise the live tree. `owners` maps it
    to every test file that declares the lint's `SCRIPT` constant, live tests
    or not -- a file can own a lint while testing it only against synthetic
    trees, which still needs to run when the lint changes.
    """
    tests_dir = root / TESTS_DIR
    if not tests_dir.is_dir():
        raise StructuralError(f"{TESTS_DIR}: not a directory")

    live: dict[str, list[tuple[str, str]]] = {}
    owners: dict[str, list[str]] = {}
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
        owners.setdefault(script_name, []).append(path.name)

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
                live.setdefault(script_name, []).append((path.name, node.name))
    return live, owners


def collect_invokers(root: Path, tracked: list[str]) -> dict[str, list[str]]:
    """Map lint path -> tracked scripts that invoke it.

    An unlaned lint can be reachable by being called from another script --
    `check_approval_duplicate.py` runs only from `tools/pr_comment.sh`. Test
    files do not count: a test proves the lint works, not that anything runs it.
    """
    invokers: dict[str, list[str]] = {}
    candidates = [
        path
        for path in tracked
        if any(path.startswith(f"{d.as_posix()}/") for d in INVOKER_DIRS)
    ]
    for path in candidates:
        try:
            text = (root / path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for tool in set(_TOOL_RE.findall(text)):
            if tool == path:
                continue
            invokers.setdefault(tool, []).append(path)
    return invokers


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
        ["bash", str(CLASSIFIER), "--", *paths],
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


def _reaches_light(root: Path, paths: list[str], memo: dict[str, bool]) -> bool:
    """Can a PR touching only files this hook watches select the LIGHT lane?

    Reachability is per-path, never per-union. A PR is free to touch any
    *subset* of a hook's watched files, and `lightweight_only` is an AND over
    the paths in that PR — so classifying the whole matched set at once
    under-approximates. For a mixed pattern (`docs/**.md` beside `tools/*.py`)
    the one app-required path would mask every lightweight path the same
    pattern watches, and the guard would silently miss exactly the lane gap it
    exists to find.

    One lightweight path is enough to prove reachability, so stop at the
    first — the answer does not depend on the scan order. The memo is shared
    across guards because their patterns overlap heavily.
    """
    for path in paths:
        if path not in memo:
            outputs = _classify(root, [path])
            memo[path] = outputs.get("lightweight_only") == "true"
        if memo[path]:
            return True
    return False


def _allow_marker_in_source(root: Path, tool: str) -> str | None:
    try:
        text = (root / tool).read_text(encoding="utf-8")
    except OSError:
        return None
    match = _ALLOW_RE.search(text)
    return match.group("reason") if match else None


def analyse(root: Path) -> tuple[list[Guard], list[Unlaned], list[tuple[str, list[str]]]]:
    guards, unlaned_reasons = collect_guards(root)
    ci_lanes, ci_jobs = collect_ci_lanes(root)
    test_lanes = collect_test_lanes(root)
    live, owners = collect_live_tests(root)
    tracked = _tracked_files(root)
    invokers = collect_invokers(root, tracked)
    light_memo: dict[str, bool] = {}

    for guard in guards:
        if guard.selector == "files":
            assert guard.pattern is not None
            try:
                matcher = re.compile(guard.pattern)
            except re.error as exc:
                raise StructuralError(
                    f"{PRE_COMMIT}: hook {guard.hook_id} has an invalid files "
                    f"pattern {guard.pattern!r}: {exc}"
                ) from None
            guard.matched = [path for path in tracked if matcher.search(path)]
        else:
            # `types:` is approximated as every tracked file; see the docstring.
            guard.matched = list(tracked)
        guard.ci_lanes = ci_lanes.get(guard.tool, set())
        guard.live_tests = [
            f"{file}::{name}" for file, name in live.get(Path(guard.tool).name, [])
        ]
        guard.test_lanes = {
            lane
            for file, _ in live.get(Path(guard.tool).name, [])
            for lane in test_lanes.get(file, set())
        }
        guard.reaches_light = _reaches_light(root, guard.matched, light_memo)

    laned = {guard.tool for guard in guards}
    unlaned = [
        Unlaned(
            tool=tool,
            reason=unlaned_reasons.get(tool, "no pre-commit hook"),
            ci_jobs=sorted(ci_jobs.get(tool, [])),
            invokers=sorted(invokers.get(tool, [])),
            allow_reason=_allow_marker_in_source(root, tool),
        )
        for tool in _tool_inventory(root)
        if tool not in laned
    ]

    # Separate obligation from the lane rules above, and easy to confuse with
    # them: a lint can be fully lane-covered by an unconditional CI step while
    # none of its own tests run on the pull request that edits it. Editing a
    # lint is an app-required change, so the QUICK lane is the one that has to
    # execute its tests -- and QUICK runs only the files `quick_check` names.
    stale_tests: list[tuple[str, list[str]]] = []
    # Union rather than the on-disk inventory alone, so a hook that names a
    # lint is enough -- the rule does not depend on where the lint was found.
    for tool in sorted(set(_tool_inventory(root)) | laned):
        files = owners.get(Path(tool).name, [])
        if files and not any(QUICK in test_lanes.get(f, set()) for f in files):
            stale_tests.append((tool, sorted(files)))
    return guards, unlaned, stale_tests


def _describe(guard: Guard) -> str:
    order = (LIGHT, QUICK, FULL)
    lanes = "+".join(x.upper() for x in order if x in guard.required_lanes)
    covered = "+".join(x.upper() for x in order if x in guard.covered_lanes)
    # Tool first, matching the unlaned section, so the two listings together
    # read as one inventory of `tools/check_*.py`.
    return (
        f"  {Path(guard.tool).name:36s} hook={guard.hook_id:30s} "
        f"by={guard.selector:5s} needs={lanes:16s} has={covered or '-':16s} "
        f"tests={len(guard.live_tests):2d} files={len(guard.matched)}"
    )


def _describe_unlaned(record: Unlaned) -> str:
    if record.allow_reason:
        where = f"allowed: {record.allow_reason}"
    elif record.ci_jobs:
        where = f"ci jobs: {','.join(record.ci_jobs)}"
    elif record.invokers:
        where = f"invoked by: {','.join(record.invokers)}"
    else:
        where = "RUNS NOWHERE"
    return f"  {Path(record.tool).name:36s} {record.reason}; {where}"


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
        guards, unlaned, stale_tests = analyse(root)
    except StructuralError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not guards:
        print(
            f"error: {PRE_COMMIT} declares no tools/check_*.py hook that selects "
            "files; this guard has nothing to lane-check and is silently passing",
            file=sys.stderr,
        )
        return 2

    if args.list:
        print("laned (file-selecting hook -- full per-lane obligation):")
        for guard in guards:
            suffix = ""
            if guard.allow_reason:
                suffix = f"  [allowed: {guard.allow_reason}]"
            elif not guard.matched:
                suffix = "  [selects no tracked file]"
            print(_describe(guard) + suffix)
        print("\nunlaned (no file-selecting hook -- must merely run somewhere):")
        for record in unlaned:
            print(_describe_unlaned(record))
        print("\nlints whose own tests never run in the QUICK lane:")
        for tool, files in stale_tests:
            print(f"  {Path(tool).name:36s} {','.join(files)}")
        if not stale_tests:
            print("  none")
        return 0

    violations: list[str] = []
    for guard in guards:
        if guard.allow_reason or not guard.matched:
            continue
        gaps = guard.gaps
        if not gaps:
            continue
        where = (
            f"it runs in CI lanes {'+'.join(sorted(guard.ci_lanes)).upper()}"
            if guard.ci_lanes
            else "it runs in no CI job at all"
        )
        tests = (
            f" and its {len(guard.live_tests)} live-tree test(s) run in "
            f"{'+'.join(sorted(guard.test_lanes)).upper() or 'no lane'}"
            if guard.live_tests
            else " and it has no live-tree test"
        )
        for lane in sorted(gaps):
            violations.append(
                f"{PRE_COMMIT}: {guard.hook_id} ({guard.tool}) is not exercised "
                f"in the {lane.upper()} lane: {where}{tests}. "
                + _remedy(lane)
            )

    for record in unlaned:
        if record.is_reachable:
            continue
        violations.append(
            f"{record.tool}: this lint runs nowhere. It has no file-selecting "
            f"pre-commit hook ({record.reason}), no CI job names it, and no "
            "tracked script under tools/ or scripts/ invokes it. Add a CI step, "
            "call it from a script that has one, or add a "
            "`# guard-ci-coverage: allow <reason>` marker to its source."
        )

    for tool, files in stale_tests:
        violations.append(
            f"{tool}: its own tests ({', '.join(files)}) do not run in the "
            "QUICK lane. Editing a lint is an app-required change, and an "
            "ordinary synchronize push runs only the test files named in the "
            "`quick_check` job -- so a logic regression in this lint would "
            "merge green. Add the file(s) to that pytest list."
        )

    for violation in violations:
        print(violation, file=sys.stderr)
    return 1 if violations else 0


def _remedy(lane: str) -> str:
    if lane == LIGHT:
        return (
            "A pull request touching only its watched files skips every test "
            "job, so it would merge unchecked. Add the step to the fast-guard "
            "block in the `changes` job."
        )
    if lane == QUICK:
        return (
            "The quick lane runs only the test files named explicitly in "
            "`quick_check`, so a live-tree test elsewhere does not cover it. "
            "Add the step to the fast-guard block in the `changes` job, or add "
            "its test file to the `quick_check` pytest list."
        )
    return (
        "Add the step to the fast-guard block in the `changes` job, or add a "
        "zero-argument test over REPO_ROOT to its test file."
    )


if __name__ == "__main__":
    raise SystemExit(main())

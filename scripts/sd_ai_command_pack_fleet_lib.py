#!/usr/bin/env python3
"""Shared fleet manifest, payload digest, and candidate-ledger contracts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

FLEET_SCHEMA_VERSION = 4
FLEET_PROFILE_SCHEMA_VERSION = 1
CANDIDATE_LEDGER_SCHEMA_VERSION = 2
MAX_CANDIDATE_TIMEOUT_SECONDS = 3600
MAX_FLEET_CONCURRENCY = 4
SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
CONSUMER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PACK_NAME = "sd-ai-command-pack"
DEFAULT_FLEET_MANIFEST = Path("docs/fleet/consumers.json")
FLEET_CONFIG_ENV = "SD_AI_COMMAND_PACK_FLEET_CONFIG"
FLEET_MANIFEST_ENV = "SD_AI_COMMAND_PACK_FLEET_MANIFEST"


class FleetConfigError(ValueError):
    """Raised when source-owned fleet configuration is invalid."""


@dataclass(frozen=True)
class FleetConsumer:
    name: str
    github: str
    path_hint: str
    platforms: tuple[str, ...]
    rollout_priority: int
    candidate_timeout_seconds: int
    candidate_prepare: tuple[tuple[str, ...], ...]
    candidate_checks: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class FleetRolloutCohort:
    name: str
    strategy: str
    max_concurrency: int
    consumers: tuple[str, ...]


@dataclass(frozen=True)
class FleetRolloutPolicy:
    default_concurrency: int
    cohorts: tuple[FleetRolloutCohort, ...]


@dataclass(frozen=True)
class PayloadSource:
    content: bytes
    executable: bool


@dataclass(frozen=True)
class FleetProfile:
    path: Path
    pack_source: Path
    fleet_manifest: Path
    path_overrides: Mapping[str, Path]


@dataclass(frozen=True)
class FleetResolution:
    manifest_path: Path
    pack_source: Path
    target_version: str
    path_overrides: Mapping[str, Path]
    source: str
    profile_path: Path | None = None


@dataclass(frozen=True)
class FleetProfileUpdate:
    path: Path
    status: str


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except FileNotFoundError:
        raise FleetConfigError(f"{label} not found: {path}") from None
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FleetConfigError(f"{label} is not valid UTF-8 JSON: {error}") from None
    if not isinstance(payload, dict):
        raise FleetConfigError(f"{label} must be a JSON object: {path}")
    return payload


def _configured_path(value: str, *, base: Path) -> Path:
    if not value.strip() or "\0" in value:
        raise FleetConfigError("configured path must be a non-empty string")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def fleet_profile_path(
    environ: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
    cwd: Path | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    base = Path.cwd() if cwd is None else cwd
    explicit = env.get(FLEET_CONFIG_ENV, "").strip()
    if explicit:
        return _configured_path(explicit, base=base)

    xdg_home = env.get("XDG_CONFIG_HOME", "").strip()
    if xdg_home:
        xdg_path = Path(xdg_home).expanduser()
        if not xdg_path.is_absolute():
            raise FleetConfigError("XDG_CONFIG_HOME must be an absolute path")
        config_home = xdg_path
    else:
        config_home = (Path.home() if home is None else home).expanduser() / ".config"
    return (config_home / PACK_NAME / "config.json").resolve()


def _profile_string(
    payload: Mapping[str, Any],
    field: str,
    *,
    required: bool,
) -> str | None:
    value = payload.get(field)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise FleetConfigError(f"fleet profile has invalid {field}")
    return value.strip()


def load_fleet_profile(path: Path) -> FleetProfile:
    payload = load_json_object(path, "fleet profile")
    if payload.get("schemaVersion") != FLEET_PROFILE_SCHEMA_VERSION:
        raise FleetConfigError(
            "fleet profile schemaVersion must be "
            f"{FLEET_PROFILE_SCHEMA_VERSION}: {path}"
        )

    pack_source_value = _profile_string(payload, "packSource", required=True)
    assert pack_source_value is not None
    pack_source = _configured_path(pack_source_value, base=path.parent)
    manifest_value = _profile_string(payload, "fleetManifest", required=False)
    manifest_path = (
        _configured_path(manifest_value, base=pack_source)
        if manifest_value is not None
        else (pack_source / DEFAULT_FLEET_MANIFEST).resolve()
    )

    raw_overrides = payload.get("pathOverrides", {})
    if not isinstance(raw_overrides, dict):
        raise FleetConfigError("fleet profile pathOverrides must be an object")
    overrides: dict[str, Path] = {}
    seen: set[str] = set()
    for name, value in raw_overrides.items():
        if (
            not isinstance(name, str)
            or name in {".", ".."}
            or not CONSUMER_NAME_RE.fullmatch(name)
        ):
            raise FleetConfigError("fleet profile has invalid pathOverrides key")
        key = name.casefold()
        if key in seen:
            raise FleetConfigError(f"fleet profile repeats path override {name}")
        seen.add(key)
        if not isinstance(value, str):
            raise FleetConfigError(f"fleet profile path override {name} must be a string")
        overrides[key] = _configured_path(value, base=path.parent)
    return FleetProfile(
        path=path.resolve(),
        pack_source=pack_source,
        fleet_manifest=manifest_path,
        path_overrides=overrides,
    )


def _pack_identity(pack_source: Path) -> tuple[Path, str]:
    resolved_source = pack_source.expanduser().resolve()
    manifest = load_json_object(resolved_source / "manifest.json", "pack manifest")
    if manifest.get("name") != PACK_NAME:
        raise FleetConfigError(
            f"pack source is not {PACK_NAME}: {resolved_source}"
        )
    return resolved_source, manifest_version(manifest)


def _find_pack_source(manifest_path: Path) -> Path | None:
    for candidate in (manifest_path.parent, *manifest_path.parents):
        pack_manifest = candidate / "manifest.json"
        if not pack_manifest.is_file():
            continue
        try:
            manifest = load_json_object(pack_manifest, "pack manifest")
        except FleetConfigError:
            continue
        if manifest.get("name") == PACK_NAME:
            return candidate.resolve()
    return None


def resolve_fleet_configuration(
    runtime_pack_root: Path,
    *,
    fleet_manifest: Path | None = None,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> FleetResolution:
    env = os.environ if environ is None else environ
    base = Path.cwd() if cwd is None else cwd
    requested_manifest: Path | None = None
    source = ""
    if fleet_manifest is not None:
        requested_manifest = _configured_path(str(fleet_manifest), base=base)
        source = "command line"
    elif env.get(FLEET_MANIFEST_ENV, "").strip():
        requested_manifest = _configured_path(env[FLEET_MANIFEST_ENV], base=base)
        source = FLEET_MANIFEST_ENV

    if requested_manifest is not None:
        inferred_source = _find_pack_source(requested_manifest)
        if inferred_source is None:
            try:
                inferred_source, version = _pack_identity(runtime_pack_root)
            except FleetConfigError:
                raise FleetConfigError(
                    f"cannot associate fleet manifest with an {PACK_NAME} "
                    f"source checkout: {requested_manifest}"
                ) from None
        else:
            inferred_source, version = _pack_identity(inferred_source)
        return FleetResolution(
            manifest_path=requested_manifest,
            pack_source=inferred_source,
            target_version=version,
            path_overrides={},
            source=source,
        )

    profile_path = fleet_profile_path(env, home=home, cwd=base)
    if profile_path.is_file():
        try:
            profile = load_fleet_profile(profile_path)
            pack_source, version = _pack_identity(profile.pack_source)
        except FleetConfigError as error:
            raise FleetConfigError(
                f"fleet profile is unusable ({profile_path}): {error}"
            ) from None
        return FleetResolution(
            manifest_path=profile.fleet_manifest,
            pack_source=pack_source,
            target_version=version,
            path_overrides=profile.path_overrides,
            source="machine profile",
            profile_path=profile.path,
        )

    try:
        pack_source, version = _pack_identity(runtime_pack_root)
    except FleetConfigError:
        raise FleetConfigError(
            "fleet configuration not found; run install.py TARGET "
            f"--configure-fleet from the {PACK_NAME} source checkout"
        ) from None
    return FleetResolution(
        manifest_path=(pack_source / DEFAULT_FLEET_MANIFEST).resolve(),
        pack_source=pack_source,
        target_version=version,
        path_overrides={},
        source="pack source checkout",
    )


def configure_fleet_profile(
    pack_source: Path,
    *,
    path: Path | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    cwd: Path | None = None,
    dry_run: bool = False,
) -> FleetProfileUpdate:
    resolved_source, _ = _pack_identity(pack_source)
    manifest_path = (resolved_source / DEFAULT_FLEET_MANIFEST).resolve()
    load_fleet_consumers(manifest_path)
    profile_path = (
        path.expanduser().resolve()
        if path is not None
        else fleet_profile_path(environ, home=home, cwd=cwd)
    )

    overrides: dict[str, str] = {}
    if profile_path.exists():
        load_fleet_profile(profile_path)
        existing_payload = load_json_object(profile_path, "fleet profile")
        raw_overrides = existing_payload.get("pathOverrides", {})
        assert isinstance(raw_overrides, dict)
        overrides = dict(raw_overrides)
    payload = {
        "schemaVersion": FLEET_PROFILE_SCHEMA_VERSION,
        "packSource": str(resolved_source),
        "fleetManifest": str(manifest_path),
        "pathOverrides": overrides,
    }
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    current = None
    try:
        current = profile_path.read_text(encoding="utf-8", errors="strict")
    except FileNotFoundError:
        # Missing profile means a first-time write; keep current absent.
        current = None
    except (OSError, UnicodeError) as error:
        raise FleetConfigError(f"cannot read fleet profile {profile_path}: {error}") from None
    if current == content:
        return FleetProfileUpdate(profile_path, "current")
    status = "planned" if dry_run else ("updated" if current is not None else "created")
    if dry_run:
        return FleetProfileUpdate(profile_path, status)

    try:
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=profile_path.parent,
            prefix=f".{profile_path.name}.",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, profile_path)
        finally:
            temporary_path.unlink(missing_ok=True)
    except OSError as error:
        raise FleetConfigError(f"cannot write fleet profile {profile_path}: {error}") from None
    return FleetProfileUpdate(profile_path, status)


def _required_string(item: Mapping[str, Any], field: str, label: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise FleetConfigError(f"{label} has invalid {field}")
    return value.strip()


def _parse_platforms(item: Mapping[str, Any], label: str) -> tuple[str, ...]:
    platforms = item.get("platforms")
    if not isinstance(platforms, list) or not platforms:
        raise FleetConfigError(f"{label} must list platforms")
    parsed: list[str] = []
    seen: set[str] = set()
    for platform in platforms:
        if not isinstance(platform, str) or not platform or "\0" in platform:
            raise FleetConfigError(f"{label} has invalid platform")
        if platform in seen:
            raise FleetConfigError(f"{label} repeats platform {platform}")
        seen.add(platform)
        parsed.append(platform)
    return tuple(sorted(parsed))


def _parse_candidate_commands(
    item: Mapping[str, Any],
    label: str,
    field: str,
    *,
    allow_empty: bool,
) -> tuple[tuple[str, ...], ...]:
    commands = item.get(field)
    if not isinstance(commands, list) or (not allow_empty and not commands):
        raise FleetConfigError(f"{label} must list {field}")
    parsed: list[tuple[str, ...]] = []
    for command_index, command in enumerate(commands):
        command_label = f"{label} {field}[{command_index}]"
        if not isinstance(command, list) or not command:
            raise FleetConfigError(f"{command_label} must be a non-empty argv array")
        argv: list[str] = []
        for argument in command:
            if not isinstance(argument, str) or not argument or "\0" in argument:
                raise FleetConfigError(
                    f"{command_label} must contain non-empty string arguments"
                )
            argv.append(argument)
        parsed.append(tuple(argv))
    return tuple(parsed)


def parse_fleet_rollout_policy(
    manifest: Mapping[str, Any],
    consumers: Sequence[FleetConsumer],
    label: str = "fleet manifest",
) -> FleetRolloutPolicy:
    raw_policy = manifest.get("rolloutPolicy")
    if not isinstance(raw_policy, dict):
        raise FleetConfigError(f"{label} rolloutPolicy must be an object")
    unknown_policy_fields = sorted(
        set(raw_policy) - {"defaultConcurrency", "cohorts"}
    )
    if unknown_policy_fields:
        raise FleetConfigError(
            f"{label} rolloutPolicy has unknown field {unknown_policy_fields[0]}"
        )

    default_concurrency = raw_policy.get("defaultConcurrency")
    if (
        isinstance(default_concurrency, bool)
        or not isinstance(default_concurrency, int)
        or not 1 <= default_concurrency <= MAX_FLEET_CONCURRENCY
    ):
        raise FleetConfigError(
            f"{label} rolloutPolicy defaultConcurrency must be between 1 and "
            f"{MAX_FLEET_CONCURRENCY}"
        )

    raw_cohorts = raw_policy.get("cohorts")
    if not isinstance(raw_cohorts, list) or not raw_cohorts:
        raise FleetConfigError(f"{label} rolloutPolicy cohorts must be non-empty")

    cohorts: list[FleetRolloutCohort] = []
    seen_cohorts: set[str] = set()
    configured_consumers: list[str] = []
    known_consumers = {consumer.name: consumer.name for consumer in consumers}
    seen_consumers: set[str] = set()
    for index, raw_cohort in enumerate(raw_cohorts):
        cohort_label = f"{label} rolloutPolicy cohorts[{index}]"
        if not isinstance(raw_cohort, dict):
            raise FleetConfigError(f"{cohort_label} must be an object")
        unknown_fields = sorted(
            set(raw_cohort) - {"name", "strategy", "maxConcurrency", "consumers"}
        )
        if unknown_fields:
            raise FleetConfigError(
                f"{cohort_label} has unknown field {unknown_fields[0]}"
            )
        name = _required_string(raw_cohort, "name", cohort_label)
        if name in {".", ".."} or not CONSUMER_NAME_RE.fullmatch(name):
            raise FleetConfigError(f"{cohort_label} name is not a safe identifier")
        name_key = name.casefold()
        if name_key in seen_cohorts:
            raise FleetConfigError(f"duplicate fleet rollout cohort name: {name}")
        seen_cohorts.add(name_key)

        strategy = _required_string(raw_cohort, "strategy", cohort_label)
        if strategy not in {"sequential", "bounded-parallel"}:
            raise FleetConfigError(f"{cohort_label} has invalid strategy")
        raw_max_concurrency = raw_cohort.get("maxConcurrency")
        if strategy == "sequential":
            if raw_max_concurrency is not None and (
                isinstance(raw_max_concurrency, bool)
                or not isinstance(raw_max_concurrency, int)
                or raw_max_concurrency != 1
            ):
                raise FleetConfigError(
                    f"{cohort_label} sequential maxConcurrency must be 1 when present"
                )
            max_concurrency = 1
        else:
            if (
                isinstance(raw_max_concurrency, bool)
                or not isinstance(raw_max_concurrency, int)
                or not 2 <= raw_max_concurrency <= default_concurrency
            ):
                raise FleetConfigError(
                    f"{cohort_label} bounded-parallel maxConcurrency must be between "
                    f"2 and defaultConcurrency"
                )
            max_concurrency = raw_max_concurrency

        raw_names = raw_cohort.get("consumers")
        if not isinstance(raw_names, list) or not raw_names:
            raise FleetConfigError(f"{cohort_label} consumers must be non-empty")
        cohort_consumers: list[str] = []
        for consumer_index, raw_name in enumerate(raw_names):
            if not isinstance(raw_name, str) or not raw_name.strip():
                raise FleetConfigError(
                    f"{cohort_label} consumers[{consumer_index}] must be a non-empty string"
                )
            consumer_key = raw_name
            canonical_name = known_consumers.get(consumer_key)
            if canonical_name is None:
                raise FleetConfigError(
                    f"{cohort_label} names unknown consumer {raw_name}"
                )
            if consumer_key in seen_consumers:
                raise FleetConfigError(
                    f"fleet rollout policy repeats consumer {canonical_name}"
                )
            seen_consumers.add(consumer_key)
            cohort_consumers.append(canonical_name)
            configured_consumers.append(canonical_name)
        cohorts.append(
            FleetRolloutCohort(
                name=name,
                strategy=strategy,
                max_concurrency=max_concurrency,
                consumers=tuple(cohort_consumers),
            )
        )

    if cohorts[0].name.casefold() != "canary" or cohorts[0].strategy != "sequential":
        raise FleetConfigError(
            f"{label} rolloutPolicy first cohort must be sequential canary"
        )
    expected_consumers = [consumer.name for consumer in consumers]
    if configured_consumers != expected_consumers:
        missing = [name for name in expected_consumers if name not in configured_consumers]
        if missing:
            raise FleetConfigError(
                f"{label} rolloutPolicy is missing consumer {missing[0]}"
            )
        raise FleetConfigError(
            f"{label} rolloutPolicy consumer order must match rolloutPriority order"
        )

    return FleetRolloutPolicy(
        default_concurrency=default_concurrency,
        cohorts=tuple(cohorts),
    )


def _parse_fleet_consumers_without_policy(
    manifest: Mapping[str, Any],
    label: str = "fleet manifest",
) -> list[FleetConsumer]:
    if manifest.get("schemaVersion") != FLEET_SCHEMA_VERSION:
        raise FleetConfigError(f"{label} schemaVersion must be {FLEET_SCHEMA_VERSION}")
    consumers = manifest.get("consumers")
    if not isinstance(consumers, list) or not consumers:
        raise FleetConfigError(f"{label} consumers must be a non-empty array")

    parsed: list[FleetConsumer] = []
    seen_names: set[str] = set()
    seen_priorities: set[int] = set()
    for index, item in enumerate(consumers):
        if not isinstance(item, dict):
            raise FleetConfigError(
                f"{label} consumers[{index}] must be an object"
            )
        consumer_label = f"{label} consumer {item.get('name', index)}"
        name = _required_string(item, "name", consumer_label)
        if name in {".", ".."} or not CONSUMER_NAME_RE.fullmatch(name):
            raise FleetConfigError(
                f"{consumer_label} name must be a non-path identifier using only "
                "letters, numbers, dots, underscores, and hyphens"
            )
        name_key = name.casefold()
        if name_key in seen_names:
            raise FleetConfigError(f"duplicate fleet consumer name: {name}")
        seen_names.add(name_key)

        github = _required_string(item, "github", consumer_label)
        if "/" not in github:
            raise FleetConfigError(f"{consumer_label} has invalid github slug")
        path_hint = _required_string(item, "pathHint", consumer_label)
        priority = item.get("rolloutPriority")
        if isinstance(priority, bool) or not isinstance(priority, int) or priority <= 0:
            raise FleetConfigError(f"{consumer_label} has invalid rolloutPriority")
        if priority in seen_priorities:
            raise FleetConfigError(f"duplicate fleet rolloutPriority: {priority}")
        seen_priorities.add(priority)

        timeout = item.get("candidateTimeoutSeconds")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or not 1 <= timeout <= MAX_CANDIDATE_TIMEOUT_SECONDS
        ):
            raise FleetConfigError(
                f"{consumer_label} candidateTimeoutSeconds must be between 1 and "
                f"{MAX_CANDIDATE_TIMEOUT_SECONDS}"
            )

        parsed.append(
            FleetConsumer(
                name=name,
                github=github,
                path_hint=path_hint,
                platforms=_parse_platforms(item, consumer_label),
                rollout_priority=priority,
                candidate_timeout_seconds=timeout,
                candidate_prepare=_parse_candidate_commands(
                    item,
                    consumer_label,
                    "candidatePrepare",
                    allow_empty=True,
                ),
                candidate_checks=_parse_candidate_commands(
                    item,
                    consumer_label,
                    "candidateChecks",
                    allow_empty=False,
                ),
            )
        )
    ordered = sorted(
        parsed,
        key=lambda consumer: (consumer.rollout_priority, consumer.name.casefold()),
    )
    return ordered


def parse_fleet_manifest(
    manifest: Mapping[str, Any],
    label: str = "fleet manifest",
) -> tuple[list[FleetConsumer], FleetRolloutPolicy]:
    consumers = _parse_fleet_consumers_without_policy(manifest, label)
    return consumers, parse_fleet_rollout_policy(manifest, consumers, label)


def parse_fleet_consumers(
    manifest: Mapping[str, Any],
    label: str = "fleet manifest",
) -> list[FleetConsumer]:
    consumers, _ = parse_fleet_manifest(manifest, label)
    return consumers


def load_fleet_consumers(path: Path) -> list[FleetConsumer]:
    return parse_fleet_consumers(
        load_json_object(path, "fleet manifest"),
        f"fleet manifest {path}",
    )


def load_fleet_rollout_policy(path: Path) -> FleetRolloutPolicy:
    manifest = load_json_object(path, "fleet manifest")
    _, policy = parse_fleet_manifest(
        manifest,
        f"fleet manifest {path}",
    )
    return policy


def manifest_version(manifest: Mapping[str, Any], label: str = "pack manifest") -> str:
    version = manifest.get("version")
    if not isinstance(version, str) or not version.strip():
        raise FleetConfigError(f"{label} version is missing")
    return version.strip()


def pack_version(path: Path) -> str:
    return manifest_version(load_json_object(path, "pack manifest"))


def payload_digest(
    manifest: Mapping[str, Any],
    source_loader: Callable[[str], PayloadSource],
) -> str:
    files = manifest.get("files")
    if not isinstance(files, list):
        raise FleetConfigError("pack manifest files must be an array")

    sources: set[str] = set()
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise FleetConfigError(f"pack manifest files[{index}] must be an object")
        source = item.get("source")
        if not isinstance(source, str) or not source:
            raise FleetConfigError(f"pack manifest files[{index}] has invalid source")
        source_path = PurePosixPath(source)
        if source_path.is_absolute() or ".." in source_path.parts:
            raise FleetConfigError(f"pack manifest source is unsafe: {source}")
        sources.add(source)

    digest = hashlib.sha256()
    digest.update(b"sd-ai-command-pack-candidate-payload-v1\0")
    digest.update(
        json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    )
    digest.update(b"\0")
    for source in sorted(sources):
        payload = source_loader(source)
        digest.update(source.encode("utf-8"))
        digest.update(b"\0x\0" if payload.executable else b"\0-\0")
        digest.update(hashlib.sha256(payload.content).digest())
    return f"sha256:{digest.hexdigest()}"


def filesystem_payload_digest(manifest_path: Path) -> str:
    manifest = load_json_object(manifest_path, "pack manifest")
    root = manifest_path.resolve().parent

    def load_source(relative_path: str) -> PayloadSource:
        path = root / relative_path
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
            mode = resolved.stat().st_mode
            return PayloadSource(
                content=resolved.read_bytes(),
                executable=bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)),
            )
        except (OSError, ValueError) as error:
            raise FleetConfigError(
                f"cannot read pack manifest source {relative_path}: {error}"
            ) from None

    return payload_digest(manifest, load_source)


def fleet_manifest_digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def validate_candidate_ledger(
    ledger: Mapping[str, Any],
    *,
    expected_version: str,
    expected_payload_digest: str,
    expected_fleet_digest: str,
    consumers: list[FleetConsumer],
) -> list[str]:
    errors: list[str] = []
    if ledger.get("schemaVersion") != CANDIDATE_LEDGER_SCHEMA_VERSION:
        errors.append(
            "candidate ledger schemaVersion must be "
            f"{CANDIDATE_LEDGER_SCHEMA_VERSION}"
        )
    for field, expected in (
        ("packVersion", expected_version),
        ("payloadDigest", expected_payload_digest),
        ("fleetManifestDigest", expected_fleet_digest),
    ):
        if ledger.get(field) != expected:
            errors.append(
                f"candidate ledger {field} is {ledger.get(field)!r}; expected {expected!r}"
            )
    if not isinstance(ledger.get("validatedAt"), str) or not ledger["validatedAt"]:
        errors.append("candidate ledger validatedAt is missing")

    raw_results = ledger.get("consumers")
    if not isinstance(raw_results, list):
        errors.append("candidate ledger consumers must be an array")
        return errors

    by_name: dict[str, Mapping[str, Any]] = {}
    for index, result in enumerate(raw_results):
        if not isinstance(result, dict):
            errors.append(f"candidate ledger consumers[{index}] must be an object")
            continue
        name = result.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"candidate ledger consumers[{index}] has no name")
            continue
        key = name.casefold()
        if key in by_name:
            errors.append(f"candidate ledger repeats consumer {name}")
            continue
        by_name[key] = result

    expected_names = {consumer.name.casefold() for consumer in consumers}
    actual_names = set(by_name)
    for name in sorted(expected_names - actual_names):
        errors.append(f"candidate ledger is missing consumer {name}")
    for name in sorted(actual_names - expected_names):
        errors.append(f"candidate ledger has unknown consumer {name}")

    for consumer in consumers:
        result = by_name.get(consumer.name.casefold())
        if result is None:
            continue
        if result.get("github") != consumer.github:
            errors.append(f"candidate ledger {consumer.name} github does not match fleet")
        if result.get("status") != "passed":
            errors.append(
                f"candidate ledger {consumer.name} status is {result.get('status')!r}; "
                "expected 'passed'"
            )
        base_commit = result.get("baseCommit")
        if not isinstance(base_commit, str) or not SHA_RE.fullmatch(base_commit):
            errors.append(f"candidate ledger {consumer.name} baseCommit is invalid")
        expected_prepares = [list(command) for command in consumer.candidate_prepare]
        if result.get("prepares") != expected_prepares:
            errors.append(
                f"candidate ledger {consumer.name} prepares do not match fleet"
            )
        expected_checks = [list(command) for command in consumer.candidate_checks]
        if result.get("checks") != expected_checks:
            errors.append(f"candidate ledger {consumer.name} checks do not match fleet")
    return errors

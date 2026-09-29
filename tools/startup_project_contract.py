from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Final, TypeGuard

from tools.model_presets import DEFAULT_MODEL, DEFAULT_REASONING_EFFORT

REQUIRED_FILES: Final[tuple[str, ...]] = (
    "topic-selector.md",
    "researcher.md",
    "writer.md",
    "image-maker.md",
    "content-assembler.md",
    "notion-rider.md",
    "naver-rider.md",
    "schemas/workflow-contract.schema.json",
    "schemas/stage-result.schema.json",
    "pyproject.toml",
    "uv.lock",
)
REQUIRED_PACKAGES: Final[tuple[tuple[str, str], ...]] = (
    ("httpx2", "httpx2"),
    ("jsonschema", "jsonschema"),
    ("jsonschema-specifications", "jsonschema_specifications"),
    ("packaging", "packaging"),
)
PROJECT_REQUIRED_PACKAGES: Final[tuple[str, ...]] = (
    "httpx2",
    "jsonschema",
    "packaging",
)
CODEX_PROFILE_SETTINGS: Final[tuple[tuple[str, str], ...]] = (
    ("model", DEFAULT_MODEL),
    ("model_reasoning_effort", DEFAULT_REASONING_EFFORT),
    ("approval_policy", "never"),
    ("default_permissions", "naver-stage-isolated"),
)
CODEX_PROFILE_EXACT: Final[dict[str, object]] = {
    "approval_policy": "never",
    "default_permissions": "naver-stage-isolated",
    "allow_login_shell": False,
    "web_search": "disabled",
    "permissions": {
        "naver-stage-isolated": {
            "extends": ":workspace",
            "filesystem": {
                ":root": "deny",
                ":minimal": "read",
                "/opt/homebrew": "read",
                "~/.local/bin": "read",
                "~/.local/share/uv/python": "read",
                "~/.codex": "deny",
                "~/Library/Keychains": "deny",
                "/Users/beomseok/00_AI/01_ NAVER_BLOG_AUTOMATE": "read",
                ":workspace_roots": {".": "write"},
            },
            "network": {"enabled": False, "allow_local_binding": False},
        }
    },
    "features": {"request_permissions_tool": False, "multi_agent": False},
    "agents": {"enabled": False},
}


def read_required_files(root: Path) -> tuple[tuple[Path, str], ...] | None:
    values: list[tuple[Path, str]] = []
    for relative_path in REQUIRED_FILES:
        path = root / relative_path
        if not path.is_file():
            return None
        try:
            values.append((path, path.read_text(encoding="utf-8")))
        except OSError:
            return None
    return tuple(values)


def toml_document(raw: str) -> dict[str, object] | None:
    try:
        return tomllib.loads(raw)
    except tomllib.TOMLDecodeError:
        return None


def codex_profile_contract(raw: str) -> str | None:
    profile = toml_document(raw)
    if profile is None:
        return "codex_profile"
    model = profile.pop("model", None)
    effort = profile.pop("model_reasoning_effort", None)
    if (model, effort) != (DEFAULT_MODEL, DEFAULT_REASONING_EFFORT):
        return "codex_profile_model"
    return None if profile == CODEX_PROFILE_EXACT else "codex_profile"


def package_versions(value: dict[str, object]) -> dict[str, str] | None:
    packages = value.get("package")
    if not _object_list(packages):
        return None
    versions: dict[str, str] = {}
    required_names = frozenset(name for name, _module_name in REQUIRED_PACKAGES)
    for package in packages:
        if not _object_dict(package):
            return None
        name = package.get("name")
        if not isinstance(name, str) or name not in required_names:
            continue
        version = package.get("version")
        if not isinstance(version, str) or name in versions:
            return None
        versions[name] = version
    return versions if len(versions) == len(REQUIRED_PACKAGES) else None


def project_contract(
    project_document: dict[str, object],
    lock_document: dict[str, object],
    python_version: tuple[int, int, int],
) -> str | None:
    project = project_document.get("project")
    if not _object_dict(project):
        return "pyproject"
    requires_python = project.get("requires-python")
    lock_requires_python = lock_document.get("requires-python")
    if (
        not isinstance(requires_python, str)
        or not requires_python
        or requires_python != lock_requires_python
        or not _specifier_allows_version(
            requires_python, ".".join(str(part) for part in python_version)
        )
    ):
        return "project_python"
    dependencies = _project_dependencies(project_document)
    if dependencies is None:
        return "project_dependencies"
    locked_versions = package_versions(lock_document)
    if locked_versions is None:
        return "lockfile"
    for package in PROJECT_REQUIRED_PACKAGES:
        specifier = dependencies.get(package)
        locked = locked_versions.get(package)
        if (
            specifier is None
            or locked is None
            or not _specifier_allows_version(specifier, locked)
        ):
            return "project_dependencies"
    return None


def _object_dict(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _project_dependencies(value: dict[str, object]) -> dict[str, str] | None:
    from packaging.requirements import InvalidRequirement, Requirement

    project = value.get("project")
    if not _object_dict(project):
        return None
    dependencies = project.get("dependencies")
    if not _object_list(dependencies):
        return None
    parsed: dict[str, str] = {}
    for dependency in dependencies:
        if not isinstance(dependency, str):
            return None
        try:
            requirement = Requirement(dependency)
        except InvalidRequirement:
            return None
        name = re.sub(r"[-_.]+", "-", requirement.name).lower()
        specifier = str(requirement.specifier)
        if name in parsed:
            return None
        parsed[name] = specifier
    return parsed


def _specifier_allows_version(specifier: str, version: str) -> bool:
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import InvalidVersion, Version

    try:
        return SpecifierSet(specifier).contains(Version(version), prereleases=True)
    except (InvalidSpecifier, InvalidVersion):
        return False

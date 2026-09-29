from __future__ import annotations

import importlib
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from types import ModuleType
from typing import TypeGuard

from tools.codex_profile_isolation import keychain_isolation_holds
from tools.contract_types import ContractError
from tools.feedback_preflight import (
    classify_operational_browser_status,
    daily_launchd_contract_error,
)
from tools.notion_keychain import (
    NotionApiToken,
    NotionCredentialsUnavailable,
    load_notion_api_token,
)
from tools.startup_project_contract import (
    REQUIRED_PACKAGES,
    codex_profile_contract,
    package_versions,
    project_contract,
    read_required_files,
    toml_document,
)

CODEX_PROFILE = "naver-automation"
CAPABILITY = "unknown_until_q1"


def _notion_access_probe(token: NotionApiToken, target_id: str) -> None:
    from tools.notion_api import probe_notion_access

    probe_notion_access(token, target_id)


@dataclass(frozen=True, slots=True)
class PreflightDependencies:
    python_version: tuple[int, int, int]
    import_module: Callable[[str], ModuleType]
    package_version: Callable[[str], str]
    find_executable: Callable[[str], str | None]
    locked_environment: Callable[[Path], bool]
    codex_home: Path
    credential_loader: Callable[[], NotionApiToken]
    notion_access_probe: Callable[[NotionApiToken, str], None] = _notion_access_probe
    keychain_isolation_probe: Callable[[str, Callable[[], bool]], bool] = (
        lambda codex_binary, trusted_lookup: keychain_isolation_holds(
            codex_binary=codex_binary, trusted_lookup=trusted_lookup
        )
    )


@dataclass(frozen=True, slots=True)
class PreflightResult:
    ok: bool
    connector_write_capability: str
    error_code: str | None

    def as_json(self) -> dict[str, str | bool | None]:
        return {
            "connector_write_capability": self.connector_write_capability,
            "error_code": self.error_code,
            "ok": self.ok,
        }


def _system_dependencies() -> PreflightDependencies:
    return PreflightDependencies(
        python_version=(
            sys.version_info.major,
            sys.version_info.minor,
            sys.version_info.micro,
        ),
        import_module=importlib.import_module,
        package_version=metadata.version,
        find_executable=shutil.which,
        locked_environment=_locked_environment,
        codex_home=_codex_home(),
        credential_loader=load_notion_api_token,
        notion_access_probe=_notion_access_probe,
    )


def _codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured) if configured is not None else Path.home() / ".codex"


def _locked_environment(root: Path) -> bool:
    executable = shutil.which("uv")
    if executable is None:
        return False
    try:
        completed = subprocess.run(
            (executable, "sync", "--locked", "--check", "--offline"),
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _failure(code: str) -> PreflightResult:
    return PreflightResult(False, CAPABILITY, code)


def _dependency_status(
    locked_versions: dict[str, str], dependencies: PreflightDependencies
) -> str | None:
    for package_name, module_name in REQUIRED_PACKAGES:
        expected = locked_versions.get(package_name)
        if expected is None:
            return "lockfile"
        try:
            _ = dependencies.import_module(module_name)
            installed = dependencies.package_version(package_name)
        except (ImportError, metadata.PackageNotFoundError):
            return "dependency_import"
        if installed != expected:
            return "dependency_version"
    return None


def _valid_schemas(files: tuple[tuple[Path, str], ...]) -> bool:
    import json

    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError
    except ImportError:
        return False
    schemas = tuple(
        text
        for path, text in files
        if path.name in {"workflow-contract.schema.json", "stage-result.schema.json"}
    )
    if len(schemas) != 2:
        return False
    for raw_schema in schemas:
        try:
            schema = _schema_object(json.loads(raw_schema))
        except json.JSONDecodeError:
            return False
        if schema is None:
            return False
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError:
            return False
    return True


def _schema_object(value: object) -> dict[str, object] | None:
    if not _object_dict(value):
        return None
    schema: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            return None
        schema[key] = item
    return schema


def _object_dict(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _matches_target(root: Path, target_id: str) -> bool:
    try:
        config = (root / "notion-config.md").read_text(encoding="utf-8")
    except OSError:
        return False
    configured = re.search(r"데이터 소스 ID:\s*`([^`]+)`", config)
    return configured is not None and configured.group(1) == target_id


def _valid_codex_profile(path: Path) -> bool:
    try:
        profile = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return codex_profile_contract(profile) is None


def _notion_access_error(
    dependencies: PreflightDependencies, target_id: str, codex_binary: str
) -> str | None:
    try:
        token = dependencies.credential_loader()
    except NotionCredentialsUnavailable:
        return "notion_credentials_unavailable"
    if not dependencies.keychain_isolation_probe(
        codex_binary, lambda: bool(token)
    ):
        return "codex_keychain_isolation"
    try:
        dependencies.notion_access_probe(token, target_id)
    except ContractError:
        return "notion_access_unavailable"
    return None


def _configured_target_id(root: Path) -> str | None:
    try:
        config = (root / "notion-config.md").read_text(encoding="utf-8")
    except OSError:
        return None
    configured = re.search(r"데이터 소스 ID:\s*`([^`]+)`", config)
    return configured.group(1) if configured is not None else None


def run_preflight(
    root: Path,
    target_id: str | None = None,
    dependencies: PreflightDependencies | None = None,
    *,
    require_notion_credentials: bool = True,
) -> PreflightResult:
    active_dependencies = (
        dependencies if dependencies is not None else _system_dependencies()
    )
    if active_dependencies.python_version[:2] < (3, 12):
        return _failure("python_version")
    files = read_required_files(root)
    if files is None:
        return _failure("required_file")
    pyproject_text = next(text for path, text in files if path.name == "pyproject.toml")
    lock_text = next(text for path, text in files if path.name == "uv.lock")
    pyproject = toml_document(pyproject_text)
    if pyproject is None:
        return _failure("pyproject")
    lock_document = toml_document(lock_text)
    if lock_document is None:
        return _failure("lockfile")
    locked_versions = package_versions(lock_document)
    if locked_versions is None:
        return _failure("lockfile")
    project_error = project_contract(
        pyproject, lock_document, active_dependencies.python_version
    )
    if project_error is not None:
        return _failure(project_error)
    dependency_error = _dependency_status(locked_versions, active_dependencies)
    if dependency_error is not None:
        return _failure(dependency_error)
    if not active_dependencies.locked_environment(root):
        return _failure("lock_environment")
    if not _valid_schemas(files):
        return _failure("schema")
    if target_id is not None and not _matches_target(root, target_id):
        return _failure("target_id")
    codex_binary = active_dependencies.find_executable("codex")
    if codex_binary is None:
        return _failure("codex_executable")
    profile_path = active_dependencies.codex_home / f"{CODEX_PROFILE}.config.toml"
    if not profile_path.is_file() or not _valid_codex_profile(profile_path):
        return _failure("codex_profile")
    if require_notion_credentials:
        configured_target = target_id or _configured_target_id(root)
        if configured_target is None:
            return _failure("target_id")
        access_error = _notion_access_error(
            active_dependencies, configured_target, codex_binary
        )
        if access_error is not None:
            return _failure(access_error)
    return PreflightResult(True, CAPABILITY, None)


__all__ = [
    "PreflightDependencies", "PreflightResult", "classify_operational_browser_status",
    "daily_launchd_contract_error", "run_preflight",
]

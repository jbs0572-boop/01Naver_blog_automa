from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeGuard

from tools.contract_types import ContractError

IGNORED_STAGING_FILES: Final = frozenset({"stage-result.json"})


@dataclass(frozen=True, slots=True)
class PromotionRequest:
    stage: str
    keyword: str | None
    run_id: str
    staging_root: Path
    project_root: Path
    declared: tuple[str, ...]
    ledger_path: Path


def _allowed(stage: str, path: str, keyword: str | None) -> bool:
    match stage:
        case "topic-selector":
            return path.startswith("research/topic-selection-")
        case "researcher":
            return keyword is not None and path == f"research/{keyword}.md"
        case "writer":
            return keyword is not None and path == f"drafts/{keyword}.md"
        case "image-maker":
            return keyword is not None and path.startswith(f"assets/{keyword}/")
        case "content-assembler":
            if keyword is None:
                return False
            finals = {
                f"final/{keyword}{suffix}"
                for suffix in (
                    ".md",
                    "-naver-layout.md",
                    "-naver-copy.md",
                    "-naver-input.md",
                )
            }
            return path in finals
        case "notion-rider" | "naver-rider":
            return False
        case _:
            return False


def _relative_path(value: str) -> Path:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ContractError(f"stage artifact path is unsafe: {value}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _staged_files(root: Path) -> set[str]:
    files: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ContractError(f"staged artifact cannot be a symlink: {path}")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if relative not in IGNORED_STAGING_FILES:
                files.add(relative)
    return files


def _owned_hashes(request: PromotionRequest) -> dict[str, str]:
    if not request.ledger_path.is_file():
        return {}
    try:
        raw = json.loads(request.ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError("stage ownership ledger is invalid") from error
    if not _object_dict(raw) or raw.get("run_id") != request.run_id:
        raise ContractError("stage ownership ledger does not match run")
    artifacts = raw.get("artifacts")
    if not _object_dict(artifacts):
        raise ContractError("stage ownership ledger artifacts are invalid")
    values: dict[str, str] = {}
    for key, value in artifacts.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ContractError("stage ownership ledger artifacts are invalid")
        values[key] = value
    return values


def _object_dict(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _validate(request: PromotionRequest) -> tuple[tuple[Path, Path, str], ...]:
    declared_paths = tuple(_relative_path(value) for value in request.declared)
    declared = {path.as_posix() for path in declared_paths}
    if not declared or declared != _staged_files(request.staging_root):
        raise ContractError("staging contains undeclared or missing artifacts")
    if request.stage == "content-assembler":
        if request.keyword is None:
            raise ContractError("content assembler keyword is missing")
        expected = {
            f"final/{request.keyword}{suffix}"
            for suffix in (
                ".md",
                "-naver-layout.md",
                "-naver-copy.md",
                "-naver-input.md",
            )
        }
        if declared != expected:
            raise ContractError("content assembler must declare four final files")
    owned = _owned_hashes(request)
    validated: list[tuple[Path, Path, str]] = []
    for relative in declared_paths:
        value = relative.as_posix()
        if not _allowed(request.stage, value, request.keyword):
            raise ContractError(f"artifact is outside allowed output: {value}")
        source = request.staging_root / relative
        if not source.is_file() or source.stat().st_size == 0:
            raise ContractError(f"artifact is missing or empty: {value}")
        destination = request.project_root / relative
        if destination.exists():
            expected = owned.get(value)
            if expected is None or not destination.is_file() or _sha256(destination) != expected:
                raise ContractError(f"existing artifact is not owned by this run: {value}")
        validated.append((source, destination, value))
    return tuple(validated)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".stage-promote-", dir=destination.parent)
    os.close(handle)
    temporary_path = Path(temporary)
    try:
        _ = shutil.copyfile(source, temporary_path)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def _restore(destination: Path, previous: bytes | None) -> None:
    if previous is None:
        destination.unlink(missing_ok=True)
        return
    handle, temporary = tempfile.mkstemp(prefix=".stage-restore-", dir=destination.parent)
    os.close(handle)
    temporary_path = Path(temporary)
    try:
        _ = temporary_path.write_bytes(previous)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_ledger(path: Path, run_id: str, hashes: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".ownership-", dir=path.parent)
    os.close(handle)
    temporary_path = Path(temporary)
    try:
        _ = temporary_path.write_text(
            json.dumps({"run_id": run_id, "artifacts": hashes}, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def promote_stage_artifacts(
    *,
    stage: str,
    keyword: str | None,
    run_id: str,
    staging_root: Path,
    project_root: Path,
    declared: tuple[str, ...],
    ledger_path: Path,
) -> tuple[str, ...]:
    request = PromotionRequest(
        stage, keyword, run_id, staging_root, project_root, declared, ledger_path
    )
    validated = _validate(request)
    hashes = _owned_hashes(request)
    for source, destination, relative in validated:
        previous = destination.read_bytes() if destination.is_file() else None
        _atomic_copy(source, destination)
        candidate = {**hashes, relative: _sha256(destination)}
        try:
            _write_ledger(ledger_path, run_id, candidate)
        except OSError:
            _restore(destination, previous)
            raise
        hashes = candidate
    return tuple(relative for _source, _destination, relative in validated)


__all__ = ["promote_stage_artifacts"]

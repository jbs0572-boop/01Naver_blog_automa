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
            return path.startswith("research/topic-selection-") or (
                path.startswith("metadata/creator-advisor/")
                and path.endswith(".json")
            )
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


def _image_archive_path(request: PromotionRequest) -> Path | None:
    if request.stage != "image-maker" or request.keyword is None:
        return None
    for value in (request.keyword, request.run_id):
        if not value or Path(value).name != value or value in {".", ".."}:
            raise ContractError("image asset archive path is unsafe")
    return (
        request.project_root
        / ".automation"
        / "archive"
        / "image-assets"
        / request.keyword
        / request.run_id
    )


def _archive_existing_image_assets(request: PromotionRequest) -> Path | None:
    archive = _image_archive_path(request)
    if archive is None or request.keyword is None:
        return None
    asset_dir = request.project_root / "assets" / request.keyword
    if not asset_dir.exists():
        return None
    if asset_dir.is_symlink() or not asset_dir.is_dir():
        raise ContractError("existing image asset directory is not a regular directory")
    for path in asset_dir.rglob("*"):
        if path.is_symlink():
            raise ContractError(f"existing image asset cannot be a symlink: {path}")
    if not archive.exists():
        _ = archive.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copytree(asset_dir, archive)
        return archive
    if archive.is_symlink() or not archive.is_dir():
        raise ContractError("image asset archive is not a regular directory")
    owned = _owned_hashes(request)
    for path in asset_dir.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(asset_dir).as_posix()
        owned_path = f"assets/{request.keyword}/{relative}"
        if owned.get(owned_path) == _sha256(path):
            continue
        archived = archive / relative
        if not archived.is_file() or _sha256(archived) != _sha256(path):
            raise ContractError(
                f"existing image asset is not preserved in the run archive: {relative}"
            )
    return archive


def _object_dict(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _validate(
    request: PromotionRequest, archive: Path | None
) -> tuple[tuple[Path, Path, str], ...]:
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
            current_run_owned = (
                expected is not None
                and destination.is_file()
                and _sha256(destination) == expected
            )
            archived_previous = (
                archive is not None
                and destination.is_file()
                and (archive / relative.relative_to(Path("assets") / str(request.keyword))).is_file()
                and _sha256(destination)
                == _sha256(archive / relative.relative_to(Path("assets") / str(request.keyword)))
            )
            if not current_run_owned and not archived_previous:
                raise ContractError(f"existing artifact is not owned or archived: {value}")
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


def _restore_ledger(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".ownership-restore-", dir=path.parent)
    os.close(handle)
    temporary_path = Path(temporary)
    try:
        _ = temporary_path.write_bytes(previous)
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
    archive = _archive_existing_image_assets(request)
    validated = _validate(request, archive)
    hashes = _owned_hashes(request)
    previous_files: dict[Path, bytes | None] = {}
    previous_ledger = ledger_path.read_bytes() if ledger_path.is_file() else None
    try:
        for source, destination, relative in validated:
            previous_files[destination] = (
                destination.read_bytes() if destination.is_file() else None
            )
            _atomic_copy(source, destination)
            candidate = {**hashes, relative: _sha256(destination)}
            _write_ledger(ledger_path, run_id, candidate)
            hashes = candidate
    except Exception:
        for destination, previous in reversed(tuple(previous_files.items())):
            _restore(destination, previous)
        _restore_ledger(ledger_path, previous_ledger)
        raise
    return tuple(relative for _source, _destination, relative in validated)


__all__ = ["promote_stage_artifacts"]

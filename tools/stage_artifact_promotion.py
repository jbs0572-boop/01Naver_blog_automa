from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeGuard
from uuid import uuid4

from tools.contract_types import ContractError
from tools.image_quality import validate_image_stage_assets

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
        / uuid4().hex
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
    _ = archive.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copytree(asset_dir, archive)
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


def _restore_archived_image_assets(
    asset_dir: Path, previous_asset_dir: Path
) -> list[OSError]:
    errors: list[OSError] = []
    try:
        archived_hashes = {
            path.relative_to(previous_asset_dir).as_posix(): _sha256(path)
            for path in previous_asset_dir.rglob("*")
            if path.is_file()
        }
    except OSError as error:
        return [error]
    archived_files = {Path(relative) for relative in archived_hashes}
    try:
        shutil.rmtree(asset_dir)
    except FileNotFoundError:
        pass
    except OSError as error:
        errors.append(error)
    try:
        os.replace(previous_asset_dir, asset_dir)
    except OSError as error:
        errors.append(error)
        try:
            asset_dir.mkdir(parents=True, exist_ok=True)
        except OSError as mkdir_error:
            errors.append(mkdir_error)
            return errors
        for relative in sorted(archived_files):
            try:
                destination = asset_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                _ = shutil.copy2(previous_asset_dir / relative, destination)
            except OSError as copy_error:
                errors.append(copy_error)
        try:
            current_files = sorted(asset_dir.rglob("*"), reverse=True)
        except OSError as scan_error:
            errors.append(scan_error)
            current_files = []
        for current in current_files:
            relative = current.relative_to(asset_dir)
            try:
                if current.is_file() and relative not in archived_files:
                    current.unlink()
                elif current.is_dir() and not any(current.iterdir()):
                    current.rmdir()
            except OSError as cleanup_error:
                errors.append(cleanup_error)
    try:
        restored_hashes = {
            path.relative_to(asset_dir).as_posix(): _sha256(path)
            for path in asset_dir.rglob("*")
            if path.is_file()
        }
    except OSError as error:
        errors.append(error)
        return errors
    if restored_hashes == archived_hashes:
        return []
    if not errors:
        errors.append(OSError("restored image assets do not match the archive"))
    return errors


def _replace_image_asset_set(
    request: PromotionRequest,
    validated: tuple[tuple[Path, Path, str], ...],
    archive: Path | None,
) -> tuple[str, ...]:
    if request.keyword is None:
        raise ContractError("image-maker keyword is missing")
    asset_dir = request.project_root / "assets" / request.keyword
    _ = asset_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(
        tempfile.mkdtemp(prefix=".image-stage-replace-", dir=asset_dir.parent)
    )
    staged_asset_dir = temporary_root / "staged"
    previous_asset_dir = temporary_root / "previous"
    failed_asset_dir = temporary_root / "failed"
    source_asset_dir = request.staging_root / "assets" / request.keyword
    previous_ledger = request.ledger_path.read_bytes() if request.ledger_path.is_file() else None
    previous_moved = False
    new_installed = False
    ledger_write_attempted = False
    try:
        _ = shutil.copytree(source_asset_dir, staged_asset_dir)
        if asset_dir.exists():
            os.replace(asset_dir, previous_asset_dir)
            previous_moved = True
        os.replace(staged_asset_dir, asset_dir)
        new_installed = True
        hashes = {
            relative: _sha256(request.project_root / relative)
            for _source, _destination, relative in validated
        }
        ledger_write_attempted = True
        _write_ledger(request.ledger_path, request.run_id, hashes)
    except Exception as error:
        rollback_errors: list[OSError] = []
        if new_installed and asset_dir.exists():
            try:
                os.replace(asset_dir, failed_asset_dir)
            except OSError:
                if previous_moved and previous_asset_dir.exists():
                    rollback_errors.extend(
                        _restore_archived_image_assets(asset_dir, previous_asset_dir)
                    )
                    previous_moved = False
                else:
                    try:
                        shutil.rmtree(asset_dir)
                    except OSError as rollback_error:
                        rollback_errors.append(rollback_error)
        if previous_moved and previous_asset_dir.exists() and not asset_dir.exists():
            rollback_errors.extend(
                _restore_archived_image_assets(asset_dir, previous_asset_dir)
            )
        if ledger_write_attempted:
            try:
                _restore_ledger(request.ledger_path, previous_ledger)
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
        if rollback_errors:
            raise ContractError(
                "image asset replacement failed and rollback is incomplete; "
                + f"prior assets are archived at {archive}; "
                + f"recoverable files remain at {temporary_root}"
            ) from error
        try:
            shutil.rmtree(temporary_root)
        except OSError:
            pass
        raise
    try:
        shutil.rmtree(temporary_root)
    except OSError:
        # The canonical set and ledger are committed; a leftover backup is recoverable.
        pass
    return tuple(relative for _source, _destination, relative in validated)


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
    if stage == "image-maker":
        if keyword is None:
            raise ContractError("image-maker keyword is missing")
        _ = validate_image_stage_assets(
            staging_root / "assets" / keyword,
            project_root / "drafts" / f"{keyword}.md",
        )
    archive = _archive_existing_image_assets(request)
    validated = _validate(request, archive)
    if stage == "image-maker":
        return _replace_image_asset_set(request, validated, archive)
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
    except Exception as error:
        rollback_errors: list[OSError] = []
        for destination, previous in reversed(tuple(previous_files.items())):
            try:
                _restore(destination, previous)
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
        try:
            _restore_ledger(ledger_path, previous_ledger)
        except OSError as rollback_error:
            rollback_errors.append(rollback_error)
        if rollback_errors:
            raise ContractError(
                "artifact promotion failed and rollback is incomplete; "
                + f"preserve recoverable files under {project_root}"
            ) from error
        raise
    return tuple(relative for _source, _destination, relative in validated)


__all__ = ["promote_stage_artifacts"]

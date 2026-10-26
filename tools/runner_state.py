from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from datetime import datetime
from pathlib import Path

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.runner_types import RunnerRequest, RunnerResult, RunStatus


def state_paths(
    root: Path, run_id: str, state_dir: Path | None = None
) -> tuple[Path, Path, Path]:
    parts = Path(run_id).parts
    if (
        not run_id
        or Path(run_id).is_absolute()
        or len(parts) != 1
        or parts[0] in {".", ".."}
    ):
        raise ContractError("run_id must be a single safe path component")
    base = state_dir if state_dir is not None else root / ".automation"
    return (
        base / "state" / f"{run_id}.json",
        base / "logs" / f"{run_id}.jsonl",
        base / "locks" / f"{run_id}.lock",
    )


def _safe_now(now: datetime | None) -> datetime:
    value = now if now is not None else datetime.now().astimezone()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError("runner timestamps must include a timezone")
    return value


def stable_run_id(request: RunnerRequest) -> str:
    now = _safe_now(request.now)
    material = "|".join(
        (request.job, request.mode, request.keyword or "", now.date().isoformat())
    )
    return "RUN-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_digest(path: Path) -> str:
    return _digest_bytes(path.read_bytes())


def input_fingerprint(request: RunnerRequest) -> str:
    records: list[JSONValue] = []
    if request.keyword is not None:
        keyword_parts = Path(request.keyword).parts
        if (
            not request.keyword
            or Path(request.keyword).is_absolute()
            or len(keyword_parts) != 1
            or keyword_parts[0] in {".", ".."}
        ):
            raise ContractError("keyword must be a single safe path component")
        keyword_dir = request.root / "assets" / request.keyword
        candidates = [
            request.root / "research" / f"{request.keyword}.md",
            request.root / "research" / f"topic-selection-{request.keyword}.md",
            request.root / "drafts" / f"{request.keyword}.md",
            request.root / "final" / f"{request.keyword}.md",
            request.root / "final" / f"{request.keyword}-naver-layout.md",
            request.root / "final" / f"{request.keyword}-naver-copy.md",
        ]
        if keyword_dir.is_dir():
            candidates.extend(
                path for path in sorted(keyword_dir.rglob("*")) if path.is_file()
            )
        for path in sorted(set(candidates)):
            record: JSONMap = {
                "path": path.relative_to(request.root).as_posix(),
                "sha256": file_digest(path) if path.is_file() else None,
            }
            records.append(record)
    else:
        for directory in ("research", "drafts", "final", "assets", "runs"):
            path = request.root / directory
            files = (
                [item for item in sorted(path.rglob("*")) if item.is_file()]
                if path.is_dir()
                else []
            )
            records.extend(
                {
                    "path": item.relative_to(request.root).as_posix(),
                    "sha256": file_digest(item),
                }
                for item in files
            )
    material: JSONMap = {
        "job": request.job,
        "mode": request.mode,
        "keyword": request.keyword,
        "inputs": records,
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + _digest_bytes(encoded)


_STATE_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_STATE_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def _open_state_parent(path: Path) -> tuple[int, tuple[tuple[str, int], ...]]:
    """Open every parent by descriptor so a swapped path cannot redirect writes."""
    absolute = Path(os.path.abspath(path))
    try:
        root_fd = os.open(absolute.anchor, _STATE_DIRECTORY_FLAGS)
    except OSError as error:
        raise ContractError("runner state path is unsafe") from error
    chain: list[tuple[str, int]] = []
    parent_fd = root_fd
    try:
        for name in absolute.parts[1:-1]:
            try:
                _ = os.mkdir(name, 0o755, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except FileExistsError:
                pass
            descriptor = os.open(name, _STATE_DIRECTORY_FLAGS, dir_fd=parent_fd)
            chain.append((name, descriptor))
            parent_fd = descriptor
    except OSError as error:
        for _, descriptor in reversed(chain):
            os.close(descriptor)
        os.close(root_fd)
        raise ContractError("runner state path is unsafe") from error
    return root_fd, tuple(chain)


def _state_parent_is_current(
    root_fd: int, chain: tuple[tuple[str, int], ...]
) -> bool:
    try:
        root = os.stat("/", follow_symlinks=False)
        opened_root = os.fstat(root_fd)
        if not stat.S_ISDIR(root.st_mode) or (root.st_dev, root.st_ino) != (
            opened_root.st_dev,
            opened_root.st_ino,
        ):
            return False
        parents = (root_fd, *(descriptor for _, descriptor in chain[:-1]))
        return all(
            (entry := os.stat(name, dir_fd=parent, follow_symlinks=False)).st_dev
            == (opened := os.fstat(descriptor)).st_dev
            and entry.st_ino == opened.st_ino
            and stat.S_ISDIR(entry.st_mode)
            for parent, (name, descriptor) in zip(parents, chain, strict=True)
        )
    except OSError:
        return False


def _read_state_at(parent_fd: int, name: str) -> bytes | None:
    try:
        descriptor = os.open(name, _STATE_READ_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ContractError("runner state path is unsafe") from error
    try:
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ContractError("runner state path is unsafe")
            return handle.read()
    except OSError as error:
        raise ContractError("runner state path is unsafe") from error


def _write_state_temp(parent_fd: int, name: str, encoded: bytes) -> tuple[int, int]:
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=parent_fd,
    )
    identity = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            _ = handle.write(encoded)
            handle.flush()
            _ = os.fsync(handle.fileno())
        return identity.st_dev, identity.st_ino
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                os.unlink(name, dir_fd=parent_fd)
        except OSError:
            pass
        raise


def _rollback_state_at(
    parent_fd: int,
    name: str,
    replacement_identity: tuple[int, int],
    previous: bytes | None,
) -> None:
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != replacement_identity:
            return
        if previous is None:
            os.unlink(name, dir_fd=parent_fd)
        else:
            temporary = f".{name}.rollback-{uuid.uuid4().hex}"
            identity = _write_state_temp(parent_fd, temporary, previous)
            try:
                current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == replacement_identity:
                    os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            finally:
                try:
                    current_temp = os.stat(
                        temporary, dir_fd=parent_fd, follow_symlinks=False
                    )
                    if (current_temp.st_dev, current_temp.st_ino) == identity:
                        os.unlink(temporary, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
        os.fsync(parent_fd)
    except OSError as error:
        raise ContractError("runner state rollback failed") from error


def atomic_write_json(path: Path, value: JSONMap) -> None:
    encoded = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    root_fd, chain = _open_state_parent(path)
    parent_fd = chain[-1][1] if chain else root_fd
    temporary = f".{path.name}.{uuid.uuid4().hex}"
    temporary_identity: tuple[int, int] | None = None
    replacement_identity: tuple[int, int] | None = None
    previous: bytes | None = None
    try:
        previous = _read_state_at(parent_fd, path.name)
        temporary_identity = _write_state_temp(parent_fd, temporary, encoded)
        if not _state_parent_is_current(root_fd, chain):
            raise ContractError("runner path changed before state write")
        os.replace(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        replacement_identity = temporary_identity
        os.fsync(parent_fd)
        if not _state_parent_is_current(root_fd, chain):
            _rollback_state_at(parent_fd, path.name, replacement_identity, previous)
            replacement_identity = None
            raise ContractError("runner path changed during state write")
    except ContractError:
        raise
    except OSError as error:
        if replacement_identity is not None:
            try:
                _rollback_state_at(
                    parent_fd, path.name, replacement_identity, previous
                )
                replacement_identity = None
            except ContractError as rollback_error:
                raise rollback_error from error
        raise ContractError(f"could not atomically write state: {path}") from error
    finally:
        if temporary_identity is not None and replacement_identity is None:
            try:
                current = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == temporary_identity:
                    os.unlink(temporary, dir_fd=parent_fd)
            except OSError:
                pass
        for _, descriptor in reversed(chain):
            os.close(descriptor)
        os.close(root_fd)


def read_state(path: Path) -> JSONMap:
    try:
        raw: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise ContractError(f"could not read runner state: {path}") from error
    if not isinstance(raw, dict):
        raise ContractError(f"runner state must be an object: {path}")
    return raw


def result_from_state(state: JSONMap, state_path: Path, log_path: Path) -> RunnerResult:
    run_id = state.get("run_id")
    status = state.get("status")
    message = state.get("message")
    stages = state.get("stages")
    if (
        not isinstance(run_id, str)
        or not isinstance(status, str)
        or not isinstance(message, str)
        or not isinstance(stages, dict)
    ):
        raise ContractError(f"runner state has invalid result fields: {state_path}")
    completed = tuple(
        key for key, value in stages.items() if value == RunStatus.PASSED.value
    )
    try:
        parsed_status = RunStatus(status)
    except ValueError as error:
        raise ContractError(f"runner state has invalid status: {status}") from error
    return RunnerResult(run_id, parsed_status, state_path, log_path, completed, message)


def request_from_state(
    state: JSONMap, root: Path, state_dir: Path | None = None
) -> RunnerRequest:
    job = state.get("job")
    mode = state.get("mode")
    keyword = state.get("keyword")
    run_id = state.get("run_id")
    if (
        not isinstance(job, str)
        or not isinstance(mode, str)
        or (keyword is not None and not isinstance(keyword, str))
        or not isinstance(run_id, str)
    ):
        raise ContractError(
            "runner state cannot be recovered: request fields are invalid"
        )
    return RunnerRequest(
        root=root,
        job=job,
        mode=mode,
        keyword=keyword,
        run_id=run_id,
        dry_run=True,
        state_dir=state_dir,
    )


__all__ = [
    "atomic_write_json",
    "file_digest",
    "input_fingerprint",
    "read_state",
    "request_from_state",
    "result_from_state",
    "stable_run_id",
    "state_paths",
]

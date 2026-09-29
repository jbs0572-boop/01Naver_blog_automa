from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.runner_secure_fs import (
    secure_atomic_replace,
    secure_read,
    secure_storage_active,
)
from tools.runner_state_request import (
    file_digest,
    input_fingerprint,
    request_from_state,
    stable_run_id,
)
from tools.runner_types import RunnerResult, RunStatus


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


def atomic_write_json(path: Path, value: JSONMap) -> None:
    if secure_storage_active():
        try:
            encoded = (
                json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            ).encode()
            secure_atomic_replace(path, encoded)
        except (TypeError, ValueError, ContractError) as error:
            raise ContractError(f"could not atomically write state: {path}") from error
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            _ = handle.write(
                json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as error:
        raise ContractError(f"could not atomically write state: {path}") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as error:
                raise ContractError(f"could not remove temporary state: {temporary}") from error


def read_state(path: Path) -> JSONMap:
    try:
        encoded = secure_read(path) if secure_storage_active() else path.read_bytes()
        raw: JSONValue = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError, OSError, ContractError) as error:
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
        key
        for key, value in stages.items()
        if value in {RunStatus.PASSED.value, RunStatus.VALIDATED.value}
    )
    try:
        parsed_status = RunStatus(status)
    except ValueError as error:
        raise ContractError(f"runner state has invalid status: {status}") from error
    return RunnerResult(run_id, parsed_status, state_path, log_path, completed, message)


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

from __future__ import annotations

import json
import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.runner_secure_fs import (
    secure_create,
    secure_read_snapshot,
    secure_unlink_if_identity,
)
from tools.runner_types import RunnerBlocked


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _existing_pid(path: Path) -> tuple[int | None, tuple[int, int]]:
    try:
        encoded, identity = secure_read_snapshot(path)
        raw: JSONValue = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError, ContractError) as error:
        raise ContractError(f"runner lock cannot be read: {path}") from error
    if not isinstance(raw, dict):
        return None, identity
    pid = raw.get("pid")
    value = pid if isinstance(pid, int) and not isinstance(pid, bool) else None
    return value, identity


@contextmanager
def acquire_lock(path: Path, metadata: JSONMap) -> Generator[None, None, None]:
    encoded = (
        json.dumps(metadata, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode()
    while True:
        try:
            lease = secure_create(path, encoded)
        except FileExistsError:
            try:
                pid, identity = _existing_pid(path)
            except FileNotFoundError:
                continue
            if pid is not None and _pid_alive(pid):
                raise RunnerBlocked(f"runner execution is already locked: {path}")
            try:
                if not secure_unlink_if_identity(path, identity):
                    continue
            except ContractError as error:
                raise ContractError(
                    f"stale runner lock cannot be removed: {path}"
                ) from error
            continue
        break
    try:
        yield
    finally:
        lease.release()


__all__ = ["acquire_lock"]

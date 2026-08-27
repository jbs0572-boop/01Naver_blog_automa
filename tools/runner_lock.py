from __future__ import annotations

import json
import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from tools.contract_types import ContractError, JSONMap, JSONValue
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


def _existing_pid(path: Path) -> int | None:
    try:
        raw: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise ContractError(f"runner lock cannot be read: {path}") from error
    if not isinstance(raw, dict):
        return None
    pid = raw.get("pid")
    return pid if isinstance(pid, int) and not isinstance(pid, bool) else None


@contextmanager
def acquire_lock(path: Path, metadata: JSONMap) -> Generator[None, None, None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pid = _existing_pid(path)
            if pid is not None and _pid_alive(pid):
                raise RunnerBlocked(f"runner execution is already locked: {path}")
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as error:
                raise ContractError(
                    f"stale runner lock cannot be removed: {path}"
                ) from error
            continue
        except OSError as error:
            raise ContractError(f"runner lock cannot be created: {path}") from error
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                _ = handle.write(
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True) + "\n"
                )
                _ = handle.flush()
                os.fsync(handle.fileno())
            break
        except OSError as error:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise ContractError(f"runner lock cannot be written: {path}") from error
    try:
        yield
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = ["acquire_lock"]

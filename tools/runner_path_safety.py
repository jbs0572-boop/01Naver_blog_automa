from __future__ import annotations

import stat

from tools.contract_types import ContractError
from tools.runner_types import RunnerRequest


def validate_weekly_runner_paths(request: RunnerRequest) -> None:
    try:
        root = request.root.resolve(strict=True)
    except OSError as error:
        raise ContractError("weekly runner path is unsafe") from error
    bases = [request.root / ".automation"]
    if request.state_dir is not None:
        bases.append(request.state_dir)
    for base in bases:
        try:
            relative = base.relative_to(request.root)
        except ValueError as error:
            raise ContractError("weekly runner path is unsafe") from error
        current = root
        for part in relative.parts:
            current /= part
            try:
                info = current.lstat()
            except FileNotFoundError:
                break
            except OSError as error:
                raise ContractError("weekly runner path is unsafe") from error
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise ContractError("weekly runner path is unsafe")


__all__ = ["validate_weekly_runner_paths"]

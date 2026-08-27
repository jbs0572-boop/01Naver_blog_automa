from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.contract_types import ContractError
from tools.runner_execution import get_status, recover_job, run_job
from tools.runner_types import RunnerRequest, RunStatus


def _options(arguments: list[str]) -> dict[str, str | bool]:
    values: dict[str, str | bool] = {}
    index = 0
    while index < len(arguments):
        name = arguments[index]
        if name == "--dry-run":
            values["dry-run"] = True
            index += 1
            continue
        if not name.startswith("--") or index + 1 >= len(arguments):
            raise ContractError(f"expected --key value, got: {name}")
        values[name[2:]] = arguments[index + 1]
        index += 2
    return values


def _required(values: dict[str, str | bool], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise ContractError(f"missing option: --{key}")
    return value


def _path_option(values: dict[str, str | bool], key: str, default: str) -> Path:
    value = values.get(key, default)
    if not isinstance(value, str):
        raise ContractError(f"invalid option: --{key}")
    return Path(value).resolve()


def _state_option(values: dict[str, str | bool], root: Path) -> Path | None:
    key = "state-dir" if "state-dir" in values else "state-root"
    return (
        _path_option(values, key, str(root / ".automation")) if key in values else None
    )


def _cli(arguments: list[str]) -> int:
    if len(arguments) < 2:
        raise ContractError("command required: run, status, or recover")
    command = arguments[1]
    if command == "run":
        if len(arguments) < 3:
            raise ContractError("run requires a job name")
        values = _options(arguments[3:])
        root = _path_option(values, "root", ".")
        keyword_value = values.get("keyword")
        run_id_value = values.get("run-id")
        request = RunnerRequest(
            root=root,
            job=arguments[2],
            mode=_required(values, "mode"),
            keyword=keyword_value if isinstance(keyword_value, str) else None,
            run_id=run_id_value if isinstance(run_id_value, str) else None,
            dry_run=values.get("dry-run") is True,
            state_dir=_state_option(values, root),
        )
        result = run_job(request)
    elif command == "status":
        values = _options(arguments[2:])
        root = _path_option(values, "root", ".")
        state = get_status(
            root, _required(values, "run-id"), _state_option(values, root)
        )
        print(json.dumps(state, ensure_ascii=False, sort_keys=True))
        return 0
    elif command == "recover":
        values = _options(arguments[2:])
        root = _path_option(values, "root", ".")
        result = recover_job(
            RunnerRequest(
                root=root,
                job="",
                mode="",
                run_id=_required(values, "run-id"),
                dry_run=values.get("dry-run") is True,
                state_dir=_state_option(values, root),
            )
        )
    else:
        raise ContractError(f"unknown runner command: {command}")
    print(json.dumps(result.as_json(), ensure_ascii=False, sort_keys=True))
    return 0 if result.status in {RunStatus.PASSED, RunStatus.SKIPPED} else 2


def main(arguments: list[str] | None = None) -> int:
    try:
        return _cli(sys.argv if arguments is None else arguments)
    except ContractError as error:
        print(f"automation-runner: {error}", file=sys.stderr)
        return 2

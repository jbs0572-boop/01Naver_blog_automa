from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tools.contract_types import ContractError
from tools.feedback_preflight import operational_browser_preflight
from tools.runner_state import read_state, request_from_state, state_paths
from tools.startup_preflight import PreflightDependencies, run_preflight
from tools.weekly_feedback_inputs import prepare_feedback_inputs


def _runner_main() -> Callable[[list[str]], int]:
    from tools.runner_cli import main

    return main


def _option(arguments: list[str], name: str) -> str | None:
    positions = [index for index, value in enumerate(arguments) if value == name]
    if not positions:
        return None
    if len(positions) != 1 or positions[0] + 1 >= len(arguments):
        return ""
    value = arguments[positions[0] + 1]
    return value if not value.startswith("--") else ""


def _print(result: Mapping[str, str | bool | int | None]) -> None:
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def _explicit_request(arguments: list[str]) -> tuple[Path, str | None] | None:
    if len(arguments) < 2 or arguments[1] != "preflight":
        return None
    root = _option(arguments[2:], "--root")
    target_id = _option(arguments[2:], "--target-id")
    allowed = {"--root", "--target-id"}
    names = arguments[2::2]
    if (
        root is None
        or root == ""
        or target_id == ""
        or any(name not in allowed for name in names)
        or len(arguments[2:]) != len(names) * 2
    ):
        return (Path(), "")
    return (Path(root).resolve(), target_id)


def _failure() -> int:
    _print(
        {
            "connector_write_capability": "unknown_until_q1",
            "error_code": "preflight_arguments",
            "ok": False,
        }
    )
    return 2


def _effective_dry_run(arguments: list[str], root: Path) -> bool | None:
    if len(arguments) < 2:
        return False
    command = arguments[1]
    if command not in {"recover", "resume"}:
        return "--dry-run" in arguments[2:]
    if "--dry-run" in arguments[2:]:
        return True
    run_id = _option(arguments[2:], "--run-id")
    state_root = _option(arguments[2:], "--state-dir")
    if state_root is None:
        state_root = _option(arguments[2:], "--state-root")
    if run_id is None or run_id == "" or state_root == "":
        return None
    state_dir = (
        Path(os.path.abspath(state_root)) if state_root is not None else None
    )
    try:
        state_path, _, _ = state_paths(root, run_id, state_dir)
        state = read_state(state_path)
        recovered = request_from_state(state, root, state_dir)
    except ContractError:
        return None
    if (
        recovered.run_id != run_id
        or not isinstance(state.get("dry_run"), bool)
    ):
        return None
    return recovered.dry_run


def _requires_notion_credentials(arguments: list[str], dry_run: bool) -> bool:
    if len(arguments) < 2:
        return False
    command = arguments[1]
    if command == "run":
        return (
            len(arguments) >= 3
            and arguments[2] == "daily-generate"
            and not dry_run
        )
    if command in {"recover", "resume"}:
        return not dry_run
    return False


def _is_new_daily_request(arguments: list[str]) -> bool:
    return len(arguments) >= 3 and arguments[1:3] == ["run", "daily-generate"]


def _is_weekly_read_only_request(arguments: list[str]) -> bool:
    return len(arguments) >= 3 and arguments[1:3] == ["run", "weekly-improve"]


def _weekly_inputs_are_valid(root: Path) -> bool:
    value = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
    try:
        _ = prepare_feedback_inputs(root, datetime.fromisoformat(value), value)
    except ContractError as error:
        _print({"error_code": str(error), "ok": False})
        return False
    return True


def main(
    arguments: list[str] | None = None,
    *,
    dependencies: PreflightDependencies | None = None,
    runner_loader: Callable[[], Callable[[list[str]], int]] | None = None,
) -> int:
    active_arguments = list(sys.argv if arguments is None else arguments)
    explicit = _explicit_request(active_arguments)
    if explicit is not None:
        root, target_id = explicit
        if target_id == "":
            return _failure()
        result = run_preflight(root, target_id, dependencies)
        _print(result.as_json())
        return 0 if result.ok else 2
    root_option = _option(active_arguments[1:], "--root")
    if root_option == "":
        return _failure()
    root = Path.cwd() if root_option is None else Path(root_option).resolve()
    loader = _runner_main if runner_loader is None else runner_loader
    if _is_weekly_read_only_request(active_arguments) and _weekly_inputs_are_valid(root):
        return loader()(active_arguments)
    if _is_weekly_read_only_request(active_arguments):
        return 2
    if _is_new_daily_request(active_arguments):
        browser_result = operational_browser_preflight(root)
        if browser_result.operational_browser_status == "browser_preflight_failed":
            _print(browser_result.as_json())
            return 2
    effective_dry_run = _effective_dry_run(active_arguments, root)
    if effective_dry_run is None:
        _print(
            {
                "connector_write_capability": "unknown_until_q1",
                "error_code": "preflight_resume_state",
                "ok": False,
            }
        )
        return 2
    result = run_preflight(
        root,
        dependencies=dependencies,
        require_notion_credentials=_requires_notion_credentials(
            active_arguments, effective_dry_run
        ),
    )
    if not result.ok:
        _print(result.as_json())
        return 2
    return loader()(active_arguments)


if __name__ == "__main__":
    raise SystemExit(main())

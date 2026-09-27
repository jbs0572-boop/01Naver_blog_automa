from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

from tools.browser_gateway import BrowserCapability, load_aside_browser_gateway
from tools.contract_types import ContractError
from tools.dashboard_model_settings import ModelSettingsStore
from tools.external_adapter import NotionAdapter
from tools.naver_adapter import NaverBrowserAdapter
from tools.runner_execution import (
    confirm_job,
    get_status,
    recover_job,
    resume_job,
    run_job,
)
from tools.runner_state import read_state, state_paths
from tools.runner_types import (
    ConfirmationInput,
    RunnerRequest,
    RunStatus,
    TopicSelectionContext,
)

type NotionAdapterFactory = Callable[[Path], NotionAdapter]
type NaverAdapterFactory = Callable[[Path], NaverBrowserAdapter]


def _default_notion_adapter_factory(_root: Path) -> NotionAdapter:
    from tools.notion_api import NotionApiAdapter

    return NotionApiAdapter()


def _live_notion_adapter(
    root: Path,
    dry_run: bool,
    factory: NotionAdapterFactory | None,
) -> NotionAdapter | None:
    if dry_run:
        return None
    active_factory = (
        _default_notion_adapter_factory if factory is None else factory
    )
    return active_factory(root)


def _live_naver_adapter(
    root: Path,
    factory: NaverAdapterFactory | None,
) -> tuple[NaverBrowserAdapter, Callable[[], None]]:
    if factory is not None:
        return factory(root), lambda: None
    gateway = load_aside_browser_gateway(
        root,
        required_capabilities=frozenset({BrowserCapability.NAVER_DRAFT_WRITE}),
    )
    return gateway.create_naver_adapter(discard_recovery=True), gateway.close


def _options(
    arguments: list[str], allowed: frozenset[str]
) -> dict[str, str | bool]:
    values: dict[str, str | bool] = {}
    index = 0
    while index < len(arguments):
        name = arguments[index]
        if name == "--mode":
            raise ContractError("--mode was retired; the workflow is always production")
        key = name.removeprefix("--")
        if key not in allowed:
            raise ContractError(f"unknown option: {name}")
        if key in values:
            raise ContractError(f"duplicate option: {name}")
        if name in {"--dry-run", "--auto-topic"}:
            values[key] = True
            index += 1
            continue
        if not name.startswith("--") or index + 1 >= len(arguments):
            raise ContractError(f"expected --key value, got: {name}")
        values[key] = arguments[index + 1]
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


def _state_option(values: dict[str, str | bool], _root: Path) -> Path | None:
    key = "state-dir" if "state-dir" in values else "state-root"
    value = values.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractError(f"invalid option: --{key}")
    return Path(os.path.abspath(value))


def _cli(
    arguments: list[str],
    notion_adapter_factory: NotionAdapterFactory | None = None,
    naver_adapter_factory: NaverAdapterFactory | None = None,
) -> int:
    if len(arguments) < 2:
        raise ContractError(
            "command required: run, status, recover, resume, or confirm"
        )
    command = arguments[1]
    if command == "run":
        if len(arguments) < 3:
            raise ContractError("run requires a job name")
        values = _options(
            arguments[3:],
            frozenset({
                "root", "run-id", "dry-run", "state-dir", "state-root",
                "keyword", "auto-topic", "as-of-date",
            }),
        )
        root = _path_option(values, "root", ".")
        keyword_value = values.get("keyword")
        run_id_value = values.get("run-id")
        auto_topic = values.get("auto-topic") is True
        as_of_date = values.get("as-of-date")
        if arguments[2] == "daily-generate" and not isinstance(as_of_date, str):
            raise ContractError("daily-generate requires --as-of-date")
        selection_context = None
        if isinstance(as_of_date, str):
            try:
                normalized_date = date.fromisoformat(as_of_date.strip()).isoformat()
            except ValueError as error:
                raise ContractError("invalid option: --as-of-date") from error
            selection_context = TopicSelectionContext("", "", "", normalized_date)
        dry_run = values.get("dry-run") is True
        notion_adapter = _live_notion_adapter(
            root,
            dry_run or arguments[2] != "daily-generate",
            notion_adapter_factory,
        )
        request = RunnerRequest(
            root=root,
            job=arguments[2],
            keyword=keyword_value if isinstance(keyword_value, str) else None,
            run_id=run_id_value if isinstance(run_id_value, str) else None,
            dry_run=dry_run,
            state_dir=_state_option(values, root),
            auto_topic=auto_topic,
            selection_context=selection_context,
            notion_adapter=notion_adapter,
            model_config=(
                ModelSettingsStore(root).snapshot()
                if arguments[2] == "daily-generate"
                else None
            ),
        )
        result = run_job(request)
    elif command == "status":
        values = _options(
            arguments[2:], frozenset({"root", "run-id", "state-dir", "state-root"})
        )
        root = _path_option(values, "root", ".")
        state = get_status(
            root, _required(values, "run-id"), _state_option(values, root)
        )
        print(json.dumps(state, ensure_ascii=False, sort_keys=True))
        return 0
    elif command == "recover":
        values = _options(
            arguments[2:],
            frozenset({"root", "run-id", "dry-run", "state-dir", "state-root"}),
        )
        root = _path_option(values, "root", ".")
        result = recover_job(
            RunnerRequest(
                root=root,
                job="",
                run_id=_required(values, "run-id"),
                dry_run=values.get("dry-run") is True,
                state_dir=_state_option(values, root),
                notion_adapter=_live_notion_adapter(
                    root,
                    values.get("dry-run") is True,
                    notion_adapter_factory,
                ),
            )
        )
    elif command == "resume":
        values = _options(
            arguments[2:], frozenset({"root", "run-id", "state-dir", "state-root"})
        )
        root = _path_option(values, "root", ".")
        run_id = _required(values, "run-id")
        state_path, _, _ = state_paths(root, run_id, _state_option(values, root))
        state = read_state(state_path)
        stages = state.get("stages")
        requires_naver = (
            state.get("job") == "daily-generate"
            and isinstance(stages, dict)
            and stages.get("notion-rider") in {"passed", "validated"}
            and stages.get("naver-rider") not in {"passed", "validated"}
            and state.get("status") != RunStatus.DRAFT_SAVED.value
            and state.get("naver_save_outcome_uncertain") is not True
        )
        resume_naver_adapter: NaverBrowserAdapter | None = None
        cleanup_naver = lambda: None
        if requires_naver:
            resume_naver_adapter, cleanup_naver = _live_naver_adapter(
                root, naver_adapter_factory
            )
        try:
            result = resume_job(
                RunnerRequest(
                root=root,
                job="",
                run_id=run_id,
                state_dir=_state_option(values, root),
                notion_adapter=_live_notion_adapter(
                    root,
                    False,
                    notion_adapter_factory,
                ),
                naver_adapter=resume_naver_adapter,
                )
            )
        finally:
            cleanup_naver()
    elif command == "confirm":
        values = _options(
            arguments[2:],
            frozenset({"root", "run-id", "action", "actor", "state-dir", "state-root", "confirmation-nonce"}),
        )
        root = _path_option(values, "root", ".")
        actor = values.get("actor", "operator")
        if not isinstance(actor, str):
            raise ContractError("invalid option: --actor")
        action = _required(values, "action")
        naver_adapter: NaverBrowserAdapter | None = None
        cleanup_naver = lambda: None
        if action == "naver-draft-save":
            naver_adapter, cleanup_naver = _live_naver_adapter(
                root, naver_adapter_factory
            )
        try:
            result = confirm_job(ConfirmationInput(
                root=root,
                run_id=_required(values, "run-id"),
                action=action,
                state_dir=_state_option(values, root),
                actor=actor,
                naver_adapter=naver_adapter,
                confirmation_nonce=_required(values, "confirmation-nonce"),
            ))
        finally:
            cleanup_naver()
    else:
        raise ContractError(f"unknown runner command: {command}")
    print(json.dumps(result.as_json(), ensure_ascii=False, sort_keys=True))
    return (
        0
        if result.status
        in {
            RunStatus.PASSED,
            RunStatus.VALIDATED,
            RunStatus.LOCAL_ONLY,
            RunStatus.SKIPPED,
            RunStatus.READY_FOR_NAVER,
            RunStatus.AWAITING_USER_CONFIRMATION,
            RunStatus.DRAFT_SAVED,
        }
        else 2
    )


def main(
    arguments: list[str] | None = None,
    *,
    notion_adapter_factory: NotionAdapterFactory | None = None,
    naver_adapter_factory: NaverAdapterFactory | None = None,
) -> int:
    try:
        return _cli(
            sys.argv if arguments is None else arguments,
            notion_adapter_factory,
            naver_adapter_factory,
        )
    except ContractError as error:
        print(f"automation-runner: {error}", file=sys.stderr)
        return 2

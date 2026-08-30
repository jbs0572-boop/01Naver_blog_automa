from __future__ import annotations

# pyright: reportAny=false
import json
import os
import sys
from pathlib import Path

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.gate import GateRequest, authorize_external_write
from tools.tool_policy import (
    ToolAction,
    classify_tool_call,
    is_notion_connector,
    notion_resource_id,
)


def _required_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise ContractError(f"missing environment variable: {key}")
    return value


def _workspace_from_cwd(cwd: str) -> Path:
    current = Path(cwd).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "AGENTS.md").is_file() and (
            candidate / "notion-config.md"
        ).is_file():
            return candidate
    raise ContractError("workflow workspace root could not be found from hook cwd")


def _emit(permission: str, reason: str) -> int:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": permission,
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    return 0


def run_hook() -> int:
    try:
        raw_value: JSONValue = json.loads(sys.stdin.read())
        if not isinstance(raw_value, dict):
            raise ContractError("hook input must be a JSON object")
        raw: JSONMap = raw_value
        tool_name = raw.get("tool_name")
        if not isinstance(tool_name, str):
            raise ContractError("hook tool_name must be a string")
        tool_input = raw.get("tool_input", {})
        if not isinstance(tool_input, dict):
            raise ContractError("hook tool_input must be an object")
        decision = classify_tool_call(tool_name, tool_input)
        if decision.action in {ToolAction.INTERNAL, ToolAction.READ}:
            return 0
        if decision.action is ToolAction.DENY:
            return _emit("deny", decision.reason)
        lock_value = os.environ.get("WORKFLOW_LOCK")
        if lock_value and Path(lock_value).is_file():
            raise ContractError("workflow execution lock is active")
        root = _workspace_from_cwd(str(raw.get("cwd", os.getcwd())))
        result = authorize_external_write(
            GateRequest(
                root=root,
                manifest_path=Path(_required_env("WORKFLOW_MANIFEST")),
                run_log=Path(_required_env("WORKFLOW_RUN_LOG")),
                gate=_required_env("WORKFLOW_GATE"),
                run_id=_required_env("WORKFLOW_RUN_ID"),
                target_id=_required_env("WORKFLOW_TARGET_ID"),
                notion_connector=is_notion_connector(tool_name),
                notion_operation=decision.operation,
                notion_resource_id=notion_resource_id(decision.operation, tool_input),
                notion_page_id=os.environ.get("WORKFLOW_NOTION_PAGE_ID"),
                notion_verified_at=os.environ.get("WORKFLOW_NOTION_VERIFIED_AT"),
                blog_id=os.environ.get("WORKFLOW_BLOG_ID"),
            )
        )
        return _emit("allow", f"verified {result['verified_artifact_digest']}")
    except (ContractError, json.JSONDecodeError, OSError) as error:
        return _emit("deny", str(error))


__all__ = ["run_hook"]

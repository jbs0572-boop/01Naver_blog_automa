from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from tools.contract_types import JSONMap


class ToolAction(StrEnum):
    INTERNAL = "internal"
    READ = "read"
    WRITE = "write"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class ToolDecision:
    action: ToolAction
    reason: str
    external: bool


_READ_ACTIONS = frozenset(
    {
        "fetch",
        "search",
        "query",
        "read",
        "get",
        "list",
        "find",
        "inspect",
        "snapshot",
        "screenshot",
        "open",
        "navigate",
        "status",
        "retrieve",
        "read_page",
        "get_page",
        "search_pages",
        "list_databases",
        "query_database",
        "fetch_page",
    }
)
_WRITE_ACTIONS = frozenset(
    {
        "duplicate_page",
        "create_page",
        "update_page",
        "create_attachment",
        "move_page",
        "submit",
        "save",
        "save_draft",
        "publish",
        "create",
        "update",
        "upload",
        "delete",
        "archive",
        "write",
        "edit",
        "send",
        "fill",
        "fill_form",
        "evaluate",
        "click",
        "type",
        "key",
        "drag",
        "press",
        "select",
        "check",
        "uncheck",
        "attach_file",
    }
)
_EXTERNAL_PREFIXES = ("mcp__", "browser.", "chrome.", "computer.")


def _explicit_action(tool_input: JSONMap) -> str | None:
    for key in ("action", "operation", "method"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value.lower().replace("-", "_")
    return None


def _action_name(tool_name: str) -> str | None:
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__")
        return parts[-1] if len(parts) >= 3 and parts[-1] else None
    for prefix in ("browser.", "chrome.", "computer."):
        if tool_name.startswith(prefix):
            return tool_name.removeprefix(prefix)
    return None


def classify_tool_call(
    tool_name: str, tool_input: JSONMap | None = None
) -> ToolDecision:
    normalized_name = tool_name.strip().lower()
    if not normalized_name:
        return ToolDecision(ToolAction.DENY, "tool name is missing", True)
    if not normalized_name.startswith(_EXTERNAL_PREFIXES):
        return ToolDecision(
            ToolAction.INTERNAL, "local tool is outside external-write policy", False
        )
    action = _explicit_action(tool_input or {})
    if action is None:
        raw_action = _action_name(normalized_name)
        action = raw_action.replace("-", "_") if raw_action is not None else None
    if action in _READ_ACTIONS:
        return ToolDecision(ToolAction.READ, f"registered read action: {action}", True)
    if action in _WRITE_ACTIONS:
        return ToolDecision(
            ToolAction.WRITE, f"registered external write action: {action}", True
        )
    return ToolDecision(ToolAction.DENY, "external tool/action is not registered", True)


__all__ = ["ToolAction", "ToolDecision", "classify_tool_call"]

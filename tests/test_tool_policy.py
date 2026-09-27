from __future__ import annotations

import pytest

from tools.contract_types import JSONMap
from tools.tool_policy import ToolAction, classify_tool_call


def test_read_query_is_not_promoted_by_write_words() -> None:
    tool_input: JSONMap = {"query": "how to create and update a page"}

    decision = classify_tool_call("mcp__notion__search", tool_input)

    assert decision.action is ToolAction.READ


@pytest.mark.parametrize(
    ("tool_name", "expected"),
    [
        ("mcp__codex_apps__notion_fetch", ToolAction.READ),
        ("mcp__codex_apps__notion_notion_create_pages", ToolAction.WRITE),
        ("mcp__codex_apps__notion_notion_update_page", ToolAction.WRITE),
    ],
)
def test_installed_notion_connector_actions_are_registered(
    tool_name: str, expected: ToolAction
) -> None:
    decision = classify_tool_call(tool_name, {})

    assert decision.action is expected


def test_named_tool_action_cannot_be_overridden_by_input() -> None:
    tool_input: JSONMap = {"operation": "create_attachment"}

    decision = classify_tool_call(
        "mcp__codex_apps__notion_notion_create_pages", tool_input
    )

    assert decision.operation == "create_pages"


def test_registered_external_writes_are_gate_protected() -> None:
    assert (
        classify_tool_call("mcp__notion__duplicate_page", {}).action is ToolAction.WRITE
    )
    assert (
        classify_tool_call("mcp__notion__create_attachment", {}).action
        is ToolAction.WRITE
    )
    assert classify_tool_call("mcp__naver__submit", {}).action is ToolAction.WRITE
    assert classify_tool_call("browser.fill", {}).action is ToolAction.WRITE


def test_read_tools_are_allowed_without_gate() -> None:
    for tool_name in (
        "browser.snapshot",
        "browser.goto",
        "browser.extract",
        "browser.get_content",
        "browser.text",
        "browser.wait",
    ):
        assert classify_tool_call(tool_name, {}).action is ToolAction.READ
    assert classify_tool_call("mcp__notion__get_page", {}).action is ToolAction.READ


def test_unknown_external_tool_defaults_to_deny() -> None:
    decision = classify_tool_call("mcp__unknown__do_something", {})

    assert decision.action is ToolAction.DENY
    assert "registered" in decision.reason


def test_non_external_tool_is_internal() -> None:
    decision = classify_tool_call("local_file_read", {})

    assert decision.action is ToolAction.INTERNAL

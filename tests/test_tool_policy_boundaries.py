"""Explicitly exercise every classifier helper and its deny-by-default behavior."""

import pytest

from tools import tool_policy
from tools.tool_policy import ToolAction, classify_tool_call


@pytest.mark.parametrize("tool_name,expected", [
    ("mcp__Notion__read", "read"),
    ("mcp__Notion__", None),
    ("mcp__invalid", None),
    ("browser.get_page", "get_page"),
    ("chrome.click", "click"),
    ("computer.type", "type"),
    ("local_function", None),
])
def test_action_name_parses_only_registered_external_shapes(tool_name, expected):
    assert tool_policy._action_name(tool_name) == expected


@pytest.mark.parametrize("tool_input,expected", [
    ({"action": "READ-PAGE"}, "read_page"),
    ({"operation": "CREATE-PAGE"}, "create_page"),
    ({"method": "Save_Draft"}, "save_draft"),
    ({"action": "", "method": "READ"}, "read"),
    ({"action": 3, "method": "READ"}, "read"),
    ({"operation": "unknown", "method": "read"}, "unknown"),
    ({"action": None}, None),
])
def test_explicit_action_precedence_and_normalization(tool_input, expected):
    assert tool_policy._explicit_action(tool_input) == expected


@pytest.mark.parametrize("tool_name, action", [
    ("", ToolAction.DENY),
    ("   ", ToolAction.DENY),
    ("os.remove", ToolAction.INTERNAL),
    ("MCP__NOTION__READ", ToolAction.READ),
    ("mcp__notion__create-page", ToolAction.WRITE),
    ("browser.click", ToolAction.WRITE),
    ("chrome.search", ToolAction.READ),
    ("computer.unknown", ToolAction.DENY),
    ("mcp__notion__", ToolAction.DENY),
])
def test_classifier_enforces_known_actions_and_default_deny(tool_name, action):
    decision = classify_tool_call(tool_name)
    assert decision.action == action
    assert decision.external is (action != ToolAction.INTERNAL)
    assert decision.reason


def test_explicit_write_action_cannot_be_disguised_by_read_tool_name():
    decision = classify_tool_call("mcp__Notion__read", {"operation": "save-draft"})
    assert decision.action is ToolAction.WRITE


def test_unrecognized_explicit_action_is_not_fallback_to_read():
    assert classify_tool_call("mcp__Notion__read", {"action": "run-unknown-command"}).action is ToolAction.DENY


def test_optional_input_none_uses_tool_name():
    assert classify_tool_call("browser.open", None).action is ToolAction.READ

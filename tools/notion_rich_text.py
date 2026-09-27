from __future__ import annotations

from typing import Final

from tools.contract_types import ContractError, JSONMap, JSONValue

_ANNOTATION_FLAGS: Final = (
    "bold",
    "italic",
    "strikethrough",
    "underline",
    "code",
)
_ANNOTATION_COLOR: Final = "color"


def expected_rich_text(value: list[JSONValue]) -> list[JSONValue]:
    return _canonical_rich_text(value, remote=False)


def remote_rich_text(value: JSONValue | None) -> list[JSONValue]:
    if not isinstance(value, list):
        raise ContractError("Notion API rich_text must be an array")
    return _canonical_rich_text(value, remote=True)


def plain_fragments(value: JSONValue | None) -> str:
    if not isinstance(value, list):
        return ""
    result: list[str] = []
    for raw in value:
        if isinstance(raw, dict):
            fragment = raw.get("plain_text")
            if isinstance(fragment, str):
                result.append(fragment)
    return "".join(result)


def _canonical_rich_text(value: list[JSONValue], *, remote: bool) -> list[JSONValue]:
    result: list[JSONValue] = []
    for raw in value:
        item = _rich_item(raw, remote=remote)
        result.append(item)
    return result


def _rich_item(raw: JSONValue, *, remote: bool) -> JSONMap:
    if not isinstance(raw, dict):
        raise ContractError("Notion rich_text item must be an object")
    if raw.get("type") != "text":
        raise ContractError("Notion rich_text item must be text")
    text = _map(raw.get("text"), "rich_text text")
    content = text.get("content")
    if not isinstance(content, str):
        raise ContractError("Notion rich_text content is missing")
    if remote and raw.get("plain_text") != content:
        raise ContractError("Notion rich_text plain_text does not match content")
    url = _link_url(text.get("link"))
    if remote:
        href = raw.get("href")
        if href != url:
            raise ContractError("Notion rich_text href does not match link")
    return {
        "content": content,
        "url": url,
        "annotations": _annotations(raw.get("annotations"), remote=remote),
    }


def _map(value: JSONValue | None, label: str) -> JSONMap:
    if not isinstance(value, dict):
        raise ContractError(f"Notion API {label} must be an object")
    return value


def _link_url(value: JSONValue | None) -> str | None:
    if value is None:
        return None
    link = _map(value, "rich_text link")
    url = link.get("url")
    if not isinstance(url, str) or not url:
        raise ContractError("Notion rich_text link URL is invalid")
    return url


def _annotations(value: JSONValue | None, *, remote: bool) -> JSONMap:
    if value is None:
        if remote:
            raise ContractError("Notion API rich_text annotations must be an object")
        annotations: JSONMap = {}
    else:
        annotations = _map(value, "rich_text annotations")
    result: JSONMap = {}
    for flag in _ANNOTATION_FLAGS:
        enabled = annotations.get(flag)
        if enabled is None and not remote:
            enabled = False
        if not isinstance(enabled, bool):
            raise ContractError(f"Notion rich_text annotation is invalid: {flag}")
        result[flag] = enabled
    color = annotations.get(_ANNOTATION_COLOR)
    if color is None and not remote:
        color = "default"
    if not isinstance(color, str) or not color:
        raise ContractError("Notion rich_text annotation is invalid: color")
    result[_ANNOTATION_COLOR] = color
    return result

from __future__ import annotations

import re

from tools.contract_types import ContractError, JSONMap, JSONValue

_LINK_PATTERN = r"(?P<link>\[(?P<link_text>[^\]]+)\]\((?P<url>https?://[^\s)]+)\))"
_BOLD_PATTERN = r"(?P<bold>\*\*(?P<bold_text>[^*]+)\*\*)"
_CODE_PATTERN = r"(?P<code>`(?P<code_text>[^`]+)`)"
_INLINE = re.compile(f"{_LINK_PATTERN}|{_BOLD_PATTERN}|{_CODE_PATTERN}")
_MAX_TEXT_CONTENT = 2_000
_MAX_RICH_TEXT_ITEMS = 100


def rich_text(content: str) -> list[JSONValue]:
    parts: list[JSONValue] = []
    start = 0
    for matched in _INLINE.finditer(content):
        _append_text(parts, content[start : matched.start()])
        match matched.lastgroup:
            case "link":
                text = matched.group("link_text")
                url = matched.group("url")
                if text is None or url is None:
                    raise ContractError("invalid Markdown link")
                if len(url) > _MAX_TEXT_CONTENT:
                    raise ContractError("Markdown link URL exceeds Notion limit")
                _append_segment(parts, text, url=url)
            case "bold":
                text = _required_group(matched.group("bold_text"), "Markdown bold")
                _append_segment(parts, text, annotation="bold")
            case "code":
                text = _required_group(matched.group("code_text"), "Markdown code")
                _append_segment(parts, text, annotation="code")
            case _:
                raise ContractError("invalid Markdown inline segment")
        start = matched.end()
    _append_text(parts, content[start:])
    if len(parts) > _MAX_RICH_TEXT_ITEMS:
        raise ContractError("Notion rich text item count exceeds 100")
    return parts


def plain_text(content: str) -> str:
    parts: list[str] = []
    start = 0
    for matched in _INLINE.finditer(content):
        parts.append(content[start : matched.start()])
        match matched.lastgroup:
            case "link":
                value = matched.group("link_text")
            case "bold":
                value = matched.group("bold_text")
            case "code":
                value = matched.group("code_text")
            case _:
                raise ContractError("invalid Markdown inline segment")
        parts.append(_required_group(value, "Markdown inline segment"))
        start = matched.end()
    parts.append(content[start:])
    return "".join(parts)


def _required_group(value: str | None, name: str) -> str:
    if value is None:
        raise ContractError(f"invalid {name}")
    return value


def _append_text(parts: list[JSONValue], content: str) -> None:
    _append_segment(parts, content)


def _append_segment(
    parts: list[JSONValue],
    content: str,
    *,
    url: str | None = None,
    annotation: str | None = None,
) -> None:
    for start in range(0, len(content), _MAX_TEXT_CONTENT):
        text = _json_map(content=content[start : start + _MAX_TEXT_CONTENT])
        if url is not None:
            text["link"] = _json_map(url=url)
        value = _json_map(type="text", text=text)
        if annotation is not None:
            value["annotations"] = _json_map(**{annotation: True})
        parts.append(value)


def _json_map(**values: JSONValue) -> JSONMap:
    return values

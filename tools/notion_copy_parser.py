from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError
from tools.notion_content_models import (
    BlankBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    ParsedBlock,
    ParsedNotionCopy,
    TableBlock,
    TextBlock,
)
from tools.notion_copy_grammar import (
    close_tag,
    closed_tag,
    heading_level,
    looks_like_tag,
    open_tag,
    parse_image,
    table_row,
    table_title,
)

_COPY_SOURCE_PREFIX: Final = "# 네이버 복사 전용 원본: "


def parse_naver_copy(path: Path) -> ParsedNotionCopy:
    return parse_naver_copy_text(path.read_text(encoding="utf-8"))


def parse_naver_copy_text(source: str) -> ParsedNotionCopy:
    title: str | None = None
    blocks: list[ParsedBlock] = []
    state: tuple[str, str, list[str]] | None = None
    active_image_index: int | None = None
    prologue_open = False
    prologue_seen = False
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if state is not None:
            title, state, active_image_index = _read_state_line(
                state, line, raw_line, title, blocks, active_image_index
            )
            continue
        if not line:
            continue
        if prologue_open:
            if line != "## 복사 순서":
                raise ContractError("copy prologue must end with ## 복사 순서")
            prologue_open = False
            continue
        if (
            not prologue_seen
            and title is None
            and not blocks
            and _is_copy_source_heading(line)
        ):
            prologue_open = True
            prologue_seen = True
            continue
        title, state, active_image_index = _read_root_line(
            line, title, blocks, active_image_index
        )
    if state is not None:
        raise ContractError(f"unbalanced tag: {state[0]}")
    if title is None:
        raise ContractError("missing title")
    _validate_images(blocks)
    return ParsedNotionCopy(title, tuple(blocks))


def _is_copy_source_heading(line: str) -> bool:
    return line.startswith(_COPY_SOURCE_PREFIX)


def _read_state_line(
    state: tuple[str, str, list[str]],
    line: str,
    raw_line: str,
    title: str | None,
    blocks: list[ParsedBlock],
    active_image_index: int | None,
) -> tuple[str | None, tuple[str, str, list[str]] | None, int | None]:
    tag, attrs, lines = state
    closing = close_tag(line)
    if closing == tag:
        updated_title, updated_image_index = _close_state(
            tag, attrs, lines, title, blocks, active_image_index
        )
        return (updated_title, None, updated_image_index)
    match tag:
        case "LIST":
            item = closed_tag(line, "ITEM")
        case "TABLE":
            item = closed_tag(line, "ROW")
        case _:
            item = None
    if item is not None:
        lines.append(item[1])
        return (title, state, active_image_index)
    if tag in {"LIST", "TABLE"}:
        raise ContractError(f"{tag} contains invalid content: {line}")
    if looks_like_tag(line):
        raise ContractError(f"unknown or unbalanced tag inside {tag}: {line}")
    lines.append(raw_line)
    return (title, state, active_image_index)


def _read_root_line(
    line: str,
    title: str | None,
    blocks: list[ParsedBlock],
    active_image_index: int | None,
) -> tuple[str | None, tuple[str, str, list[str]] | None, int | None]:
    matched = closed_tag(line, "TITLE")
    if matched is not None:
        return (_set_title(title, matched[1]), None, None)
    closed = _any_closed_tag(line)
    if closed is not None:
        tag, attrs, content = closed
        return _apply_closed(tag, attrs, content, title, blocks, active_image_index)
    opened = open_tag(line)
    if opened is None:
        raise ContractError(f"unknown or unbalanced tag: {line}")
    tag, attrs = opened
    match tag:
        case "TITLE" | "TEXT" | "LIST" | "TABLE" | "ALT" | "CAPTION":
            if tag == "TITLE" and title is not None:
                raise ContractError("duplicate title")
            return (title, (tag, attrs, []), active_image_index)
        case "IMAGE":
            blocks.append(parse_image(attrs))
            return (title, None, len(blocks) - 1)
        case "BLANK":
            if attrs:
                raise ContractError("BLANK must not have attributes")
            blocks.append(BlankBlock())
            return (title, None, None)
        case "HEADING" | "ITEM" | "ROW":
            raise ContractError(f"unbalanced tag: {tag}")
        case _:
            raise ContractError(f"unknown tag: {tag}")


def _any_closed_tag(line: str) -> tuple[str, str, str] | None:
    for tag in ("TEXT", "HEADING", "ITEM", "ROW", "ALT", "CAPTION"):
        matched = closed_tag(line, tag)
        if matched is not None:
            return (tag, matched[0], matched[1])
    return None


def _apply_closed(
    tag: str,
    attrs: str,
    content: str,
    title: str | None,
    blocks: list[ParsedBlock],
    active_image_index: int | None,
) -> tuple[str | None, tuple[str, str, list[str]] | None, int | None]:
    match tag:
        case "TEXT":
            if attrs:
                raise ContractError("TEXT must not have attributes")
            blocks.append(TextBlock(content))
            return (title, None, None)
        case "HEADING":
            blocks.append(HeadingBlock(heading_level(attrs), content))
            return (title, None, None)
        case "ALT":
            return (title, None, _set_image_alt(blocks, active_image_index, content))
        case "CAPTION":
            return (
                title,
                None,
                _set_image_caption(blocks, active_image_index, content),
            )
        case "ITEM" | "ROW":
            raise ContractError(f"{tag} must be nested inside its container")
        case _:
            raise ContractError(f"unknown tag: {tag}")


def _close_state(
    tag: str,
    attrs: str,
    lines: list[str],
    title: str | None,
    blocks: list[ParsedBlock],
    active_image_index: int | None,
) -> tuple[str | None, int | None]:
    content = "\n".join(lines).strip()
    match tag:
        case "TITLE":
            return (_set_title(title, content), None)
        case "TEXT":
            if attrs:
                raise ContractError("TEXT must not have attributes")
            blocks.append(TextBlock(content))
            return (title, None)
        case "LIST":
            if attrs:
                raise ContractError("LIST must not have attributes")
            if not lines:
                raise ContractError("LIST must contain at least one ITEM")
            blocks.append(ListBlock(tuple(lines)))
            return (title, None)
        case "TABLE":
            if not lines:
                raise ContractError("TABLE must contain at least one ROW")
            blocks.append(
                TableBlock(table_title(attrs), tuple(table_row(row) for row in lines))
            )
            return (title, None)
        case "ALT":
            return (title, _set_image_alt(blocks, active_image_index, content))
        case "CAPTION":
            return (title, _set_image_caption(blocks, active_image_index, content))
        case _:
            raise ContractError(f"unknown tag: {tag}")


def _set_title(existing: str | None, content: str) -> str:
    if existing is not None:
        raise ContractError("duplicate title")
    if not content:
        raise ContractError("title is empty")
    return content


def _set_image_alt(
    blocks: list[ParsedBlock], active_index: int | None, content: str
) -> int:
    return _replace_image_text(blocks, active_index, content, "ALT")


def _set_image_caption(
    blocks: list[ParsedBlock], active_index: int | None, content: str
) -> int:
    return _replace_image_text(blocks, active_index, content, "CAPTION")


def _replace_image_text(
    blocks: list[ParsedBlock], active_index: int | None, content: str, tag: str
) -> int:
    if active_index is None:
        raise ContractError(f"{tag} must immediately follow IMAGE")
    block = blocks[active_index]
    if not isinstance(block, ImageBlock):
        raise ContractError(f"{tag} must immediately follow IMAGE")
    if tag == "ALT" and not content:
        raise ContractError("ALT is empty")
    match tag:
        case "ALT":
            blocks[active_index] = replace(block, alt=content)
        case "CAPTION":
            blocks[active_index] = replace(block, caption=content)
        case _:
            raise ContractError(f"unknown tag: {tag}")
    return active_index


def _validate_images(blocks: list[ParsedBlock]) -> None:
    images = [block for block in blocks if isinstance(block, ImageBlock)]
    if not images or not images[0].representative:
        raise ContractError("first image must be representative=true")
    if any(image.representative for image in images[1:]):
        raise ContractError("only the first image may be representative=true")

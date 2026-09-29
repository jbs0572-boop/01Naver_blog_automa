from __future__ import annotations

import re
import shlex
from pathlib import Path

from tools.contract_types import ContractError
from tools.notion_content_models import ImageBlock, TableCell

_CLOSED_TAG = re.compile(
    r"^\[(?P<tag>[A-Z]+)(?P<attrs>(?:\s+[^\]]*)?)\](?P<content>.*)\[/(?P=tag)\]$"
)
_OPEN_TAG = re.compile(r"^\[(?P<tag>[A-Z]+)(?P<attrs>(?:\s+[^\]]*)?)\]$")
_CLOSE_TAG = re.compile(r"^\[/(?P<tag>[A-Z]+)\]$")


def closed_tag(line: str, expected: str) -> tuple[str, str] | None:
    matched = _CLOSED_TAG.fullmatch(line)
    if matched is None or matched.group("tag") != expected:
        return None
    return (matched.group("attrs").strip(), matched.group("content").strip())


def open_tag(line: str) -> tuple[str, str] | None:
    matched = _OPEN_TAG.fullmatch(line)
    if matched is None:
        return None
    return (matched.group("tag"), matched.group("attrs").strip())


def close_tag(line: str) -> str | None:
    matched = _CLOSE_TAG.fullmatch(line)
    if matched is None:
        return None
    return matched.group("tag")


def looks_like_tag(line: str) -> bool:
    return line.startswith("[") and line.endswith("]")


def parse_image(attrs: str) -> ImageBlock:
    values = attributes(attrs)
    _ = values.pop("evidence", None)
    required = only_required(values, {"file", "alt", "representative"})
    filename = required["file"]
    if (
        not filename
        or Path(filename).name != filename
        or "/" in filename
        or "\\" in filename
    ):
        raise ContractError("image file must not contain a path component")
    match required["representative"]:
        case "true":
            return ImageBlock(filename, required["alt"], True, "")
        case "false":
            return ImageBlock(filename, required["alt"], False, "")
        case _:
            raise ContractError("IMAGE representative must be true or false")


def heading_level(attrs: str) -> int:
    level = only_required(attributes(attrs), {"level"})["level"]
    if level not in {"2", "3", "4"}:
        raise ContractError("HEADING level must be 2, 3, or 4")
    return int(level)


def table_title(attrs: str) -> str:
    return only_required(attributes(attrs), {"title"})["title"]


def table_row(raw: str) -> tuple[TableCell, ...]:
    cells: list[TableCell] = []
    for cell in raw.split(";"):
        key, separator, value = cell.strip().partition("=")
        if not separator or not key.strip() or not value.strip():
            raise ContractError("TABLE ROW must use non-empty key=value cells")
        cells.append(TableCell(key.strip(), value.strip()))
    return tuple(cells)


def attributes(raw: str) -> dict[str, str]:
    try:
        tokens = shlex.split(raw)
    except ValueError as error:
        raise ContractError("invalid tag attributes") from error
    result: dict[str, str] = {}
    for token in tokens:
        key, separator, value = token.partition("=")
        if not separator or not key or not value or key in result:
            raise ContractError("invalid tag attributes")
        result[key] = value
    return result


def only_required(values: dict[str, str], required: set[str]) -> dict[str, str]:
    if set(values) != required:
        raise ContractError("invalid tag attributes")
    return values

from __future__ import annotations

import json
from pathlib import Path

from tools.contract_types import ContractError
from tools.notion_copy_grammar import (
    attributes,
    closed_tag,
    only_required,
    open_tag,
    table_row,
)


def normalize_naver_copy_file(path: Path, asset_dir: Path) -> None:
    source = path.read_text(encoding="utf-8")
    normalized = normalize_naver_copy_text(source, asset_dir)
    if normalized != source:
        _ = path.write_text(normalized, encoding="utf-8")


def normalize_naver_copy_text(source: str, asset_dir: Path) -> str:
    lines = source.splitlines()
    normalized: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        opened = open_tag(line)
        if opened is None:
            normalized.append(lines[index])
            index += 1
            continue
        tag, attrs = opened
        match tag:
            case "IMAGE":
                normalized.append(_normalize_image(attrs, asset_dir))
                index += 1
            case "LIST":
                normalized.append(_normalize_list(attrs))
                index += 1
            case "TABLE":
                table, index = _normalize_table(lines, index, attrs)
                normalized.extend(table)
            case _:
                normalized.append(lines[index])
                index += 1
    suffix = "\n" if source.endswith("\n") else ""
    return "\n".join(normalized) + suffix


def _normalize_image(attrs: str, asset_dir: Path) -> str:
    values = attributes(attrs)
    _ = values.pop("evidence", None)
    required = only_required(values, {"file", "alt", "representative"})
    filename = Path(required["file"]).name
    if not filename or filename in {".", ".."} or not (asset_dir / filename).is_file():
        raise ContractError("IMAGE file does not resolve to a declared asset")
    representative = required["representative"]
    if representative not in {"true", "false"}:
        raise ContractError("IMAGE representative must be true or false")
    return (
        f"[IMAGE file={_quoted(filename)} alt={_quoted(required['alt'])} "
        f"representative={representative}]"
    )


def _normalize_list(attrs: str) -> str:
    if not attrs:
        return "[LIST]"
    values = attributes(attrs)
    allowed = values in (
        {"ordered": "true"},
        {"ordered": "false"},
        {"type": "ordered"},
        {"type": "unordered"},
    )
    if not allowed:
        raise ContractError("invalid tag attributes")
    return "[LIST]"


def _normalize_table(
    lines: list[str], start: int, attrs: str
) -> tuple[list[str], int]:
    rows: list[str] = []
    index = start + 1
    while index < len(lines) and lines[index].strip() != "[/TABLE]":
        matched = closed_tag(lines[index].strip(), "ROW")
        if matched is None or matched[0]:
            raise ContractError("TABLE contains invalid ROW content")
        rows.append(matched[1])
        index += 1
    if index >= len(lines) or not rows:
        raise ContractError("TABLE must contain rows and a closing tag")
    if any("[ITEM]" in row or "[/ITEM]" in row for row in rows):
        raise ContractError("TABLE ROW cannot contain nested ITEM tags")
    if attrs:
        title = only_required(attributes(attrs), {"title"})["title"]
    else:
        title = ""
    if title and all(_is_canonical_row(row) for row in rows):
        return ([lines[start], *lines[start + 1 : index + 1]], index + 1)
    headers = _pipe_cells(rows[0])
    if not title:
        title = " / ".join(headers)
    converted = [f"[TABLE title={_quoted(title)}]"]
    for row in rows[1:]:
        values = _pipe_cells(row)
        if len(values) != len(headers):
            raise ContractError("TABLE pipe rows must have equal width")
        if any("=" in value or ";" in value for value in (*headers, *values)):
            raise ContractError("TABLE pipe cells contain reserved delimiters")
        cells = "; ".join(
            f"{header}={value}" for header, value in zip(headers, values, strict=True)
        )
        converted.append(f"[ROW]{cells}[/ROW]")
    if len(converted) == 1:
        raise ContractError("TABLE pipe form must contain a data row")
    converted.append("[/TABLE]")
    return (converted, index + 1)


def _pipe_cells(row: str) -> tuple[str, ...]:
    cells = tuple(cell.strip() for cell in row.split("|"))
    if len(cells) < 2:
        raise ContractError("TABLE row must use key=value cells or pipe-separated cells")
    if any(not cell for cell in cells):
        raise ContractError("TABLE pipe row contains an empty cell")
    return cells


def _is_canonical_row(row: str) -> bool:
    try:
        _ = table_row(row)
    except ContractError:
        return False
    return True


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


__all__ = ["normalize_naver_copy_file", "normalize_naver_copy_text"]

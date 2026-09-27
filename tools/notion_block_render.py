from __future__ import annotations

from collections.abc import Mapping

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.notion_content_models import (
    AssetMetadata,
    BlankBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    ParsedNotionCopy,
    TableBlock,
    TableCell,
    TextBlock,
)
from tools.notion_inline_markup import plain_text, rich_text
from tools.notion_rich_text import expected_rich_text


def notion_children(
    parsed: ParsedNotionCopy, upload_ids_by_filename: Mapping[str, str]
) -> list[JSONMap]:
    children: list[JSONMap] = []
    for block in parsed.blocks:
        match block:
            case TextBlock(content=content):
                children.append(_rich_child("paragraph", content))
            case HeadingBlock(level=level, content=content):
                children.append(_rich_child(f"heading_{level}", content))
            case ListBlock(items=items):
                children.extend(_rich_child("bulleted_list_item", item) for item in items)
            case TableBlock(title=title, rows=rows):
                children.extend(_table_children(title, rows))
            case ImageBlock(filename=filename, alt=alt, caption=caption):
                children.append(
                    _image_child(filename, alt, caption, upload_ids_by_filename)
                )
            case BlankBlock():
                children.append(_rich_child("paragraph", ""))
    return children


def expected_blocks(
    parsed: ParsedNotionCopy, asset_metadata_by_filename: Mapping[str, AssetMetadata]
) -> JSONMap:
    blocks: list[JSONValue] = []
    for block in parsed.blocks:
        match block:
            case TextBlock(content=content):
                blocks.append(_expected_text_block("paragraph", content))
            case HeadingBlock(level=level, content=content):
                blocks.append(
                    _expected_text_block("heading", content, heading_level=level)
                )
            case ListBlock(items=items):
                blocks.extend(
                    _expected_text_block("list_item", item, list_type="bulleted")
                    for item in items
                )
            case TableBlock(title=title, rows=rows):
                blocks.extend(_expected_table_blocks(title, rows))
            case ImageBlock(
                filename=filename,
                alt=alt,
                representative=representative,
                caption=caption,
            ):
                blocks.append(
                    _expected_image(
                        filename,
                        alt,
                        representative,
                        caption,
                        asset_metadata_by_filename,
                    )
                )
            case BlankBlock():
                blocks.append(_expected_text_block("paragraph", ""))
    return _json_map(title=parsed.title, properties=_json_map(), blocks=_json_list(*blocks))


def _table_children(
    title: str, rows: tuple[tuple[TableCell, ...], ...]
) -> list[JSONMap]:
    width = max(len(row) for row in rows)
    if any(len(row) != width for row in rows):
        raise ContractError("Notion table rows must have equal width")
    table_rows: list[JSONValue] = []
    for row in rows:
        cells = [_json_list(*rich_text(cell.content)) for cell in row]
        table_rows.append(
            _json_map(object="block", type="table_row", table_row=_json_map(cells=_json_list(*cells)))
        )
    return [
        _rich_child("paragraph", title),
        _json_map(
            object="block",
            type="table",
            table=_json_map(
                table_width=width,
                has_column_header=False,
                has_row_header=False,
                children=_json_list(*table_rows),
            ),
        ),
    ]


def _image_child(
    filename: str,
    alt: str,
    caption: str,
    upload_ids_by_filename: Mapping[str, str],
) -> JSONMap:
    upload_id = upload_ids_by_filename.get(filename)
    if not isinstance(upload_id, str) or not upload_id:
        raise ContractError(f"missing Notion upload ID for image: {filename}")
    return _json_map(
        object="block",
        type="image",
        image=_json_map(
            type="file_upload",
            file_upload=_json_map(id=upload_id),
            caption=_json_list(*rich_text(_image_caption(alt, caption))),
        ),
    )


def _expected_table_blocks(
    title: str, rows: tuple[tuple[TableCell, ...], ...]
) -> list[JSONValue]:
    blocks: list[JSONValue] = [
        _expected_text_block("paragraph", title),
        _json_map(type="table"),
    ]
    blocks.extend(
            _json_map(
                type="table_row",
                table_cells=_json_list(*(plain_text(cell.content) for cell in row)),
                table_rich_text=_json_list(
                    *(expected_rich_text(rich_text(cell.content)) for cell in row)
                ),
            )
        for row in rows
    )
    return blocks


def _expected_image(
    filename: str,
    alt: str,
    representative: bool,
    caption: str,
    asset_metadata_by_filename: Mapping[str, AssetMetadata],
) -> JSONMap:
    metadata = asset_metadata_by_filename.get(filename)
    if metadata is None:
        raise ContractError(f"missing asset metadata for image: {filename}")
    required_role = "thumbnail" if representative else "body_image"
    if metadata.artifact_role != required_role:
        raise ContractError(f"image role does not match metadata: {filename}")
    return _json_map(
        type="image",
        image=_json_map(
            artifact_role=metadata.artifact_role,
            original_sha256=metadata.original_sha256,
            caption=plain_text(_image_caption(alt, caption)),
            caption_rich_text=expected_rich_text(
                rich_text(_image_caption(alt, caption))
            ),
            order=metadata.order,
        ),
    )


def _image_caption(alt: str, caption: str) -> str:
    return f"[ALT] {alt} [/ALT]\n[CAPTION] {caption} [/CAPTION]"


def _expected_text_block(
    kind: str,
    content: str,
    *,
    heading_level: int | None = None,
    list_type: str | None = None,
) -> JSONMap:
    result = _json_map(
        type=kind,
        plain_text=plain_text(content),
        rich_text=expected_rich_text(rich_text(content)),
    )
    if heading_level is not None:
        result["heading_level"] = heading_level
    if list_type is not None:
        result["list_type"] = list_type
    return result


def _rich_child(kind: str, content: str) -> JSONMap:
    return _json_map(
        object="block", type=kind, **{kind: _json_map(rich_text=_json_list(*rich_text(content)))}
    )


def _json_map(**values: JSONValue) -> JSONMap:
    return values


def _json_list(*values: JSONValue) -> list[JSONValue]:
    return list(values)

from __future__ import annotations

from pathlib import Path

import pytest

from tools.contract_types import ContractError
from tools.notion_content import (
    AssetMetadata,
    expected_blocks,
    notion_children,
    parse_naver_copy,
)
from tools.notion_inline_markup import rich_text


def _write_copy(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "topic-naver-copy.md"
    _ = path.write_text(content, encoding="utf-8")
    return path


def test_rich_text_splits_long_segments_without_losing_formatting() -> None:
    plain = "가" * 2_001
    bold = "나" * 2_001

    parts = rich_text(f"{plain}**{bold}**")

    assert parts == [
        {"type": "text", "text": {"content": "가" * 2_000}},
        {"type": "text", "text": {"content": "가"}},
        {
            "type": "text",
            "text": {"content": "나" * 2_000},
            "annotations": {"bold": True},
        },
        {
            "type": "text",
            "text": {"content": "나"},
            "annotations": {"bold": True},
        },
    ]


def test_rich_text_rejects_array_and_url_limits_before_api_write() -> None:
    with pytest.raises(ContractError, match="item count exceeds 100"):
        _ = rich_text("`x`" * 101)
    with pytest.raises(ContractError, match="link URL exceeds Notion limit"):
        _ = rich_text(f"[link](https://example.test/{'x' * 2_000})")


def test_parse_and_render_canonical_copy_when_tags_are_valid(tmp_path: Path) -> None:
    # Given: a canonical copy with every supported block and an inline quote line.
    path = _write_copy(
        tmp_path,
        """[TITLE]
정확한 **제목**
[/TITLE]
[TEXT]
첫 문장과 [공식 링크](https://example.test) 그리고 `코드`.
> 인용문도 원문 순서대로 보존합니다.
[/TEXT]
[IMAGE file="thumbnail.png" alt="초기 대체 텍스트" representative=true]
[ALT] 전용 썸네일 [/ALT]
[CAPTION] 출처: 공식 [/CAPTION]
[HEADING level=2] **소제목** [/HEADING]
[LIST]
[ITEM] 첫 항목 [/ITEM]
[ITEM] **둘째** 항목 [/ITEM]
[/LIST]
[TABLE title="일정"]
[ROW] 날짜=2026-09-01; 상태=확정 [/ROW]
[/TABLE]
[BLANK]
[IMAGE file="body.png" alt="본문 이미지" representative=false]
""",
    )

    # When: the copy becomes a typed page model and Notion child JSON.
    parsed = parse_naver_copy(path)
    children = notion_children(
        parsed, {"thumbnail.png": "upload-thumbnail", "body.png": "upload-body"}
    )
    expected = expected_blocks(
        parsed,
        {
            "thumbnail.png": AssetMetadata("thumbnail", "sha-thumb", 9),
            "body.png": AssetMetadata("body_image", "sha-body", 10),
        },
    )

    # Then: source order, rich text, upload references, and Q2 shape are preserved.
    assert parsed.title == "정확한 **제목**"
    assert [child["type"] for child in children] == [
        "paragraph",
        "image",
        "heading_2",
        "bulleted_list_item",
        "bulleted_list_item",
        "paragraph",
        "table",
        "paragraph",
        "image",
    ]
    assert children[0] == {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {
                    "type": "text",
                    "text": {"content": "첫 문장과 "},
                },
                {
                    "type": "text",
                    "text": {
                        "content": "공식 링크",
                        "link": {"url": "https://example.test"},
                    },
                },
                {
                    "type": "text",
                    "text": {"content": " 그리고 "},
                },
                {
                    "type": "text",
                    "text": {"content": "코드"},
                    "annotations": {"code": True},
                },
                {
                    "type": "text",
                    "text": {"content": ".\n> 인용문도 원문 순서대로 보존합니다."},
                },
            ]
        },
    }
    assert children[1] == {
        "object": "block",
        "type": "image",
        "image": {
            "type": "file_upload",
            "file_upload": {"id": "upload-thumbnail"},
            "caption": [
                {
                    "type": "text",
                    "text": {
                        "content": "[ALT] 전용 썸네일 [/ALT]\n[CAPTION] 출처: 공식 [/CAPTION]"
                    },
                }
            ],
        },
    }
    assert children[5] == {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {
                    "type": "text",
                    "text": {"content": "일정"},
                }
            ]
        },
    }
    assert children[6] == {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": 2,
            "has_column_header": False,
            "has_row_header": False,
            "children": [
                {
                    "object": "block",
                    "type": "table_row",
                    "table_row": {
                        "cells": [
                            [
                                {
                                    "type": "text",
                                    "text": {"content": "날짜: 2026-09-01"},
                                }
                            ],
                            [
                                {
                                    "type": "text",
                                    "text": {"content": "상태: 확정"},
                                }
                            ],
                        ]
                    },
                }
            ],
        },
    }
    blocks = expected["blocks"]
    assert expected["title"] == "정확한 **제목**"
    assert expected["properties"] == {}
    assert isinstance(blocks, list)
    assert [block["type"] for block in blocks if isinstance(block, dict)] == [
        "paragraph",
        "image",
        "heading",
        "list_item",
        "list_item",
        "paragraph",
        "table",
        "table_row",
        "paragraph",
        "image",
    ]
    first = blocks[0]
    assert isinstance(first, dict)
    assert first["plain_text"] == "첫 문장과 공식 링크 그리고 코드.\n> 인용문도 원문 순서대로 보존합니다."
    first_rich = first["rich_text"]
    assert isinstance(first_rich, list)
    link = first_rich[1]
    code = first_rich[3]
    assert isinstance(link, dict) and link["url"] == "https://example.test"
    assert isinstance(code, dict)
    annotations = code["annotations"]
    assert isinstance(annotations, dict) and annotations["code"] is True
    table_row = blocks[7]
    assert isinstance(table_row, dict)
    assert table_row["table_cells"] == ["날짜: 2026-09-01", "상태: 확정"]
    image_expected = blocks[1]
    assert isinstance(image_expected, dict)
    image_data = image_expected["image"]
    assert isinstance(image_data, dict)
    assert image_data["caption"] == "[ALT] 전용 썸네일 [/ALT]\n[CAPTION] 출처: 공식 [/CAPTION]"
    caption_rich = image_data["caption_rich_text"]
    assert isinstance(caption_rich, list)
    caption = caption_rich[0]
    assert isinstance(caption, dict)
    content = caption["content"]
    assert isinstance(content, str) and content.startswith("[ALT]")
    assert children[-1] == {
        "object": "block",
        "type": "image",
        "image": {
            "type": "file_upload",
            "file_upload": {"id": "upload-body"},
            "caption": [
                {
                    "type": "text",
                    "text": {
                        "content": "[ALT] 본문 이미지 [/ALT]\n[CAPTION]  [/CAPTION]"
                    },
                }
            ],
        },
    }


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[TITLE] 제목 [/TITLE]\n[UNKNOWN]", "unknown tag"),
        ("# 임의 Markdown\n[TITLE] 제목 [/TITLE]", "unknown or unbalanced tag"),
        (
            "# 네이버 복사 전용 원본: 제목\n## 임의 순서\n[TITLE] 제목 [/TITLE]",
            "copy prologue must end with ## 복사 순서",
        ),
        (
            "# 네이버 복사 전용 원본: 제목\n## 복사 순서\n# 네이버 복사 전용 원본: 중복\n[TITLE] 제목 [/TITLE]",
            "unknown or unbalanced tag",
        ),
        ("[TITLE] 하나 [/TITLE]\n[TITLE] 둘 [/TITLE]", "duplicate title"),
        (
            '[TITLE] 제목 [/TITLE]\n[IMAGE file="dir/body.png" alt="x" representative=true]',
            "path component",
        ),
        (
            '[TITLE] 제목 [/TITLE]\n[IMAGE file="thumbnail.png" alt="x" representative=false]',
            "first image",
        ),
    ],
)
def test_parse_rejects_contract_violation_when_input_is_invalid(
    tmp_path: Path, content: str, message: str
) -> None:
    # Given: malformed canonical content.
    path = _write_copy(tmp_path, content)

    # When / Then: parsing fails closed at the tagged boundary.
    with pytest.raises(ContractError, match=message):
        _ = parse_naver_copy(path)


def test_render_rejects_missing_image_upload_when_attachment_is_absent(
    tmp_path: Path,
) -> None:
    # Given: a parsed thumbnail whose upload was not reconciled.
    parsed = parse_naver_copy(
        _write_copy(
            tmp_path,
            '[TITLE] 제목 [/TITLE]\n[IMAGE file="thumbnail.png" alt="x" representative=true]',
        )
    )

    # When / Then: request construction refuses a broken image reference.
    with pytest.raises(ContractError, match="missing Notion upload ID"):
        _ = notion_children(parsed, {})


def test_parse_rejects_multiline_alt_when_no_image_precedes_it(tmp_path: Path) -> None:
    # Given: ALT uses the block form before a canonical image exists.
    path = _write_copy(
        tmp_path,
        "[TITLE] 제목 [/TITLE]\n[ALT]\n대체 텍스트\n[/ALT]",
    )

    # When / Then: it fails as a tagged-boundary contract error, never IndexError.
    with pytest.raises(ContractError, match="ALT must immediately follow IMAGE"):
        _ = parse_naver_copy(path)


def test_parse_preserves_content_when_documented_copy_prologue_precedes_title(
    tmp_path: Path,
) -> None:
    # Given: the documented copy-only prologue before canonical tagged content.
    path = _write_copy(
        tmp_path,
        """# 네이버 복사 전용 원본: 회귀 테스트

## 복사 순서

[TITLE] 제목 [/TITLE]
[IMAGE file="thumbnail.png" alt="대표 이미지" representative=true]
[TEXT] 본문 [/TEXT]
""",
    )

    # When: the copy is parsed through the public facade.
    parsed = parse_naver_copy(path)

    # Then: prologue text adds no content and the tagged order is unchanged.
    assert parsed.title == "제목"
    assert [type(block).__name__ for block in parsed.blocks] == [
        "ImageBlock",
        "TextBlock",
    ]

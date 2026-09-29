from __future__ import annotations

from pathlib import Path

import pytest

from tools.contract_types import ContractError, JSONMap
from tools.external_adapter import verify_notion_round_trip
from tools.notion_api_payload import normalized_block
from tools.notion_content import AssetMetadata, expected_blocks, parse_naver_copy


def _copy(tmp_path: Path) -> Path:
    path = tmp_path / "topic-naver-copy.md"
    _ = path.write_text(
        "[TITLE] 제목 [/TITLE]\n[TEXT] 첫 [공식](https://example.test/source) "
        + "**강조** [/TEXT]\n[IMAGE file=\"thumbnail.png\" alt=\"대체 텍스트\" "
        + "representative=true]\n[CAPTION] 출처: 공식 [/CAPTION]",
        encoding="utf-8",
    )
    return path


def _remote_text(
    content: str,
    *,
    href: str | None = None,
    bold: bool = False,
) -> JSONMap:
    return {
        "type": "text",
        "text": {
            "content": content,
            "link": None if href is None else {"url": href},
        },
        "plain_text": content,
        "href": href,
        "annotations": {
            "bold": bold,
            "italic": False,
            "strikethrough": False,
            "underline": False,
            "code": False,
            "color": "default",
        },
    }


def _remote_page(
    *,
    href: str = "https://example.test/source",
    bold: bool = True,
    caption_bold: bool = False,
) -> tuple[JSONMap, AssetMetadata]:
    metadata = AssetMetadata("thumbnail", "a" * 64, 1)
    paragraph: JSONMap = {
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                _remote_text("첫 "),
                _remote_text("공식", href=href),
                _remote_text(" "),
                _remote_text("강조", bold=bold),
            ]
        },
    }
    image: JSONMap = {
        "type": "image",
        "image": {
            "caption": [
                _remote_text(
                    "[ALT] 대체 텍스트 [/ALT]\n[CAPTION] 출처: 공식 [/CAPTION]",
                    bold=caption_bold,
                )
            ]
        },
    }
    return {
        "title": "제목",
        "properties": {},
        "blocks": [normalized_block(paragraph, None), normalized_block(image, metadata)],
    }, metadata


def _expected_page(tmp_path: Path, metadata: AssetMetadata) -> JSONMap:
    expected = expected_blocks(
        parse_naver_copy(_copy(tmp_path)), {"thumbnail.png": metadata}
    )
    expected["properties"] = {}
    return expected


def test_q2_rich_text_passes_when_link_annotations_and_caption_match(
    tmp_path: Path,
) -> None:
    # Given: source rich text and the equivalent Notion API response.
    actual, metadata = _remote_page()
    expected = _expected_page(tmp_path, metadata)

    # When: Q2 compares its canonical content digest.
    result = verify_notion_round_trip(
        expected, actual, "page", "2026-09-01T00:00:00+09:00", "digest"
    )

    # Then: semantic rich text and the image ALT/caption round-trip pass.
    assert result["storage_integrity"] == "passed"


def test_q2_rich_text_rejects_changed_link_url(tmp_path: Path) -> None:
    # Given: the rendered text has a different target URL.
    actual, metadata = _remote_page(href="https://example.test/changed")

    # When / Then: Q2 rejects the changed hyperlink.
    with pytest.raises(ContractError, match="content digest"):
        _ = verify_notion_round_trip(
            _expected_page(tmp_path, metadata),
            actual,
            "page",
            "2026-09-01T00:00:00+09:00",
            "digest",
        )


def test_q2_rich_text_rejects_changed_annotation(tmp_path: Path) -> None:
    # Given: the rendered bold annotation was removed.
    actual, metadata = _remote_page(bold=False)

    # When / Then: Q2 rejects the changed annotation.
    with pytest.raises(ContractError, match="content digest"):
        _ = verify_notion_round_trip(
            _expected_page(tmp_path, metadata),
            actual,
            "page",
            "2026-09-01T00:00:00+09:00",
            "digest",
        )


def test_q2_caption_rejects_changed_annotation(tmp_path: Path) -> None:
    # Given: an image caption annotation changed while its plain text stayed the same.
    actual, metadata = _remote_page(caption_bold=True)

    # When / Then: Q2 rejects the altered caption semantics.
    with pytest.raises(ContractError, match="content digest"):
        _ = verify_notion_round_trip(
            _expected_page(tmp_path, metadata),
            actual,
            "page",
            "2026-09-01T00:00:00+09:00",
            "digest",
        )


def test_remote_rich_text_rejects_missing_href_for_a_link() -> None:
    # Given: a remote Notion text item whose nested link disagrees with href.
    block: JSONMap = {
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {
                    **_remote_text("공식", href="https://example.test/source"),
                    "href": None,
                }
            ]
        },
    }

    # When / Then: the remote boundary fails closed.
    with pytest.raises(ContractError, match="href"):
        _ = normalized_block(block, None)


def test_remote_rich_text_rejects_malformed_item() -> None:
    # Given: malformed remote rich text is not a text-item array.
    block: JSONMap = {"type": "paragraph", "paragraph": {"rich_text": "wrong"}}

    # When / Then: normalization fails before Q2 can compare an unsafe value.
    with pytest.raises(ContractError, match="rich_text must be an array"):
        _ = normalized_block(block, None)

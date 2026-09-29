from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest

from tests._notion_api_test_support import (
    client as _client,
)
from tests._notion_api_test_support import (
    schema_properties as _schema_properties,
)
from tools.contract_types import ContractError, JSONMap
from tools.notion_api import NotionApiTransport
from tools.notion_api_payload import canonical_block
from tools.notion_content import AssetMetadata, parse_naver_copy


def test_schema_validation_fails_closed_on_required_type_mismatch() -> None:
    properties = _schema_properties()
    properties["검수 완료일"] = {"type": "rich_text"}
    with (
        _client(
            httpx2.MockTransport(
                lambda _request: httpx2.Response(
                    200, json={"id": "ds", "properties": properties}
                )
            )
        ) as client,
        pytest.raises(ContractError, match="검수 완료일"),
    ):
        NotionApiTransport(client).validate_schema("ds", timeout_seconds=2)


def test_prefix_comparison_ignores_response_default_rich_text_fields() -> None:
    request: JSONMap = {
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": "same"}}]},
    }
    response: JSONMap = {
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {
                    "type": "text",
                    "plain_text": "same",
                    "href": None,
                    "annotations": {
                        "bold": False,
                        "italic": False,
                        "strikethrough": False,
                    },
                    "text": {"content": "same", "link": None},
                }
            ]
        },
    }
    assert canonical_block(request) == canonical_block(response)


def test_reused_upload_identity_is_rendered_into_created_page(tmp_path: Path) -> None:
    copy = tmp_path / "copy.md"
    _ = copy.write_text(
        '[TITLE] 제목 [/TITLE]\n[IMAGE file="thumbnail.png" alt="대표" representative=true]',
        encoding="utf-8",
    )
    parsed = parse_naver_copy(copy)
    posted: JSONMap = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        posted.update(json.loads(request.content))
        return httpx2.Response(200, json={"id": "page"})

    with _client(httpx2.MockTransport(handler)) as client:
        _ = NotionApiTransport(
            client, parsed=parsed, image_filenames=("thumbnail.png",)
        ).create_page("ds", "run", "digest", ("reused-upload",), timeout_seconds=2)
    rendered = posted["children"]
    assert isinstance(rendered, list)
    image = rendered[0]
    assert isinstance(image, dict)
    image_payload = image.get("image")
    assert isinstance(image_payload, dict)
    upload = image_payload.get("file_upload")
    assert isinstance(upload, dict)
    assert upload.get("id") == "reused-upload"


def test_fetch_page_rejects_wrong_signed_image_bytes() -> None:
    digest = "0" * 64

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/pages/page":
            return httpx2.Response(200, json={"properties": {"제목": {"title": []}}})
        if request.url.path == "/v1/blocks/page/children":
            return httpx2.Response(
                200,
                json={
                    "results": [
                        {
                            "type": "image",
                            "image": {
                                "type": "file",
                                "file": {"url": "https://files.example/signed"},
                                "caption": [],
                            },
                        }
                    ],
                    "has_more": False,
                },
            )
        return httpx2.Response(200, content=b"wrong")

    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="hash"),
    ):
        _ = NotionApiTransport(
            client,
            asset_metadata=(AssetMetadata("thumbnail", digest, 1),),
            image_filenames=("thumbnail.png",),
        ).fetch_page("page", timeout_seconds=2)

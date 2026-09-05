from __future__ import annotations

import hashlib

import httpx2

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.notion_api_http import NotionHttp, array, as_map, text
from tools.notion_api_payload import (
    actual_properties,
    normalized_block,
    plain_fragments,
)
from tools.notion_content import AssetMetadata


def children(http: NotionHttp, block_id: str, timeout: float) -> tuple[JSONMap, ...]:
    blocks: list[JSONMap] = []
    cursor: str | None = None
    while True:
        params = {"page_size": "100"}
        if cursor:
            params["start_cursor"] = cursor
        page = http.read(
            "GET", f"/v1/blocks/{block_id}/children", timeout, params=params
        )
        for raw in array(page, "results"):
            block = as_map(raw, "block")
            if block.get("type") == "table" and block.get("has_children") is True:
                as_map(block.get("table"), "table")["children"] = list(
                    children(http, text(block, "id"), timeout)
                )
            blocks.append(block)
        if page.get("has_more") is not True:
            return tuple(blocks)
        cursor = text(page, "next_cursor")


def fetch_page(
    client: httpx2.Client,
    http: NotionHttp,
    page_id: str,
    timeout: float,
    expected_properties: JSONMap,
    metadata: tuple[AssetMetadata, ...],
    expected_target_id: str | None,
) -> JSONMap:
    page = http.read("GET", f"/v1/pages/{page_id}", timeout)
    if expected_target_id is not None:
        verify_page_parent(page, expected_target_id)
    blocks: list[JSONValue] = []
    image_index = 0
    for block in children(http, page_id, timeout):
        asset = None
        if block.get("type") == "image":
            if image_index >= len(metadata):
                raise ContractError("Notion image count exceeds manifest")
            asset = metadata[image_index]
            verify_image(client, block, asset, timeout)
            image_index += 1
        blocks.append(normalized_block(block, asset))
        nested = (
            as_map(block.get("table"), "table").get("children")
            if block.get("type") == "table"
            else None
        )
        if isinstance(nested, list):
            blocks.extend(normalized_block(as_map(row, "row"), None) for row in nested)
    if image_index != len(metadata):
        raise ContractError("Notion image count does not match manifest")
    props = as_map(page.get("properties"), "properties")
    return {
        "title": plain_fragments(as_map(props.get("제목"), "제목").get("title")),
        "properties": actual_properties(props, expected_properties),
        "blocks": blocks,
    }


def verify_page_parent(page: JSONMap, expected_target_id: str) -> None:
    parent = page.get("parent")
    if not isinstance(parent, dict):
        raise ContractError("Notion page parent data source is missing")
    if parent.get("type") != "data_source_id":
        raise ContractError("Notion page parent type is not data_source_id")
    actual_target_id = parent.get("data_source_id")
    if not isinstance(actual_target_id, str) or not actual_target_id:
        raise ContractError("Notion page parent data source is missing")
    if actual_target_id != expected_target_id:
        raise ContractError("Notion page parent data source does not match expected target")


def verify_image(
    client: httpx2.Client,
    block: JSONMap,
    metadata: AssetMetadata,
    timeout: float,
) -> None:
    payload = as_map(block.get("image"), "image")
    if payload.get("type") == "file_upload":
        raise ContractError("Notion image upload is not finalized for Q2")
    if payload.get("type") != "file":
        raise ContractError("Notion image block is not file-backed")
    verify_bytes(
        client, text(as_map(payload.get("file"), "file"), "url"), metadata, timeout
    )


def verify_bytes(
    client: httpx2.Client, url: str, metadata: AssetMetadata, timeout: float
) -> None:
    try:
        request = client.build_request("GET", url, timeout=timeout)
        _ = request.headers.pop("Authorization", None)
        _ = request.headers.pop("Notion-Version", None)
        response = client.send(request, follow_redirects=True)
    except httpx2.RequestError:
        raise ContractError("Notion image verification download failed") from None
    if response.status_code != 200:
        raise ContractError("Notion image verification download failed")
    if hashlib.sha256(response.content).hexdigest() != metadata.original_sha256:
        raise ContractError("Notion image content hash does not match manifest")

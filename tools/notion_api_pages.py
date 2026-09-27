from __future__ import annotations

import json
import re
from collections.abc import Callable

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.notion_api_http import NotionHttp, array, as_map, text
from tools.notion_api_payload import (
    REQUIRED_PROPERTIES,
    plain_fragments,
    select_property,
)

_MAX_REQUEST_BYTES = 500 * 1024
_MAX_ROOT_BLOCKS = 100
_MAX_BLOCK_ELEMENTS = 1_000
_ARTIFACT_DIGEST = re.compile(r"(?:^|;\s*)artifact_digest=([^;\s]+)")


def target_schema(http: NotionHttp, target_id: str, timeout: float) -> JSONMap:
    source = http.read("GET", f"/v1/data_sources/{target_id}", timeout)
    if text(source, "id") != target_id:
        raise ContractError("Notion data source identity does not match configuration")
    props = as_map(source.get("properties"), "data source properties")
    for name, kind in REQUIRED_PROPERTIES.items():
        if as_map(props.get(name), name).get("type") != kind:
            raise ContractError(f"Notion data source property type mismatch: {name}")
    return source


def find_pages(
    http: NotionHttp, target_id: str, run_id: str, _digest: str, timeout: float
) -> tuple[JSONMap, ...]:
    results: list[JSONMap] = []
    cursor: str | None = None
    while True:
        body: JSONMap = {
            "page_size": 100,
            "filter": {"property": "실행 ID", "rich_text": {"equals": run_id}},
        }
        if cursor:
            body["start_cursor"] = cursor
        page = http.read(
            "POST", f"/v1/data_sources/{target_id}/query", timeout, json_body=body
        )
        for raw in array(page, "results"):
            item = as_map(raw, "page")
            props = as_map(item.get("properties"), "properties")
            note = plain_fragments(
                as_map(props.get("검수 메모"), "검수 메모").get("rich_text")
            )
            match = _ARTIFACT_DIGEST.search(note)
            actual_digest = match.group(1) if match is not None else None
            results.append(
                {
                    "id": text(item, "id"),
                    "created_time": text(item, "created_time"),
                    "target_id": target_id,
                    "run_id": run_id,
                    "artifact_digest": actual_digest,
                    "title": plain_fragments(
                        as_map(props.get("제목"), "제목").get("title")
                    ),
                    "execution_date": _date(props),
                    "sequence": as_map(props.get("차수"), "차수").get("number"),
                }
            )
        if page.get("has_more") is not True:
            return tuple(results)
        cursor = text(page, "next_cursor")


def create_page(
    http: NotionHttp,
    target_id: str,
    properties: JSONMap,
    roots: tuple[JSONMap, ...],
    timeout: float,
    authorize: Callable[[], None] | None = None,
) -> JSONMap:
    body: JSONMap = {
        "parent": {"type": "data_source_id", "data_source_id": target_id},
        "properties": properties,
    }
    body["children"] = _children_batch(body, roots, 0, allow_empty=True)
    return http.create(
        "POST",
        "/v1/pages",
        timeout,
        json_body=body,
        resource="page",
        authorize=authorize,
    )


def append_page(
    http: NotionHttp,
    page_id: str,
    roots: tuple[JSONMap, ...],
    start: int,
    timeout: float,
    authorize: Callable[[], None] | None = None,
) -> JSONMap:
    if not 0 <= start < len(roots):
        raise ContractError("Notion append root index is invalid")
    body: JSONMap = {}
    body["children"] = _children_batch(body, roots, start)
    return http.create(
        "PATCH",
        f"/v1/blocks/{page_id}/children",
        timeout,
        json_body=body,
        resource="page append",
        authorize=authorize,
    )


def next_sequence(
    http: NotionHttp, target_id: str, execution_date: str, timeout: float
) -> int:
    maximum, cursor = 0, None
    while True:
        body: JSONMap = {
            "page_size": 100,
            "filter": {"property": "실행일", "date": {"equals": execution_date}},
        }
        if cursor:
            body["start_cursor"] = cursor
        page = http.read(
            "POST", f"/v1/data_sources/{target_id}/query", timeout, json_body=body
        )
        for raw in array(page, "results"):
            props = as_map(as_map(raw, "page").get("properties"), "properties")
            number = as_map(props.get("차수"), "차수").get("number")
            if select_property(props, "상태") == "테스트" or number == 0:
                continue
            if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                raise ContractError("Notion operational sequence is malformed")
            maximum = max(maximum, number)
        if page.get("has_more") is not True:
            return maximum + 1
        cursor = text(page, "next_cursor")


def _date(properties: JSONMap) -> str:
    value = as_map(
        as_map(properties.get("실행일"), "실행일").get("date"), "실행일"
    ).get("start")
    if not isinstance(value, str):
        raise ContractError("Notion execution date is malformed")
    return value


def _children_batch(
    prefix: JSONMap,
    roots: tuple[JSONMap, ...],
    start: int,
    *,
    allow_empty: bool = False,
) -> list[JSONValue]:
    children: list[JSONValue] = []
    block_elements = 0
    for root in roots[start : start + _MAX_ROOT_BLOCKS]:
        children.append(root)
        block_elements += _block_elements(root)
        body = _request_body(prefix, children)
        if block_elements > _MAX_BLOCK_ELEMENTS or _encoded_size(body) > _MAX_REQUEST_BYTES:
            _ = children.pop()
            break
    if not children and (start < len(roots) or not allow_empty):
        raise ContractError("Notion block exceeds request size or element limits")
    if _encoded_size(_request_body(prefix, children)) > _MAX_REQUEST_BYTES:
        raise ContractError("Notion page properties exceed request size limit")
    return children


def _block_elements(value: JSONValue) -> int:
    if isinstance(value, list):
        return sum(_block_elements(item) for item in value)
    if not isinstance(value, dict):
        return 0
    own = 1 if value.get("object") == "block" else 0
    return own + sum(_block_elements(item) for item in value.values())


def _encoded_size(value: JSONMap) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _request_body(prefix: JSONMap, children: list[JSONValue]) -> JSONMap:
    body = dict(prefix)
    body["children"] = children
    return body

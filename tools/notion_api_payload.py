from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.external_adapter import ExternalWriteRequest
from tools.manifest import Manifest
from tools.notion_content import AssetMetadata
from tools.notion_rich_text import (
    expected_rich_text,
    remote_rich_text,
)
from tools.notion_rich_text import (
    plain_fragments as rich_plain_fragments,
)
from tools.notion_transport import image_entries

REQUIRED_PROPERTIES = {
    "제목": "title",
    "모드": "select",
    "차수": "number",
    "실행일": "date",
    "상태": "select",
    "검수 결과": "select",
    "검수 완료일": "date",
    "검수 메모": "rich_text",
    "이미지 수": "number",
    "이미지 업로드 방식": "select",
    "최종 파일 경로": "rich_text",
    "이미지 연결표 경로": "rich_text",
    "본문 문자수": "number",
    "광고 표기 확인": "checkbox",
    "사람 검수": "select",
    "성능 판정": "select",
    "실패 단계": "select",
    "실행 ID": "rich_text",
    "주제 ID": "rich_text",
    "배치 ID": "rich_text",
    "파이프라인 버전": "rich_text",
    "재시도 횟수": "number",
    "전체 처리 시간(초)": "number",
    "품질 점수": "number",
}


def kst_date(created_at: str) -> str:
    try:
        value = datetime.fromisoformat(created_at)
    except ValueError as error:
        raise ContractError("manifest created_at is invalid") from error
    if value.tzinfo is None:
        raise ContractError("manifest created_at must include timezone")
    return value.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()


def page_properties(
    manifest: Manifest,
    request: ExternalWriteRequest,
    title: str,
    sequence: int,
    body_character_count: int,
    *,
    batch_id: str,
    retries: int,
    completed_at: str,
    duration_seconds: float | None,
) -> JSONMap:
    final_entry = next(
        entry for entry in manifest.files if entry.role == "final_markdown"
    )
    map_entry = next(entry for entry in manifest.files if entry.role == "image_map")
    result: JSONMap = {
        "제목": title_value(title),
        "모드": select_value("정식"),
        "차수": {"number": sequence},
        "실행일": {"date": {"start": kst_date(manifest.created_at)}},
        "상태": select_value("검수 대기"),
        "검수 결과": select_value("통과"),
        "검수 완료일": {"date": {"start": _notion_datetime(completed_at)}},
        "검수 메모": rich_value(
            f"artifact_digest={manifest.artifact_digest}; Q1=passed; stage=content-assembler"
        ),
        "이미지 수": {"number": len(image_entries(manifest.files))},
        "이미지 업로드 방식": select_value("Notion 파일 업로드"),
        "최종 파일 경로": rich_value(final_entry.path),
        "이미지 연결표 경로": rich_value(map_entry.path),
        "본문 문자수": {"number": body_character_count},
        "광고 표기 확인": {"checkbox": True},
        "사람 검수": select_value("대기"),
        "성능 판정": select_value("대기"),
        "실패 단계": select_value("없음"),
        "실행 ID": rich_value(request.run_id),
        "주제 ID": rich_value(manifest.topic_id),
        "배치 ID": rich_value(batch_id),
        "파이프라인 버전": rich_value(manifest.pipeline_version),
        "재시도 횟수": {"number": retries},
    }
    if duration_seconds is not None:
        result["전체 처리 시간(초)"] = {"number": duration_seconds}
    return result


def _notion_datetime(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContractError("Notion completion timestamp is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError("Notion completion timestamp must be aware")
    return parsed.replace(second=0, microsecond=0).isoformat(timespec="milliseconds")


def title_value(value: str) -> JSONMap:
    return {"title": [{"type": "text", "text": {"content": value}}]}


def rich_value(value: str) -> JSONMap:
    return {"rich_text": [{"type": "text", "text": {"content": value}}]}


def select_value(value: str) -> JSONMap:
    return {"select": {"name": value}}


def actual_properties(actual: JSONMap, expected: JSONMap) -> JSONMap:
    normalized: JSONMap = {}
    for name, expected_raw in expected.items():
        expected_value = as_map(expected_raw, f"expected property {name}")
        actual_value = as_map(actual.get(name), f"actual property {name}")
        if "title" in expected_value:
            normalized[name] = title_value(plain_fragments(actual_value.get("title")))
        elif "rich_text" in expected_value:
            normalized[name] = rich_value(
                plain_fragments(actual_value.get("rich_text"))
            )
        elif "select" in expected_value:
            normalized[name] = select_value(select_property(actual, name) or "")
        elif "number" in expected_value:
            normalized[name] = {"number": actual_value.get("number")}
        elif "date" in expected_value:
            normalized[name] = {
                "date": {"start": as_map(actual_value.get("date"), name).get("start")}
            }
        elif "checkbox" in expected_value:
            normalized[name] = {"checkbox": actual_value.get("checkbox")}
    return normalized


def canonical_block(block: JSONMap) -> JSONMap:
    kind = text(block, "type")
    payload = as_map(block.get(kind), kind)
    result: JSONMap = {"type": kind}
    if kind == "image":
        if payload.get("type") not in {"file", "file_upload"}:
            raise ContractError("Notion image block is not file-backed")
        result["caption"] = _canonical_rich(payload.get("caption"))
    elif kind == "table":
        result["table_width"] = payload.get("table_width", 0)
        children = payload.get("children")
        if isinstance(children, list):
            result["children"] = [
                canonical_block(as_map(value, "table row")) for value in children
            ]
    elif kind == "table_row":
        cells = payload.get("cells", [])
        result["cells"] = (
            [_canonical_rich(cell) for cell in cells] if isinstance(cells, list) else []
        )
    else:
        result["rich_text"] = _canonical_rich(payload.get("rich_text"))
    return result


def normalized_block(block: JSONMap, metadata: AssetMetadata | None) -> JSONMap:
    kind = text(block, "type")
    payload = as_map(block.get(kind), kind)
    if kind.startswith("heading_"):
        return {
            "type": "heading",
            "heading_level": int(kind[-1]),
            "plain_text": plain_rich(payload),
            "rich_text": remote_rich_text(payload.get("rich_text")),
        }
    if kind == "bulleted_list_item":
        return {
            "type": "list_item",
            "list_type": "bulleted",
            "plain_text": plain_rich(payload),
            "rich_text": remote_rich_text(payload.get("rich_text")),
        }
    if kind == "image":
        if metadata is None:
            raise ContractError("Notion image metadata is unavailable")
        return {
            "type": "image",
            "image": {
                "artifact_role": metadata.artifact_role,
                "original_sha256": metadata.original_sha256,
                "caption": plain_rich({"rich_text": payload.get("caption", [])}),
                "caption_rich_text": remote_rich_text(payload.get("caption")),
                "order": metadata.order,
            },
        }
    if kind == "table":
        return {"type": "table"}
    if kind == "table_row":
        cells = payload.get("cells", [])
        values: list[JSONValue] = []
        rich_values: list[JSONValue] = []
        if not isinstance(cells, list) or not all(isinstance(cell, list) for cell in cells):
            raise ContractError("Notion table row cells must be an array")
        values.extend(plain_rich({"rich_text": cell}) for cell in cells)
        rich_values.extend(remote_rich_text(cell) for cell in cells)
        return {"type": "table_row", "table_cells": values, "table_rich_text": rich_values}
    return {
        "type": "paragraph",
        "plain_text": plain_rich(payload),
        "rich_text": remote_rich_text(payload.get("rich_text")),
    }


def _canonical_rich(value: JSONValue | None) -> list[JSONValue]:
    if not isinstance(value, list):
        raise ContractError("Notion rich_text must be an array")
    return expected_rich_text(value)


def plain_rich(payload: JSONMap) -> str:
    return plain_fragments(payload.get("rich_text"))


def plain_fragments(value: JSONValue | None) -> str:
    return rich_plain_fragments(value)


def select_property(properties: JSONMap, name: str) -> str | None:
    selected = as_map(properties.get(name), name).get("select")
    return None if selected is None else text(as_map(selected, name), "name")


def as_map(value: JSONValue | None, label: str) -> JSONMap:
    if not isinstance(value, dict):
        raise ContractError(f"Notion API {label} must be an object")
    return value


def text(value: JSONMap, key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ContractError(f"Notion API {key} is missing")
    return result


def asset_metadata(manifest: Manifest) -> dict[str, AssetMetadata]:
    return {
        Path(entry.path).name: AssetMetadata(entry.role, entry.sha256, entry.order)
        for entry in image_entries(manifest.files)
    }

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.gate import GateRequest, authorize_external_write
from tools.manifest import verify_manifest


class ExternalSystem(StrEnum):
    NOTION = "notion"
    NAVER = "naver"


class ExternalAction(StrEnum):
    NOTION_WRITE = "notion_write"
    NAVER_DRAFT_SAVE = "naver_draft_save"


@dataclass(frozen=True, slots=True)
class ExternalWriteRequest:
    root: Path
    manifest_path: Path
    run_log: Path
    system: ExternalSystem
    gate: str
    run_id: str
    target_id: str
    dry_run: bool
    notion_page_id: str | None = None
    notion_verified_at: str | None = None
    blog_id: str | None = None


class NotionAdapter(Protocol):
    def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap: ...


@dataclass(frozen=True, slots=True)
class ExternalWritePlan:
    system: ExternalSystem
    action: ExternalAction
    run_id: str
    target_id: str
    artifact_digest: str
    artifact_paths: tuple[str, ...]
    dry_run: bool
    would_execute: bool

    def as_json(self) -> JSONMap:
        return {
            "system": self.system.value,
            "action": self.action.value,
            "run_id": self.run_id,
            "target_id": self.target_id,
            "artifact_digest": self.artifact_digest,
            "artifact_paths": list(self.artifact_paths),
            "dry_run": self.dry_run,
            "would_execute": self.would_execute,
        }


def normalize_notion_page(page: JSONMap) -> JSONMap:
    blocks = page.get("blocks")
    if not isinstance(blocks, list):
        raise ContractError("Notion page blocks must be an array")
    normalized_blocks: list[JSONValue] = []
    for value in blocks:
        if not isinstance(value, dict):
            raise ContractError("Notion block must be an object")
        block_type = value.get("type")
        if not isinstance(block_type, str):
            raise ContractError("Notion block type is missing")
        normalized: JSONMap = {"type": block_type}
        for key in ("plain_text", "heading_level", "list_type", "url"):
            item = value.get(key)
            if isinstance(item, (str, int)):
                normalized[key] = item
        table_cells = value.get("table_cells")
        if isinstance(table_cells, list) and all(
            isinstance(cell, str) for cell in table_cells
        ):
            normalized["table_cells"] = table_cells
        image = value.get("image")
        if isinstance(image, dict):
            image_data: JSONMap = {}
            for key in ("artifact_role", "original_sha256", "caption", "order"):
                item = image.get(key)
                if isinstance(item, (str, int)):
                    image_data[key] = item
            normalized["image"] = image_data
        normalized_blocks.append(normalized)
    title = page.get("title")
    if not isinstance(title, str):
        raise ContractError("Notion page title is missing")
    return {
        "title": title,
        "properties": page.get("properties", {}),
        "blocks": normalized_blocks,
    }


def notion_content_digest(page: JSONMap) -> str:
    normalized = normalize_notion_page(page)
    encoded = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def verify_notion_round_trip(
    expected: JSONMap,
    actual: JSONMap,
    page_id: str,
    verified_at: str,
    artifact_digest: str,
) -> JSONMap:
    expected_digest = notion_content_digest(expected)
    actual_digest = notion_content_digest(actual)
    actual_blocks = actual["blocks"]
    if not isinstance(actual_blocks, list):
        raise ContractError("Notion round-trip blocks are invalid")
    image_roles: list[str | int] = []
    for value in actual_blocks:
        if not isinstance(value, dict):
            continue
        image = value.get("image")
        if isinstance(image, dict):
            role = image.get("artifact_role")
            if isinstance(role, (str, int)):
                image_roles.append(role)
    if not image_roles or image_roles[0] != "thumbnail":
        raise ContractError("Notion first image block must be the thumbnail")
    if expected_digest != actual_digest:
        raise ContractError("Notion round-trip content digest does not match")
    return {
        "storage_integrity": "passed",
        "notion_page_id": page_id,
        "notion_last_verified_at": verified_at,
        "expected_notion_content_digest": expected_digest,
        "notion_content_digest": actual_digest,
        "notion_roundtrip_digest": artifact_digest,
        "first_image_block": "thumbnail",
    }


def plan_external_write(request: ExternalWriteRequest) -> ExternalWritePlan:
    expected_gate = (
        ExternalAction.NOTION_WRITE
        if request.system is ExternalSystem.NOTION
        else ExternalAction.NAVER_DRAFT_SAVE
    )
    if request.gate != expected_gate.value:
        raise ContractError(
            f"external adapter gate does not match system: {request.system.value}"
        )
    verified = authorize_external_write(
        GateRequest(
            root=request.root,
            manifest_path=request.manifest_path,
            run_log=request.run_log,
            gate=request.gate,
            run_id=request.run_id,
            target_id=request.target_id,
            notion_connector=request.system is ExternalSystem.NOTION,
            notion_operation="create_pages",
            notion_resource_id=request.target_id,
            notion_page_id=request.notion_page_id,
            notion_verified_at=request.notion_verified_at,
            blog_id=request.blog_id,
        )
    )
    manifest = verify_manifest(request.root, request.manifest_path)
    if not request.dry_run:
        raise ContractError("external writes are not implemented; use dry-run")
    action = expected_gate
    paths = tuple(entry.path for entry in manifest.files)
    digest = verified.get("verified_artifact_digest")
    if not isinstance(digest, str):
        raise ContractError("verified gate did not return an artifact digest")
    return ExternalWritePlan(
        request.system,
        action,
        request.run_id,
        request.target_id,
        digest,
        paths,
        True,
        False,
    )


__all__ = [
    "ExternalAction",
    "ExternalSystem",
    "ExternalWritePlan",
    "ExternalWriteRequest",
    "NotionAdapter",
    "normalize_notion_page",
    "notion_content_digest",
    "plan_external_write",
    "verify_notion_round_trip",
]

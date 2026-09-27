from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.manifest import Manifest, ManifestFile, verify_manifest
from tools.notion_content_models import (
    BlankBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    ParsedBlock,
    TableBlock,
    TextBlock,
)
from tools.notion_copy_parser import parse_naver_copy


def run_preview(root: Path, run_id: str) -> JSONMap:
    state = _state(root, run_id)
    stages = state.get("stages")
    if not isinstance(stages, dict) or stages.get("content-assembler") not in {
        "passed",
        "validated",
    }:
        return {"status": "pending", "reason": "Q1 통과 후 완성 글을 볼 수 있습니다."}
    manifest = _verified_manifest(root, state, run_id)
    source = next((entry for entry in manifest.files if entry.role == "naver_input"), None)
    if source is None:
        source = next((entry for entry in manifest.files if entry.role == "naver_copy"), None)
    if source is None:
        raise ContractError("manifest has no Naver preview source")
    parsed = parse_naver_copy(root / source.path)
    blocks: list[JSONValue] = [_block_json(block, manifest) for block in parsed.blocks]
    result: JSONMap = {
        "status": "ready",
        "run_id": run_id,
        "artifact_digest": manifest.artifact_digest,
        "title": parsed.title,
        "blocks": blocks,
        "notion_link": _notion_link(state, manifest),
    }
    return result


def preview_asset(root: Path, run_id: str, asset_id: str) -> tuple[bytes, str]:
    state = _state(root, run_id)
    manifest = _verified_manifest(root, state, run_id)
    entry = next(
        (
            item
            for item in manifest.files
            if _asset_id(item) == asset_id and item.role in {"thumbnail", "body_image"}
        ),
        None,
    )
    if entry is None:
        raise ContractError("preview asset is not in the verified manifest")
    path = root / entry.path
    content_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return path.read_bytes(), content_type


def _state(root: Path, run_id: str) -> JSONMap:
    path = root / ".automation/state" / f"{run_id}.json"
    try:
        value: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractError("run state is unavailable") from error
    if not isinstance(value, dict) or value.get("run_id") != run_id:
        raise ContractError("run state identity does not match")
    return value


def _verified_manifest(root: Path, state: JSONMap, run_id: str) -> Manifest:
    value = state.get("manifest_path")
    if not isinstance(value, str):
        raise ContractError("run manifest is unavailable")
    path = (root / value).resolve()
    try:
        _ = path.relative_to(root.resolve())
    except ValueError as error:
        raise ContractError("run manifest escapes workspace") from error
    manifest = verify_manifest(root, path)
    if manifest.run_id != run_id or state.get("artifact_digest") != manifest.artifact_digest:
        raise ContractError("run manifest identity does not match current state")
    return manifest


def _block_json(block: ParsedBlock, manifest: Manifest) -> JSONMap:
    match block:
        case TextBlock(content=content):
            return {"type": "text", "content": content}
        case HeadingBlock(level=level, content=content):
            return {"type": "heading", "level": level, "content": content}
        case ListBlock(items=items):
            return {"type": "list", "items": list(items)}
        case TableBlock(title=title, rows=rows):
            values: list[JSONValue] = []
            for row in rows:
                values.append([{"key": cell.key, "value": cell.value} for cell in row])
            return {"type": "table", "title": title, "rows": values}
        case ImageBlock(filename=filename, alt=alt, representative=representative, caption=caption):
            entry = next(
                (
                    item
                    for item in manifest.files
                    if item.role in {"thumbnail", "body_image"}
                    and Path(item.path).name == filename
                ),
                None,
            )
            if entry is None:
                raise ContractError(f"preview image is not in manifest: {filename}")
            return {
                "type": "image",
                "asset_id": _asset_id(entry),
                "alt": alt,
                "representative": representative,
                "caption": caption,
            }
        case BlankBlock():
            return {"type": "blank"}


def _asset_id(entry: ManifestFile) -> str:
    return f"{entry.order}-{entry.sha256[:16]}"


def _notion_link(state: JSONMap, manifest: Manifest) -> JSONMap:
    if (
        state.get("storage_integrity") != "passed"
        or state.get("artifact_digest") != manifest.artifact_digest
        or not isinstance(state.get("notion_page_id"), str)
    ):
        return {"status": "unavailable", "reason": "현재 Q2 기록과 결과물이 일치하지 않습니다."}
    url = state.get("notion_page_url")
    if not isinstance(url, str):
        return {"status": "verify_required", "reason": "Notion 링크 재확인이 필요합니다."}
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"notion.so", "www.notion.so"}:
        raise ContractError("verified Notion page URL is invalid")
    return {"status": "ready", "url": url}


__all__ = ["preview_asset", "run_preview"]

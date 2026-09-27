from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tools.contract_types import ContractError, JSONMap, JSONValue


def as_map(value: JSONValue, label: str) -> JSONMap:
    if not isinstance(value, dict):
        raise ContractError(f"JSON object required: {label}")
    return value


def load_json_map(path: Path) -> JSONMap:
    try:
        raw: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise ContractError(f"could not load manifest: {path}") from error
    return as_map(raw, str(path))


def text_field(data: JSONMap, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ContractError(f"missing or invalid string field: {key}")
    return value


def integer_field(data: JSONMap, key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"missing or invalid integer field: {key}")
    return value


def aware_datetime(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContractError(f"{field} must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{field} must include a timezone")
    return parsed


def ensure_canonical_layout(entries: tuple[tuple[str, str, int], ...]) -> None:
    roles = tuple(entry[0] for entry in entries)
    expected_prefix = ("final_markdown", "naver_layout", "naver_copy")
    if roles[:3] != expected_prefix or roles[-1] != "thumbnail":
        raise ContractError("manifest roles are not in canonical order")
    image_map_index = 4 if len(roles) > 3 and roles[3] == "naver_input" else 3
    if roles[image_map_index] != "image_map":
        raise ContractError("manifest image map is not in canonical order")
    if any(role != "body_image" for role in roles[image_map_index + 1 : -1]):
        raise ContractError("manifest body images are not in canonical order")
    if tuple(entry[2] for entry in entries) != tuple(range(1, len(entries) + 1)):
        raise ContractError("manifest orders are not contiguous and canonical")
    final_path = Path(entries[0][1])
    if final_path.parent != Path("final") or final_path.suffix != ".md":
        raise ContractError("manifest final markdown path is not canonical")
    keyword = final_path.stem
    expected_paths = [
        f"final/{keyword}.md",
        f"final/{keyword}-naver-layout.md",
        f"final/{keyword}-naver-copy.md",
    ]
    if image_map_index == 4:
        expected_paths.append(f"final/{keyword}-naver-input.md")
    expected_paths.append(
        f"assets/{keyword}/image-map.md",
    )
    if tuple(entry[1] for entry in entries[: image_map_index + 1]) != tuple(
        expected_paths
    ):
        raise ContractError("manifest final artifact paths are not canonical")
    asset_dir = Path("assets") / keyword
    if any(
        Path(entry[1]).parent != asset_dir for entry in entries[image_map_index + 1 :]
    ):
        raise ContractError("manifest image path is not in the topic asset directory")
    if Path(entries[-1][1]).name not in {
        "thumbnail.png",
        "thumbnail.jpg",
        "thumbnail.jpeg",
    }:
        raise ContractError("manifest thumbnail path is not canonical")


__all__ = [
    "as_map",
    "aware_datetime",
    "ensure_canonical_layout",
    "integer_field",
    "load_json_map",
    "text_field",
]

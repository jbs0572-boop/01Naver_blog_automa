from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tools.contract_types import ContractError, JSONValue


@dataclass(frozen=True, slots=True)
class GateRequest:
    root: Path
    manifest_path: Path
    run_log: Path
    gate: str
    run_id: str
    target_id: str
    notion_connector: bool = False
    notion_operation: str | None = None
    notion_resource_id: str | None = None
    notion_page_id: str | None = None
    notion_verified_at: str | None = None
    expected_notion_content_digest: str | None = None
    notion_content_digest: str | None = None
    notion_roundtrip_digest: str | None = None
    q2_artifact_digest: str | None = None
    blog_id: str | None = None
    naver_connector: str | None = None
    naver_operation: str | None = None
    naver_url: str | None = None
    naver_locator: str | None = None
    naver_value: str | None = None
    naver_phase: str | None = None


def parse_aware_datetime(value: JSONValue, field: str) -> datetime:
    if not isinstance(value, str):
        raise ContractError(f"{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContractError(f"{field} is not a valid ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


__all__ = ["GateRequest", "parse_aware_datetime"]

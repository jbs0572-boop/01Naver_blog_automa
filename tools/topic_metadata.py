from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid4

from tools.contract_types import ContractError, JSONMap

CREATOR_ADVISOR_URL = "https://creator-advisor.naver.com/naver_blog/sola_note"


@dataclass(frozen=True, slots=True)
class CreatorAdvisorCandidate:
    keyword: str
    rank: int
    trend_index: float | None = None
    channel_inflow: float | None = None
    exposure: float | None = None
    average_exposure_rank: float | None = None
    competition_index: float | None = None
    duplicate_key: str | None = None
    raw_observation: JSONMap | None = None
    missing_fields: tuple[str, ...] = ()

    def as_json(self) -> JSONMap:
        return {
            "keyword": self.keyword,
            "rank": self.rank,
            "trend_index": self.trend_index,
            "channel_inflow": self.channel_inflow,
            "exposure": self.exposure,
            "average_exposure_rank": self.average_exposure_rank,
            "competition_index": self.competition_index,
            "duplicate_key": self.duplicate_key,
            "raw_observation": self.raw_observation or {},
            "missing_fields": list(self.missing_fields),
        }


@dataclass(frozen=True, slots=True)
class CreatorAdvisorSnapshot:
    as_of_date: str
    captured_at: str
    candidates: tuple[CreatorAdvisorCandidate, ...]
    capture_id: str = ""
    channel_id: str = "naver_blog"
    query_period: str | None = None
    access_status: str = "read_only_success"
    limitations: tuple[str, ...] = ()

    def as_json(self) -> JSONMap:
        normalized_date = date.fromisoformat(self.as_of_date).isoformat()
        capture_id = self.capture_id or uuid4().hex
        return {
            "schema_version": "creator-advisor-snapshot-v1",
            "capture_id": capture_id,
            "as_of_date": normalized_date,
            "captured_at": self.captured_at,
            "timezone": "Asia/Seoul",
            "source_url": CREATOR_ADVISOR_URL,
            "channel_id": self.channel_id,
            "query_period": self.query_period,
            "candidates": [candidate.as_json() for candidate in self.candidates],
            "access_status": self.access_status,
            "limitations": list(self.limitations),
        }


def write_snapshot(root: Path, snapshot: CreatorAdvisorSnapshot) -> Path:
    payload = snapshot.as_json()
    capture_id = payload["capture_id"]
    as_of_date = payload["as_of_date"]
    if not isinstance(capture_id, str) or not isinstance(as_of_date, str):
        raise ContractError("Creator Advisor snapshot identity is invalid")
    destination = root / "metadata" / "creator-advisor" / as_of_date / f"{capture_id}.json"
    if destination.exists():
        raise ContractError("Creator Advisor snapshot is append-only")
    _ = destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    _ = temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _ = temporary.replace(destination)
    return destination


__all__ = [
    "CREATOR_ADVISOR_URL",
    "CreatorAdvisorCandidate",
    "CreatorAdvisorSnapshot",
    "write_snapshot",
]

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.topic_candidate_fields import parse_candidate_fields
from tools.topic_creator_tree import candidate_observations

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
    category_key: str | None = None
    category_rank: int | None = None
    candidate_id: str | None = None
    keyword_text: str | None = None
    aliases: tuple[str, ...] = ()

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
            "category_key": self.category_key,
            "category_rank": self.category_rank,
            "candidate_id": self.candidate_id,
            "keyword_text": self.keyword_text,
            "aliases": list(self.aliases),
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


def normalize_keyword(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().split()).casefold()


def candidates_from_creator_tree(tree: str) -> tuple[CreatorAdvisorCandidate, ...]:
    return tuple(_candidate(raw) for raw in candidate_observations(tree))


def snapshot_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _optional_number(raw: JSONMap, field_name: str) -> float | None:
    value = raw.get(field_name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ContractError(
            f"Creator Advisor snapshot candidate {field_name} is invalid"
        )
    return float(value)


def _optional_string(raw: JSONMap, field_name: str) -> str | None:
    value = raw.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractError(
            f"Creator Advisor snapshot candidate {field_name} is invalid"
        )
    return value


def _string_tuple(raw: JSONMap, field_name: str) -> tuple[str, ...]:
    value = raw.get(field_name)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ContractError(f"Creator Advisor snapshot {field_name} is invalid")
    strings = tuple(item for item in value if isinstance(item, str))
    if len(strings) != len(value):
        raise ContractError(f"Creator Advisor snapshot {field_name} is invalid")
    return strings


def _candidate(raw: JSONMap) -> CreatorAdvisorCandidate:
    fields = parse_candidate_fields(raw)
    return CreatorAdvisorCandidate(
        keyword=fields.keyword,
        rank=fields.rank,
        trend_index=_optional_number(raw, "trend_index"),
        channel_inflow=_optional_number(raw, "channel_inflow"),
        exposure=_optional_number(raw, "exposure"),
        average_exposure_rank=_optional_number(raw, "average_exposure_rank"),
        competition_index=_optional_number(raw, "competition_index"),
        duplicate_key=_optional_string(raw, "duplicate_key"),
        raw_observation=fields.raw_observation,
        missing_fields=_string_tuple(raw, "missing_fields"),
        category_key=fields.category_key,
        category_rank=fields.category_rank,
        candidate_id=fields.candidate_id,
        keyword_text=fields.keyword_text,
        aliases=_string_tuple(raw, "aliases"),
    )


def _snapshot_from_observations(
    raw: JSONMap, *, capture_id: str, as_of_date: str
) -> CreatorAdvisorSnapshot:
    captured_at = raw.get("captured_at")
    if not isinstance(captured_at, str) or not captured_at:
        raise ContractError("Creator Advisor snapshot captured_at is invalid")
    try:
        timestamp = datetime.fromisoformat(captured_at)
    except ValueError as error:
        raise ContractError("Creator Advisor snapshot captured_at is invalid") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ContractError("Creator Advisor snapshot captured_at is invalid")
    candidates_raw = raw.get("candidates")
    if not isinstance(candidates_raw, list) or not candidates_raw:
        raise ContractError("Creator Advisor snapshot candidates are invalid")
    candidates = tuple(
        candidate for candidate in candidates_raw if isinstance(candidate, dict)
    )
    if len(candidates) != len(candidates_raw):
        raise ContractError("Creator Advisor snapshot candidate is invalid")
    channel_id = raw.get("channel_id", "naver_blog")
    query_period = raw.get("query_period")
    access_status = raw.get("access_status", "read_only_success")
    if not isinstance(channel_id, str) or not channel_id:
        raise ContractError("Creator Advisor snapshot channel_id is invalid")
    if query_period is not None and not isinstance(query_period, str):
        raise ContractError("Creator Advisor snapshot query_period is invalid")
    if not isinstance(access_status, str) or not access_status:
        raise ContractError("Creator Advisor snapshot access_status is invalid")
    return CreatorAdvisorSnapshot(
        as_of_date=as_of_date,
        captured_at=captured_at,
        capture_id=capture_id,
        candidates=tuple(_candidate(candidate) for candidate in candidates),
        channel_id=channel_id,
        query_period=query_period,
        access_status=access_status,
        limitations=_string_tuple(raw, "limitations"),
    )


def canonicalize_snapshot_observations(
    path: Path, *, expected_capture_id: str, expected_as_of_date: str
) -> CreatorAdvisorSnapshot:
    try:
        raw_value: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError("Creator Advisor snapshot is unreadable") from error
    if not isinstance(raw_value, dict):
        raise ContractError("Creator Advisor snapshot must be an object")
    snapshot = _snapshot_from_observations(
        raw_value,
        capture_id=expected_capture_id,
        as_of_date=expected_as_of_date,
    )
    return snapshot


def read_snapshot(
    path: Path, *, expected_capture_id: str, expected_as_of_date: str
) -> CreatorAdvisorSnapshot:
    try:
        raw_value: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError("Creator Advisor snapshot is unreadable") from error
    if not isinstance(raw_value, dict):
        raise ContractError("Creator Advisor snapshot must be an object")
    raw = raw_value
    required = {
        "schema_version": "creator-advisor-snapshot-v1",
        "capture_id": expected_capture_id,
        "as_of_date": date.fromisoformat(expected_as_of_date).isoformat(),
        "timezone": "Asia/Seoul",
        "source_url": CREATOR_ADVISOR_URL,
    }
    for field_name, expected in required.items():
        if raw.get(field_name) != expected:
            raise ContractError(f"Creator Advisor snapshot {field_name} is invalid")
    return _snapshot_from_observations(
        raw,
        capture_id=expected_capture_id,
        as_of_date=expected_as_of_date,
    )


__all__ = [
    "CREATOR_ADVISOR_URL",
    "CreatorAdvisorCandidate",
    "CreatorAdvisorSnapshot",
    "candidates_from_creator_tree",
    "canonicalize_snapshot_observations",
    "normalize_keyword",
    "read_snapshot",
    "snapshot_sha256",
    "write_snapshot",
]

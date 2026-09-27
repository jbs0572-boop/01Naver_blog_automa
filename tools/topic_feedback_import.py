from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.topic_feedback_models import (
    TopicFeedbackArtifact,
    compute_digest,
    parse_artifact,
)
from tools.topic_feedback_policy import load_registry
from tools.topic_feedback_redaction import (
    ForbiddenFeedbackFieldError,
    InvalidDemographicCountError,
    sanitize_feedback_payload,
)
from tools.topic_feedback_source_rules import SourceRecord
from tools.topic_feedback_store import SnapshotLocation

SOURCE_REGISTRY_PATH: Final = (
    Path(__file__).resolve().parents[1] / "config/topic-feedback-sources.json"
)
_READ_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


@dataclass(frozen=True, slots=True)
class ImportRequest:
    input_spec: str
    source_id: str
    as_of_date: str
    stdin_text: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedImport:
    location: SnapshotLocation
    artifact: TopicFeedbackArtifact


def _read_regular_file(path: Path) -> str:
    try:
        descriptor = os.open(path, _READ_FLAGS)
    except OSError as error:
        raise ContractError("import input file is unreadable or unsafe") from error
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ContractError("import input file is unreadable or unsafe")
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError) as error:
        raise ContractError("import input file is unreadable or unsafe") from error


def _read_input(request: ImportRequest) -> JSONMap:
    if request.input_spec == "-":
        if request.stdin_text is None:
            raise ContractError("import stdin payload is missing")
        encoded = request.stdin_text
    else:
        if "://" in request.input_spec:
            raise ContractError(
                "import input must be stdin or an explicit local file, not a URL"
            )
        encoded = _read_regular_file(Path(request.input_spec))
    try:
        value: JSONValue = json.loads(encoded)
    except json.JSONDecodeError as error:
        raise ContractError("import input is invalid JSON") from error
    if not isinstance(value, dict):
        raise ContractError("import input must be a JSON object")
    return value


def _source(source_id: str) -> SourceRecord:
    registry = load_registry(SOURCE_REGISTRY_PATH)
    record = next(
        (source for source in registry.sources if source.source_id == source_id), None
    )
    if record is None or not record.external_data_source:
        raise ContractError("import source is not registered for external data")
    if not record.enabled and not record.manual_import_allowed:
        raise ContractError("import source is disabled")
    return record


def _query_period(raw: JSONMap) -> str | None:
    match raw.get("query_period"):
        case None:
            return None
        case str() as period:
            if not period:
                raise ContractError("import query_period is invalid")
            return period
        case dict() as period:
            if frozenset(period) != frozenset({"start", "end"}):
                raise ContractError("import query_period is invalid")
            start = period.get("start")
            end = period.get("end")
            if not isinstance(start, str) or not isinstance(end, str):
                raise ContractError("import query_period is invalid")
            try:
                start_date = date.fromisoformat(start)
                end_date = date.fromisoformat(end)
            except ValueError as error:
                raise ContractError("import query_period is invalid") from error
            if start_date > end_date:
                raise ContractError("import query_period is invalid")
            return f"{start_date.isoformat()}/{end_date.isoformat()}"
        case _:
            raise ContractError("import query_period is invalid")


def prepare_topic_signal(request: ImportRequest) -> PreparedImport:
    raw = _read_input(request)
    try:
        sanitized = sanitize_feedback_payload(raw).payload
    except (ForbiddenFeedbackFieldError, InvalidDemographicCountError) as error:
        raise ContractError("import payload violates redaction policy") from error
    record = _source(request.source_id)
    capture_id = sanitized.get("capture_id")
    raw_payload = sanitized.get("raw_payload")
    if sanitized.get("source_id") != request.source_id:
        raise ContractError("import source identity mismatch")
    if not isinstance(capture_id, str) or not isinstance(raw_payload, dict):
        raise ContractError("import payload fields are invalid")
    if sanitized.get("terms_checked_at") != record.terms_checked_at:
        raise ContractError("import terms review does not match trusted registry")
    query_period = _query_period(sanitized)
    terms_checked_at = (
        f"{record.terms_checked_at}T00:00:00+09:00"
        if record.terms_checked_at is not None
        else None
    )
    missing_fields: list[JSONValue] = [
        name
        for name, value in (
            ("query_period", query_period),
            ("terms_checked_at", terms_checked_at),
        )
        if value is None
    ]
    payload: JSONMap = {
        "schema_version": "topic-signal-snapshot-v1",
        "captured_at": f"{request.as_of_date}T00:00:00+09:00",
        "as_of_date": request.as_of_date,
        "timezone": "Asia/Seoul",
        "limitations": [item for item in record.limitations],
        "input_digests": [],
        "missing_fields": missing_fields,
        "status": "pending",
        "source_id": record.source_id,
        "source_confidence": record.confidence.value,
        "access_mode": record.access_mode.value,
        "query_period": query_period,
        "terms_checked_at": terms_checked_at,
        "raw_payload": raw_payload,
        "derived": {},
        "digest": "",
    }
    payload["digest"] = compute_digest(payload)
    return PreparedImport(
        SnapshotLocation.topic_signal(
            request.source_id, request.as_of_date, capture_id
        ),
        parse_artifact(payload),
    )

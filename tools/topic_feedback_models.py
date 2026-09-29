from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum, unique
from pathlib import Path
from typing import Final, Protocol, override

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from tools.contract_types import ContractError, JSONMap, JSONValue

SCHEMA_PATH: Final = Path(__file__).resolve().parents[1] / "schemas/topic-feedback.schema.json"
KST_OFFSET: Final = "+09:00"


@unique
class ArtifactKind(StrEnum):
    TOPIC_SIGNAL = "topic-signal-snapshot-v1"
    BLOG_STAT = "blog-stat-snapshot-v1"
    BLOG_STAT_V2 = "blog-stat-snapshot-v2"
    PUBLICATION_LINK = "publication-link-v1"
    WEEKLY_FEEDBACK = "weekly-feedback-v1"


@unique
class LifecycleStatus(StrEnum):
    MATURE = "mature"
    PENDING = "pending"
    MISSING = "missing"
    DELAYED = "delayed"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class _DatetimeFormatError(Exception):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class TopicSignalSnapshot:
    payload: JSONMap


@dataclass(frozen=True, slots=True)
class BlogStatSnapshot:
    payload: JSONMap


@dataclass(frozen=True, slots=True)
class PublicationLink:
    payload: JSONMap


@dataclass(frozen=True, slots=True)
class WeeklyFeedback:
    payload: JSONMap


type TopicFeedbackArtifact = (
    TopicSignalSnapshot | BlogStatSnapshot | PublicationLink | WeeklyFeedback
)


class _ValidatorProtocol(Protocol):
    def iter_errors(self, instance: JSONValue) -> Iterable[ValidationError]: ...


def _canonical_bytes(payload: JSONMap) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ContractError("topic feedback artifact contains non-JSON data") from error


def compute_digest(payload: JSONMap) -> str:
    unsigned = dict(payload)
    _ = unsigned.pop("digest", None)
    return f"sha256:{hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()}"


def _aware_datetime(value: JSONValue) -> None:
    if not isinstance(value, str):
        raise _DatetimeFormatError("date-time must be a string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise _DatetimeFormatError("date-time is malformed") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _DatetimeFormatError("date-time must include an offset")
    if not value.endswith(KST_OFFSET):
        raise _DatetimeFormatError("KST timestamp must retain its +09:00 offset")


def _schema() -> JSONMap:
    try:
        value: JSONValue = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError("topic feedback schema is unreadable") from error
    if not isinstance(value, dict):
        raise ContractError("topic feedback schema must be an object")
    return value


def _validate_schema(payload: JSONMap) -> None:
    raw_date = payload.get("as_of_date")
    if isinstance(raw_date, str):
        try:
            _ = date.fromisoformat(raw_date)
        except ValueError as error:
            raise ContractError("topic feedback as_of_date is invalid") from error
    for field in ("captured_at", "terms_checked_at", "published_at"):
        value = payload.get(field)
        if value is not None:
            try:
                _aware_datetime(value)
            except _DatetimeFormatError as error:
                raise ContractError(f"topic feedback {field} is invalid: {error}") from error
    validator = _validator()
    errors = sorted(validator.iter_errors(payload), key=lambda error: tuple(error.path))
    if errors:
        error = errors[0]
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        raise ContractError(f"topic feedback schema violation at {path}: {error.message}")


def _validator() -> _ValidatorProtocol:
    return Draft202012Validator(_schema())


def _missing_values(payload: JSONMap) -> None:
    raw_missing = payload.get("missing_fields")
    if not isinstance(raw_missing, list):
        raise ContractError("missing_fields must be an array")
    missing = {value for value in raw_missing if isinstance(value, str)}
    if len(missing) != len(raw_missing):
        raise ContractError("missing_fields must contain unique strings")
    nullable = {
        key
        for key, value in payload.items()
        if value is None
    }
    if nullable != missing:
        raise ContractError("null values and missing_fields must agree")


def _kind(payload: JSONMap) -> ArtifactKind:
    raw = payload.get("schema_version")
    if not isinstance(raw, str):
        raise ContractError("schema_version must be a string")
    try:
        return ArtifactKind(raw)
    except ValueError as error:
        raise ContractError(f"unsupported topic feedback schema_version: {raw}") from error


def parse_artifact(payload: JSONMap) -> TopicFeedbackArtifact:
    _ = _canonical_bytes(payload)
    _validate_schema(payload)
    _missing_values(payload)
    digest = payload.get("digest")
    if digest != compute_digest(payload):
        raise ContractError("topic feedback artifact digest mismatch")
    kind = _kind(payload)
    constructors = {
        ArtifactKind.TOPIC_SIGNAL: TopicSignalSnapshot,
        ArtifactKind.BLOG_STAT: BlogStatSnapshot,
        ArtifactKind.BLOG_STAT_V2: BlogStatSnapshot,
        ArtifactKind.PUBLICATION_LINK: PublicationLink,
        ArtifactKind.WEEKLY_FEEDBACK: WeeklyFeedback,
    }
    return constructors[kind](payload=dict(payload))


def serialize_artifact(artifact: TopicFeedbackArtifact) -> str:
    validated = parse_artifact(artifact.payload)
    if type(validated) is not type(artifact):
        raise ContractError("topic feedback model kind does not match its payload")
    return _canonical_bytes(artifact.payload).decode("utf-8")

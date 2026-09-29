from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Final, override

from tools.contract_types import JSONMap, JSONValue

MAX_DEMOGRAPHIC_COUNT: Final = 2_147_483_647
MINIMUM_DEMOGRAPHIC_COUNT: Final = 5
FORBIDDEN_KEY_POLICY_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "cookie",
        "secret",
        "session",
        "token",
        "visitor",
    }
)
FORBIDDEN_KEY_SUFFIX_POLICY_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "apikey",
        "authorization",
        "clientsecret",
        "cookie",
        "cookies",
        "credential",
        "header",
        "headers",
        "nidses",
        "nidaut",
        "password",
        "profilepath",
        "referrerquery",
        "secret",
        "sessionid",
        "sessiontoken",
        "token",
        "tokenid",
        "visitorid",
        "visitoridentifier",
    }
)
DEMOGRAPHIC_COLLECTION_KEYS: Final[frozenset[str]] = frozenset(
    {"agebuckets", "demographics", "demographicbuckets", "genderbuckets"}
)


@dataclass(slots=True)
class ForbiddenFeedbackFieldError(Exception):
    field_names: tuple[str, ...]

    @override
    def __str__(self) -> str:
        return "feedback payload contains forbidden secret-like field"


@dataclass(slots=True)
class InvalidDemographicCountError(Exception):
    field_name: str

    @override
    def __str__(self) -> str:
        return "demographic count must be a bounded non-negative integer"


@dataclass(frozen=True, slots=True)
class RedactionAudit:
    removed_field_names: tuple[str, ...]
    removed_sparse_bucket_count: int
    removed_missing_bucket_count: int


@dataclass(frozen=True, slots=True)
class SanitizedFeedbackPayload:
    payload: JSONMap
    audit: RedactionAudit


@dataclass(slots=True)
class _AuditAccumulator:
    """Mutates only while a single pure redaction traversal builds its immutable audit."""

    field_names: set[str] = field(default_factory=set)
    sparse_bucket_count: int = 0
    missing_bucket_count: int = 0

    def record_sparse(self, field_name: str) -> None:
        self.field_names.add(field_name)
        self.sparse_bucket_count += 1

    def record_missing(self, field_name: str) -> None:
        self.field_names.add(field_name)
        self.missing_bucket_count += 1

    def freeze(self) -> RedactionAudit:
        return RedactionAudit(
            tuple(sorted(self.field_names)),
            self.sparse_bucket_count,
            self.missing_bucket_count,
        )


def sanitize_feedback_payload(payload: JSONMap) -> SanitizedFeedbackPayload:
    forbidden_names = _forbidden_field_names(payload)
    if forbidden_names:
        raise ForbiddenFeedbackFieldError(forbidden_names)
    audit = _AuditAccumulator()
    sanitized = _sanitize_value(payload, audit)
    match sanitized:
        case dict() as sanitized_payload:
            return SanitizedFeedbackPayload(sanitized_payload, audit.freeze())
        case _:
            raise AssertionError("JSON map sanitization must remain a map")


def _forbidden_field_names(value: JSONValue) -> tuple[str, ...]:
    names: set[str] = set()
    _collect_forbidden_field_names(value, names)
    return tuple(sorted(names))


def _collect_forbidden_field_names(value: JSONValue, names: set[str]) -> None:
    match value:
        case dict() as mapping:
            for key, nested in mapping.items():
                normalized_key = _normalized_key(key)
                if _is_forbidden_key(normalized_key):
                    names.add(normalized_key)
                _collect_forbidden_field_names(nested, names)
        case list() as items:
            for item in items:
                _collect_forbidden_field_names(item, names)
        case None | bool() | int() | float() | str():
            return


def _sanitize_value(value: JSONValue, audit: _AuditAccumulator) -> JSONValue:
    match value:
        case dict() as mapping:
            sanitized: JSONMap = {}
            for key, nested in mapping.items():
                normalized_key = _normalized_key(key)
                if normalized_key in DEMOGRAPHIC_COLLECTION_KEYS:
                    sanitized[key] = _sanitize_demographic_collection(nested, normalized_key, audit)
                else:
                    sanitized[key] = _sanitize_value(nested, audit)
            return sanitized
        case list() as items:
            return [_sanitize_value(item, audit) for item in items]
        case None | bool() | int() | float() | str():
            return value


def _sanitize_demographic_collection(
    value: JSONValue, field_name: str, audit: _AuditAccumulator
) -> JSONValue:
    match value:
        case list() as buckets:
            sanitized: list[JSONValue] = []
            for bucket in buckets:
                match bucket:
                    case dict() as mapping:
                        sanitized_bucket = _sanitize_demographic_bucket(mapping, field_name, audit)
                        if sanitized_bucket is not None:
                            sanitized.append(sanitized_bucket)
                    case _:
                        audit.record_missing(field_name)
            return sanitized
        case _:
            audit.record_missing(field_name)
            return []


def _sanitize_demographic_bucket(
    bucket: JSONMap, field_name: str, audit: _AuditAccumulator
) -> JSONMap | None:
    if "count" not in bucket:
        audit.record_missing(field_name)
        return None
    count = bucket["count"]
    match count:
        case bool():
            raise InvalidDemographicCountError(field_name)
        case int() as integer:
            if integer < 0 or integer > MAX_DEMOGRAPHIC_COUNT:
                raise InvalidDemographicCountError(field_name)
            if integer < MINIMUM_DEMOGRAPHIC_COUNT:
                audit.record_sparse(field_name)
                return None
            sanitized = _sanitize_value(bucket, audit)
            match sanitized:
                case dict() as sanitized_bucket:
                    return sanitized_bucket
                case _:
                    raise AssertionError("JSON map sanitization must remain a map")
        case None:
            audit.record_missing(field_name)
            return None
        case str() as text:
            if text.strip() == "":
                audit.record_missing(field_name)
                return None
            raise InvalidDemographicCountError(field_name)
        case _:
            raise InvalidDemographicCountError(field_name)


def _normalized_key(key: str) -> str:
    normalized = unicodedata.normalize("NFKC", key).casefold()
    return "".join(character for character in normalized if character.isalnum())


FORBIDDEN_KEYS: Final[frozenset[str]] = frozenset(
    _normalized_key(token) for token in FORBIDDEN_KEY_POLICY_TOKENS
)
FORBIDDEN_KEY_SUFFIXES: Final[tuple[str, ...]] = tuple(
    sorted(
        (_normalized_key(token) for token in FORBIDDEN_KEY_SUFFIX_POLICY_TOKENS),
        key=len,
        reverse=True,
    )
)


def _is_forbidden_key(normalized_key: str) -> bool:
    return normalized_key in FORBIDDEN_KEYS or normalized_key.endswith(FORBIDDEN_KEY_SUFFIXES)

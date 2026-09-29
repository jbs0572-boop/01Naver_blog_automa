from __future__ import annotations

import json
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.topic_feedback_source_rules import (
    EXPECTED_SOURCE_IDS,
    MISSING_TENTH_FIELDS,
    REGISTRY_FIELDS,
    SOURCE_FIELDS,
    TERMS_POLICY_FIELDS,
    AccessMode,
    SourceConfidence,
    SourceRecord,
    SourceRegistry,
    follows_source_rule,
)
from tools.topic_feedback_trust import (
    CURRENT_TERMS_URL,
    TRUSTED_TERMS_CHECKED_AT,
    TRUSTED_TERMS_EFFECTIVE_DATE,
    contains_sensitive_config_data,
    matches_trusted_source_identity,
)

SAFE_ACTIVATION_ERROR: Final = (
    "source activation requires identified product and reviewed terms"
)
_SCHEMA_VERSION: Final = "topic-feedback-source-registry-v1"


class SourcePolicyError(ContractError):
    pass


def _required_string(raw: JSONMap, name: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value.strip():
        raise SourcePolicyError("source registry field is invalid")
    return value


def _string_tuple(raw: JSONMap, name: str) -> tuple[str, ...]:
    value = raw.get(name)
    if not isinstance(value, list):
        raise SourcePolicyError("source registry field is invalid")
    strings = tuple(item for item in value if isinstance(item, str) and item)
    if len(strings) != len(value):
        raise SourcePolicyError("source registry field is invalid")
    return strings


def _date_value(raw: JSONMap, name: str, *, allow_none: bool) -> str | None:
    value = raw.get(name)
    if value is None and allow_none:
        return None
    if not isinstance(value, str):
        raise SourcePolicyError("source registry field is invalid")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise SourcePolicyError("source registry field is invalid") from error


def _optional_url(raw: JSONMap) -> str | None:
    value = raw.get("official_url")
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith("https://"):
        raise SourcePolicyError("source registry field is invalid")
    return value


def _optional_string(raw: JSONMap, name: str) -> str | None:
    value = raw.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SourcePolicyError("source registry field is invalid")
    return value


def _enum_value[E: StrEnum](raw: JSONMap, name: str, enum_type: type[E]) -> E:
    value = _required_string(raw, name)
    try:
        return enum_type(value)
    except ValueError as error:
        raise SourcePolicyError("source registry field is invalid") from error


def _required_bool(raw: JSONMap, name: str) -> bool:
    value = raw.get(name)
    if not isinstance(value, bool):
        raise SourcePolicyError("source registry field is invalid")
    return value


def _source_record(raw: JSONMap) -> SourceRecord:
    if frozenset(raw) != SOURCE_FIELDS:
        raise SourcePolicyError("source registry field is invalid")
    source_id = _required_string(raw, "id")
    record = SourceRecord(
        source_id=source_id,
        display_name=_required_string(raw, "display_name"),
        product_identified=_required_bool(raw, "product_identified"),
        official_url=_optional_url(raw),
        terms_checked_at=_date_value(raw, "terms_checked_at", allow_none=True),
        confidence=_enum_value(raw, "confidence", SourceConfidence),
        access_mode=_enum_value(raw, "access_mode", AccessMode),
        enabled=_required_bool(raw, "enabled"),
        activation_proof=_optional_string(raw, "activation_proof"),
        automatic_candidate_provider=_required_bool(
            raw, "automatic_candidate_provider"
        ),
        manual_import_allowed=_required_bool(raw, "manual_import_allowed"),
        external_data_source=_required_bool(raw, "external_data_source"),
        allowed_purposes=_string_tuple(raw, "allowed_purposes"),
        limitations=_string_tuple(raw, "limitations"),
    )
    if record.enabled and (
        record.source_id not in EXPECTED_SOURCE_IDS
        or not record.product_identified
        or record.official_url is None
        or record.terms_checked_at is None
        or record.access_mode is AccessMode.DISABLED
        or not record.external_data_source
    ):
        raise SourcePolicyError(SAFE_ACTIVATION_ERROR)
    if not matches_trusted_source_identity(record.as_json()):
        raise SourcePolicyError("source registry source identity is invalid")
    if not follows_source_rule(record.as_json()):
        if record.source_id == "naver-search-ads-keyword-tool" and record.enabled:
            raise SourcePolicyError(SAFE_ACTIVATION_ERROR)
        raise SourcePolicyError("source registry source policy is invalid")
    return record


def load_registry(path: Path) -> SourceRegistry:
    try:
        raw_value: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourcePolicyError("source registry is unreadable") from error
    if not isinstance(raw_value, dict) or contains_sensitive_config_data(raw_value):
        raise SourcePolicyError("source registry contains secret-like data")
    if frozenset(raw_value) != REGISTRY_FIELDS:
        raise SourcePolicyError("source registry field is invalid")
    if raw_value.get("schema_version") != _SCHEMA_VERSION:
        raise SourcePolicyError("source registry schema version is invalid")
    if raw_value.get("scope") != "C" or raw_value.get("kpi_v1") != "search_inflow":
        raise SourcePolicyError("source registry decisions are invalid")
    if raw_value.get("candidate_source") != "creator_advisor_only":
        raise SourcePolicyError("source registry candidate source is invalid")
    missing_tenth = raw_value.get("missing_tenth_tool")
    if (
        not isinstance(missing_tenth, dict)
        or frozenset(missing_tenth) != MISSING_TENTH_FIELDS
        or missing_tenth.get("id") != "missing-tenth-tool"
        or missing_tenth.get("status") != "disabled_placeholder"
        or missing_tenth.get("identified_product") is not False
    ):
        raise SourcePolicyError("source registry placeholder is invalid")
    terms_policy = raw_value.get("terms_policy")
    if (
        not isinstance(terms_policy, dict)
        or frozenset(terms_policy) != TERMS_POLICY_FIELDS
        or terms_policy.get("official_url") != CURRENT_TERMS_URL
    ):
        raise SourcePolicyError("source registry terms policy is invalid")
    terms_checked_at = _date_value(terms_policy, "terms_checked_at", allow_none=False)
    if terms_checked_at is None:
        raise SourcePolicyError("source registry terms policy is invalid")
    effective_date = _date_value(terms_policy, "effective_date", allow_none=False)
    if effective_date is None:
        raise SourcePolicyError("source registry terms policy is invalid")
    if (
        terms_checked_at != TRUSTED_TERMS_CHECKED_AT
        or effective_date != TRUSTED_TERMS_EFFECTIVE_DATE
    ):
        raise SourcePolicyError("source registry terms policy is invalid")
    terms_conclusion = _required_string(terms_policy, "conclusion")
    sources_value = raw_value.get("sources")
    if not isinstance(sources_value, list):
        raise SourcePolicyError("source registry sources are invalid")
    sources = tuple(
        _source_record(source) for source in sources_value if isinstance(source, dict)
    )
    if len(sources) != len(sources_value):
        raise SourcePolicyError("source registry sources are invalid")
    source_ids = tuple(source.source_id for source in sources)
    if frozenset(source_ids) != EXPECTED_SOURCE_IDS or len(set(source_ids)) != len(
        source_ids
    ):
        raise SourcePolicyError("source registry sources are invalid")
    display_names = tuple(source.display_name for source in sources)
    if len(set(display_names)) != len(display_names):
        raise SourcePolicyError("source registry source policy is invalid")
    return SourceRegistry(
        "C",
        "creator_advisor_only",
        "search_inflow",
        CURRENT_TERMS_URL,
        terms_checked_at,
        effective_date,
        terms_conclusion,
        sources,
    )

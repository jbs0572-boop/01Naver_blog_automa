from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Protocol

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from tools.contract_types import ContractError, JSONMap
from tools.topic_feedback_models import (
    compute_digest,
    parse_artifact,
    serialize_artifact,
)

SCHEMA = Path("schemas/topic-feedback.schema.json")


class _ValidatorProtocol(Protocol):
    def validate(self, instance: JSONMap) -> None: ...


def _validator() -> _ValidatorProtocol:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _base(version: str) -> JSONMap:
    return {
        "schema_version": version,
        "captured_at": "2026-09-08T09:30:00+09:00",
        "as_of_date": "2026-09-08",
        "timezone": "Asia/Seoul",
        "limitations": [],
        "input_digests": ["sha256:" + "1" * 64],
        "missing_fields": [],
        "status": "mature",
        "digest": "",
    }


def artifacts() -> list[JSONMap]:
    signal = _base("topic-signal-snapshot-v1")
    signal.update(
        {
            "source_id": "naver_datalab",
            "source_confidence": "A",
            "access_mode": "official_api",
            "query_period": "2026-09-01/2026-09-07",
            "terms_checked_at": "2026-09-08T08:00:00+09:00",
            "raw_payload": {"ratio": 3.5},
            "derived": {"trend_index": 3.5},
        }
    )
    stats = _base("blog-stat-snapshot-v1")
    stats.update(
        {
            "blog_post_id": "POST-1",
            "search_inflow": 2,
            "views": 3,
            "exposure": None,
            "average_exposure_rank": None,
        }
    )
    stats["missing_fields"] = ["exposure", "average_exposure_rank"]
    stats_v2 = _base("blog-stat-snapshot-v2")
    stats_v2.update(
        {
            "mapping_version": "blog-stats-columns-v1",
            "capture_id": "CAP-1",
            "blog_id": "owner",
            "blog_post_id": "POST-1",
            "publication_run_id": "RUN-1",
            "publication_link_digest": "sha256:" + "2" * 64,
            "coverage_start": "2026-09-01",
            "coverage_end": "2026-09-08",
            "search_inflow": 2,
            "views": 3,
            "exposure": None,
            "average_exposure_rank": None,
            "demographics": [{"dimension": "age", "bucket": "30-39", "count": 5}],
        }
    )
    stats_v2["missing_fields"] = ["exposure", "average_exposure_rank"]
    link = _base("publication-link-v1")
    link.update(
        {
            "run_id": "RUN-1",
            "topic_id": "TOPIC-1",
            "keyword": "서울 여행",
            "blog_post_id": None,
            "published_at": "2026-09-07T12:00:00+09:00",
            "artifact_digest": "sha256:" + "2" * 64,
            "score_version": "rules-v1",
            "source_identity": "current-run",
            "legacy_identity": None,
        }
    )
    link["status"] = "pending"
    link["missing_fields"] = ["blog_post_id", "legacy_identity"]
    weekly = _base("weekly-feedback-v1")
    weekly.update(
        {
            "feedback_id": "FB-1",
            "score_version": "rules-v1",
            "cohorts": [],
            "rankings": [],
        }
    )
    result = [signal, stats, link, weekly, stats_v2]
    for value in result:
        value["digest"] = compute_digest(value)
    return result


@pytest.mark.parametrize("payload", artifacts())
def test_valid_union_roundtrip_and_digest(payload: JSONMap) -> None:
    _validator().validate(payload)
    model = parse_artifact(payload)
    encoded = serialize_artifact(model)
    assert encoded == serialize_artifact(parse_artifact(json.loads(encoded)))
    assert json.loads(encoded)["digest"] == compute_digest(payload)


def test_digest_is_independent_of_key_order() -> None:
    payload = artifacts()[0]
    reversed_payload = dict(reversed(tuple(payload.items())))
    assert compute_digest(payload) == compute_digest(reversed_payload)


def test_weekly_feedback_rejects_arbitrary_cohort_and_ranking_objects() -> None:
    # Given: the legacy weekly shell with untyped object payloads.
    payload = artifacts()[3]
    payload["cohorts"] = [{"surprise": True}]
    payload["rankings"] = [{"surprise": True}]
    payload["digest"] = compute_digest(payload)

    # When/Then: strict weekly structures reject both arbitrary objects.
    with pytest.raises(ValidationError):
        _validator().validate(payload)


@pytest.mark.parametrize("mutation", ["unknown_field", "invalid_version", "cross_kind"])
def test_rejects_unknown_fields_versions_and_cross_kind_leakage(mutation: str) -> None:
    payload = copy.deepcopy(artifacts()[0])
    if mutation == "unknown_field":
        payload["surprise"] = True
    elif mutation == "invalid_version":
        payload["schema_version"] = "topic-signal-snapshot-v2"
    else:
        payload["views"] = 10
    payload["digest"] = compute_digest(payload)
    with pytest.raises(ContractError):
        _ = parse_artifact(payload)


def test_rejects_digest_tamper() -> None:
    payload = artifacts()[1]
    payload["views"] = 99
    with pytest.raises(ContractError, match="digest"):
        _ = parse_artifact(payload)


def test_rejects_naive_datetime_and_non_kst_offset() -> None:
    for timestamp in ("2026-09-08T09:30:00", "2026-09-08T09:30:00+00:00"):
        payload = artifacts()[0]
        payload["captured_at"] = timestamp
        payload["digest"] = compute_digest(payload)
        with pytest.raises(ContractError):
            _ = parse_artifact(payload)


def test_rejects_invalid_calendar_date() -> None:
    payload = artifacts()[0]
    payload["as_of_date"] = "2026-99-99"
    payload["digest"] = compute_digest(payload)
    with pytest.raises(ContractError, match="as_of_date"):
        _ = parse_artifact(payload)


def test_v2_rejects_sparse_demographic_bucket() -> None:
    payload = artifacts()[4]
    demographics = payload["demographics"]
    assert isinstance(demographics, list)
    bucket = demographics[0]
    assert isinstance(bucket, dict)
    bucket["count"] = 4
    payload["digest"] = compute_digest(payload)
    with pytest.raises(ContractError, match="schema violation"):
        _ = parse_artifact(payload)

from __future__ import annotations

import json

import pytest

from tools.contract_types import ContractError, JSONMap
from tools.topic_feedback_models import (
    compute_digest,
    parse_artifact,
    serialize_artifact,
)


def artifacts() -> list[JSONMap]:
    common: JSONMap = {
        "schema_version": "blog-stat-snapshot-v1",
        "captured_at": "2026-09-08T09:30:00+09:00",
        "as_of_date": "2026-09-08",
        "timezone": "Asia/Seoul",
        "limitations": [],
        "input_digests": ["sha256:" + "1" * 64],
        "missing_fields": ["exposure", "average_exposure_rank"],
        "status": "mature",
        "blog_post_id": "POST-1",
        "search_inflow": 2,
        "views": 3,
        "exposure": None,
        "average_exposure_rank": None,
        "digest": "",
    }
    common["digest"] = compute_digest(common)
    link: JSONMap = {
        "schema_version": "publication-link-v1",
        "captured_at": "2026-09-08T09:30:00+09:00",
        "as_of_date": "2026-09-08",
        "timezone": "Asia/Seoul",
        "limitations": [],
        "input_digests": [],
        "missing_fields": ["blog_post_id", "legacy_identity"],
        "status": "pending",
        "run_id": "RUN-1",
        "topic_id": "TOPIC-1",
        "keyword": "서울 여행",
        "blog_post_id": None,
        "published_at": "2026-09-07T12:00:00+09:00",
        "artifact_digest": "sha256:" + "2" * 64,
        "score_version": "rules-v1",
        "source_identity": "current-run",
        "legacy_identity": None,
        "digest": "",
    }
    link["digest"] = compute_digest(link)
    return [common, link]


@pytest.mark.parametrize(
    "field", ["search_inflow", "views", "exposure", "average_exposure_rank"]
)
@pytest.mark.parametrize("value", [-1, True])
def test_numeric_boundaries(field: str, value: float | bool) -> None:
    payload = artifacts()[0]
    payload[field] = value
    payload["missing_fields"] = [
        name for name in ("exposure", "average_exposure_rank") if payload[name] is None
    ]
    payload["digest"] = compute_digest(payload)
    with pytest.raises(ContractError, match="schema violation"):
        _ = parse_artifact(payload)


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
@pytest.mark.parametrize(
    "field", ["search_inflow", "views", "exposure", "average_exposure_rank"]
)
def test_non_finite_metrics_have_no_digest_and_are_rejected(
    field: str, value: float
) -> None:
    payload = artifacts()[0]
    payload[field] = value
    payload["missing_fields"] = [
        name for name in ("exposure", "average_exposure_rank") if payload[name] is None
    ]
    with pytest.raises(ContractError, match="non-JSON"):
        _ = compute_digest(payload)
    with pytest.raises(ContractError, match="non-JSON"):
        _ = parse_artifact(payload)


def test_missing_metrics_require_null_and_missing_fields() -> None:
    payload = artifacts()[0]
    payload["missing_fields"] = []
    payload["digest"] = compute_digest(payload)
    with pytest.raises(ContractError):
        _ = parse_artifact(payload)


def test_zero_metrics_are_valid() -> None:
    payload = artifacts()[0]
    for field in ("search_inflow", "views", "exposure", "average_exposure_rank"):
        payload[field] = 0
    payload["missing_fields"] = []
    payload["digest"] = compute_digest(payload)
    _ = parse_artifact(payload)


@pytest.mark.parametrize(
    "field", ["search_inflow", "views", "exposure", "average_exposure_rank"]
)
def test_blank_metric_is_invalid_and_not_missing(field: str) -> None:
    payload = artifacts()[0]
    payload[field] = ""
    payload["missing_fields"] = [
        name for name in ("exposure", "average_exposure_rank") if payload[name] is None
    ]
    payload["digest"] = compute_digest(payload)
    with pytest.raises(ContractError, match="schema violation"):
        _ = parse_artifact(payload)


def test_malformed_hashes_are_rejected() -> None:
    payload = artifacts()[1]
    payload["artifact_digest"] = "abc"
    payload["digest"] = compute_digest(payload)
    with pytest.raises(ContractError):
        _ = parse_artifact(payload)


def test_publication_identity_is_preserved() -> None:
    payload = artifacts()[1]
    encoded = serialize_artifact(parse_artifact(payload))
    decoded: JSONMap = json.loads(encoded)
    assert decoded["run_id"] == "RUN-1"
    assert decoded["blog_post_id"] is None
    assert decoded["source_identity"] == "current-run"


def test_serialize_rejects_mutation_after_parse() -> None:
    model = parse_artifact(artifacts()[0])
    model.payload["views"] = 100
    with pytest.raises(ContractError, match="digest"):
        _ = serialize_artifact(model)


def test_v2_blog_stat_roundtrip_preserves_publication_and_coverage() -> None:
    payload: JSONMap = {
        "schema_version": "blog-stat-snapshot-v2",
        "captured_at": "2026-09-08T09:30:00+09:00",
        "as_of_date": "2026-09-07",
        "timezone": "Asia/Seoul",
        "limitations": ["owner_export_only"],
        "input_digests": ["sha256:" + "1" * 64, "sha256:" + "2" * 64],
        "missing_fields": ["average_exposure_rank", "exposure"],
        "status": "mature",
        "digest": "",
        "mapping_version": "blog-stats-columns-v1",
        "capture_id": "CAP-1",
        "blog_id": "owner",
        "blog_post_id": "POST-1",
        "publication_run_id": "RUN-1",
        "publication_link_digest": "sha256:" + "2" * 64,
        "coverage_start": "2026-09-01",
        "coverage_end": "2026-09-07",
        "search_inflow": 2,
        "views": 3,
        "exposure": None,
        "average_exposure_rank": None,
        "demographics": [],
    }
    payload["digest"] = compute_digest(payload)

    encoded = serialize_artifact(parse_artifact(payload))

    decoded: JSONMap = json.loads(encoded)
    assert decoded["publication_run_id"] == "RUN-1"
    assert decoded["coverage_end"] == "2026-09-07"

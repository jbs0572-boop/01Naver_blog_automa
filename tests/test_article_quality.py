from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tools.article_quality import (
    QUALITY_REPORT_VERSION,
    RUBRIC_VERSION,
    SCORE_MAXIMA,
    ArticleQualityAssessment,
    assessment_digest,
    parse_assessment,
)
from tools.contract_types import ContractError, JSONMap


def _report(scores: dict[str, int] | None = None) -> JSONMap:
    active_scores = scores or dict(SCORE_MAXIMA)
    score_values: JSONMap = {name: value for name, value in active_scores.items()}
    failed = sum(active_scores.values()) < 85
    evidence: JSONMap = {
        name: "checked against the cited source and final image" for name in SCORE_MAXIMA
    }
    payload: JSONMap = {
        "report_version": QUALITY_REPORT_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "run_id": "run-1",
        "topic_id": "topic-1",
        "artifact_digest": "sha256:" + "a" * 64,
        "reviewer": "human-reviewer",
        "reviewed_at": "2026-09-01T00:05:00+09:00",
        "scores": score_values,
        "evidence": evidence,
        "immediate_failures": [],
        "cause_type": "execution_defect" if failed else None,
        "failure_stage": "writer" if failed else None,
        "next_action": "review evidence and revise" if failed else "none",
    }
    payload["report_digest"] = assessment_digest(payload)
    return payload


def _parse(payload: JSONMap) -> ArticleQualityAssessment:
    return parse_assessment(
        payload,
        expected_run_id="run-1",
        expected_topic_id="topic-1",
        expected_artifact_digest="sha256:" + "a" * 64,
        q2_verified_at="2026-09-01T00:03:00+09:00",
    )


def test_assessment_passes_at_exact_85_with_current_identity() -> None:
    scores = dict(SCORE_MAXIMA)
    scores["factual_accuracy"] = 10

    assessment = _parse(_report(scores))

    assert assessment.total_score == 85
    assert assessment.passed


@pytest.mark.parametrize("score", [84, 63])
def test_assessment_below_85_fails_even_without_immediate_failure(score: int) -> None:
    scores = dict(SCORE_MAXIMA)
    if score == 84:
        scores["factual_accuracy"] = 9
    else:
        scores["factual_accuracy"] = 0
        scores["source_completeness"] = 8

    assessment = _parse(_report(scores))

    assert not assessment.passed


def test_immediate_failure_overrides_a_high_total() -> None:
    report = _report()
    report["immediate_failures"] = ["unsupported_core_claim"]
    report["cause_type"] = "execution_defect"
    report["failure_stage"] = "researcher"
    report["next_action"] = "repair source evidence"
    report["report_digest"] = assessment_digest(report)

    assert not _parse(report).passed


@pytest.mark.parametrize(
    "change",
    [
        {"artifact_digest": "sha256:" + "b" * 64},
        {"rubric_version": "old-rubric"},
        {"reviewer": ""},
    ],
)
def test_assessment_rejects_stale_or_incomplete_identity(change: dict[str, str]) -> None:
    report = _report()
    report.update(change)
    report["report_digest"] = assessment_digest(report)

    with pytest.raises(ContractError):
        _ = _parse(report)


def test_assessment_rejects_missing_evidence_invalid_digest_and_pre_q2_review() -> None:
    missing_evidence = _report()
    evidence = missing_evidence["evidence"]
    assert isinstance(evidence, dict)
    del evidence["image_quality"]
    missing_evidence["report_digest"] = assessment_digest(missing_evidence)
    with pytest.raises(ContractError, match="evidence"):
        _ = _parse(missing_evidence)

    invalid_digest = _report()
    invalid_digest["report_digest"] = "sha256:" + "0" * 64
    with pytest.raises(ContractError, match="digest"):
        _ = _parse(invalid_digest)

    pre_q2 = _report()
    pre_q2["reviewed_at"] = "2026-09-01T00:02:59+09:00"
    pre_q2["report_digest"] = assessment_digest(pre_q2)
    with pytest.raises(ContractError, match="after"):
        _ = _parse(pre_q2)

    same_as_q2 = _report()
    same_as_q2["reviewed_at"] = "2026-09-01T00:03:00+09:00"
    same_as_q2["report_digest"] = assessment_digest(same_as_q2)
    with pytest.raises(ContractError, match="after"):
        _ = _parse(same_as_q2)

def test_assessment_timestamp_is_aware() -> None:
    report = _report()
    report["reviewed_at"] = datetime.now(UTC).replace(tzinfo=None).isoformat()
    report["report_digest"] = assessment_digest(report)

    with pytest.raises(ContractError, match="timezone"):
        _ = _parse(report)

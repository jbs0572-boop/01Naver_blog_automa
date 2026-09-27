from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.gate_models import parse_aware_datetime

RUBRIC_VERSION: Final = "article-quality-v1"
QUALITY_REPORT_VERSION: Final = "article-quality-report-v1"
SCORE_MAXIMA: Final = {
    "factual_accuracy": 25,
    "source_completeness": 20,
    "search_intent": 15,
    "structure_and_style": 15,
    "seo_and_aeo": 10,
    "image_quality": 10,
    "storage_integrity": 5,
}


@dataclass(frozen=True, slots=True)
class ArticleQualityAssessment:
    run_id: str
    topic_id: str
    artifact_digest: str
    reviewer: str
    reviewed_at: datetime
    scores: tuple[tuple[str, int], ...]
    evidence: tuple[tuple[str, str], ...]
    immediate_failures: tuple[str, ...]
    cause_type: str | None
    failure_stage: str | None
    next_action: str
    report_digest: str

    @property
    def total_score(self) -> int:
        return sum(score for _, score in self.scores)

    @property
    def passed(self) -> bool:
        return self.total_score >= 85 and not self.immediate_failures


class ArticleQualityFailure(ContractError):
    """A current human review failed the required article quality threshold."""

    assessment: ArticleQualityAssessment

    def __init__(self, assessment: ArticleQualityAssessment) -> None:
        self.assessment = assessment
        super().__init__(
            f"quality_failed: article score {assessment.total_score}/100; cause={assessment.cause_type}; stage={assessment.failure_stage}; next_action={assessment.next_action}"
        )

    @property
    def event_details(self) -> JSONMap:
        """Return nonsensitive review feedback for the current run event."""
        return {
            "article_quality": {
                "article_quality_score": self.assessment.total_score,
                "article_quality_rubric_version": RUBRIC_VERSION,
                "article_quality_report_digest": self.assessment.report_digest,
                "cause_type": self.assessment.cause_type,
                "failure_stage": self.assessment.failure_stage,
                "next_action": self.assessment.next_action,
                "immediate_failures": list(self.assessment.immediate_failures),
            }
        }


def assessment_path(root: Path, run_id: str) -> Path:
    """Return the run-scoped local article review report path."""
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ContractError("quality report run_id is not a safe path component")
    return root / "metadata" / "quality-reviews" / f"{run_id}.json"


def _required_string(payload: JSONMap, field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"quality report {field} is missing")
    return value.strip()


def _report_digest(payload: JSONMap) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "report_digest"}
    canonical = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def parse_assessment(
    payload: JSONValue,
    *,
    expected_run_id: str,
    expected_topic_id: str,
    expected_artifact_digest: str,
    q2_verified_at: str,
) -> ArticleQualityAssessment:
    """Parse and bind a human review to the latest Q2-verified artifact."""
    if not isinstance(payload, dict):
        raise ContractError("quality report must be a JSON object")
    if payload.get("report_version") != QUALITY_REPORT_VERSION:
        raise ContractError("quality report version is unsupported")
    if payload.get("rubric_version") != RUBRIC_VERSION:
        raise ContractError("quality report rubric version does not match")
    run_id = _required_string(payload, "run_id")
    topic_id = _required_string(payload, "topic_id")
    artifact_digest = _required_string(payload, "artifact_digest")
    if (run_id, topic_id, artifact_digest) != (
        expected_run_id,
        expected_topic_id,
        expected_artifact_digest,
    ):
        raise ContractError("quality report does not match the current run artifacts")
    reviewer = _required_string(payload, "reviewer")
    reviewed_at = parse_aware_datetime(payload.get("reviewed_at"), "reviewed_at")
    if reviewed_at < parse_aware_datetime(q2_verified_at, "notion_last_verified_at"):
        raise ContractError("quality report predates the successful Notion Q2 review")

    raw_scores = payload.get("scores")
    raw_evidence = payload.get("evidence")
    if not isinstance(raw_scores, dict) or set(raw_scores) != set(SCORE_MAXIMA):
        raise ContractError("quality report must score every rubric category")
    if not isinstance(raw_evidence, dict) or set(raw_evidence) != set(SCORE_MAXIMA):
        raise ContractError("quality report must provide evidence for every category")
    scores: list[tuple[str, int]] = []
    evidence: list[tuple[str, str]] = []
    for name, maximum in SCORE_MAXIMA.items():
        score = raw_scores[name]
        note = raw_evidence[name]
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= maximum:
            raise ContractError(f"quality report score is invalid: {name}")
        if not isinstance(note, str) or not note.strip():
            raise ContractError(f"quality report evidence is missing: {name}")
        scores.append((name, score))
        evidence.append((name, note.strip()[:1000]))

    raw_failures = payload.get("immediate_failures")
    if not isinstance(raw_failures, list):
        raise ContractError("quality report immediate_failures must be string codes")
    failure_codes: list[str] = []
    for item in raw_failures:
        if not isinstance(item, str) or not item.strip():
            raise ContractError("quality report immediate_failures must be string codes")
        code = item.strip()
        if code not in failure_codes:
            failure_codes.append(code)
    failures = tuple(failure_codes)
    total = sum(score for _, score in scores)
    passed = total >= 85 and not failures
    cause_type = payload.get("cause_type")
    failure_stage = payload.get("failure_stage")
    next_action = _required_string(payload, "next_action")
    if passed:
        if cause_type is not None or failure_stage is not None:
            raise ContractError("passing quality report cannot carry failure metadata")
    elif cause_type not in {"topic_unsuitable", "execution_defect"}:
        raise ContractError("failed quality report requires a classified cause")
    elif not isinstance(failure_stage, str) or not failure_stage.strip():
        raise ContractError("failed quality report requires failure_stage")

    supplied_digest = _required_string(payload, "report_digest")
    calculated_digest = _report_digest(payload)
    if supplied_digest != calculated_digest:
        raise ContractError("quality report digest is invalid")
    return ArticleQualityAssessment(
        run_id=run_id,
        topic_id=topic_id,
        artifact_digest=artifact_digest,
        reviewer=reviewer,
        reviewed_at=reviewed_at,
        scores=tuple(scores),
        evidence=tuple(evidence),
        immediate_failures=failures,
        cause_type=cause_type if isinstance(cause_type, str) else None,
        failure_stage=failure_stage if isinstance(failure_stage, str) else None,
        next_action=next_action,
        report_digest=supplied_digest,
    )


def load_assessment(
    root: Path,
    *,
    run_id: str,
    topic_id: str,
    artifact_digest: str,
    q2_verified_at: str,
) -> ArticleQualityAssessment:
    """Load the required article review report for a Naver write gate."""
    path = assessment_path(root, run_id)
    try:
        payload: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"quality report is missing or invalid: {path}") from error
    return parse_assessment(
        payload,
        expected_run_id=run_id,
        expected_topic_id=topic_id,
        expected_artifact_digest=artifact_digest,
        q2_verified_at=q2_verified_at,
    )


def assessment_digest(payload: JSONMap) -> str:
    """Return the canonical digest to place in a reviewed assessment."""
    return _report_digest(payload)


__all__ = [
    "QUALITY_REPORT_VERSION",
    "RUBRIC_VERSION",
    "SCORE_MAXIMA",
    "ArticleQualityAssessment",
    "ArticleQualityFailure",
    "assessment_digest",
    "assessment_path",
    "load_assessment",
    "parse_assessment",
]

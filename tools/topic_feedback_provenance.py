from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tools.contract_types import ContractError, JSONMap
from tools.topic_feedback_config import load_rollout_config
from tools.topic_feedback_models import ArtifactKind
from tools.topic_metadata import read_snapshot
from tools.weekly_feedback_evaluation_input import load_governed_evaluation
from tools.weekly_feedback_outcome_evidence import validate_outcome_evidence_file


@dataclass(frozen=True, slots=True)
class ProvenanceRequest:
    root: Path
    relative: str
    payload: JSONMap
    cutoff: datetime


def validate_provenance_file(request: ProvenanceRequest) -> bool:
    schema_version = request.payload.get("schema_version")
    if schema_version == "creator-advisor-snapshot-v1":
        capture_id = request.payload.get("capture_id")
        as_of_date = request.payload.get("as_of_date")
        if not isinstance(capture_id, str) or not isinstance(as_of_date, str):
            raise ContractError("feedback evidence file mismatch")
        snapshot = read_snapshot(
            request.root / request.relative,
            expected_capture_id=capture_id,
            expected_as_of_date=as_of_date,
        )
        try:
            captured = datetime.fromisoformat(snapshot.captured_at)
        except ValueError as error:
            raise ContractError("captured_at must be a KST timestamp") from error
        if not snapshot.captured_at.endswith("+09:00") or captured > request.cutoff:
            raise ContractError("feedback evidence manifest is from the future")
        return True
    if schema_version == "topic-feedback-rollout-v1":
        _ = load_rollout_config(
            request.root, request.root / request.relative
        )
        return True
    if schema_version in {
        "topic-feedback-evaluation-v2",
        "topic-feedback-evaluation-v3",
    }:
        evaluation = load_governed_evaluation(
            request.root, request.root / request.relative
        )
        if evaluation.evaluation_as_of > request.cutoff:
            raise ContractError("feedback evidence manifest is from the future")
        return True
    if schema_version in {
        "topic-feedback-selection-evidence-v1",
        "topic-feedback-outcome-observation-v1",
    }:
        validate_outcome_evidence_file(
            request.root, request.root / request.relative, schema_version
        )
        return True
    if schema_version not in {item.value for item in ArtifactKind}:
        raise ContractError(
            f"unsupported topic feedback schema_version: {schema_version}"
        )
    return False


__all__ = ["ProvenanceRequest", "validate_provenance_file"]

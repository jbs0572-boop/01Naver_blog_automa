from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.topic_feedback_evaluation import evaluate_outcomes, parse_candidate_outcomes
from tools.topic_feedback_scoring_models import CandidateOutcome, EvaluationResult
from tools.topic_feedback_store_fs import open_directory, open_root, read_file
from tools.weekly_feedback_outcome_evidence import load_factual_outcome

KST: Final = timedelta(hours=9)
_FIELDS: Final = frozenset(
    {
        "schema_version",
        "baseline_score_version",
        "challenger_score_version",
        "evaluation_as_of",
        "evaluation_parameters",
        "outcomes",
        "result",
        "ndcg_delta",
        "digest",
    }
)
_PARAMETERS: Final[JSONMap] = {
    "minimum_mature_samples": 30,
    "metric": "search_inflow",
    "horizons_days": [7, 28],
    "aggregation": "median",
    "comparison": "strict_improvement",
    "validation": "publication_time_expanding_origin_v2",
}


@dataclass(frozen=True, slots=True)
class GovernedEvaluation:
    path: Path
    digest: str
    evaluation_as_of: datetime
    outcomes: tuple[CandidateOutcome, ...]
    result: EvaluationResult
    evidence_paths: tuple[Path, ...]
    actionable: bool


def _map(encoded: bytes) -> JSONMap:
    try:
        value: JSONValue = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("feedback input digest mismatch") from error
    if not isinstance(value, dict):
        raise ContractError("feedback input digest mismatch")
    return value


def _canonical(payload: JSONMap) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as error:
        raise ContractError("feedback input digest mismatch") from error


def _sha256(encoded: bytes) -> str:
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _safe_read(root: Path, path: Path) -> bytes:
    try:
        relative = path.relative_to(root) if path.is_absolute() else path
    except ValueError as error:
        raise ContractError("feedback input digest mismatch") from error
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ContractError("feedback input digest mismatch")
    descriptors: list[int] = []
    try:
        descriptor = open_root(root)
        descriptors.append(descriptor)
        for part in relative.parts[:-1]:
            descriptor = open_directory(descriptor, part, create=False)
            descriptors.append(descriptor)
        return read_file(descriptor, relative.name)
    except ContractError as error:
        raise ContractError("feedback input digest mismatch") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _time(value: JSONValue) -> datetime:
    if not isinstance(value, str) or not value.endswith("+09:00"):
        raise ContractError("feedback input digest mismatch")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContractError("feedback input digest mismatch") from error
    if parsed.utcoffset() != KST:
        raise ContractError("feedback input digest mismatch")
    return parsed


def _validate_outcome_origin(
    outcomes: tuple[CandidateOutcome, ...], evaluation_as_of: datetime
) -> None:
    identities = [item.outcome_id for item in outcomes]
    if len(identities) != len(set(identities)):
        raise ContractError("feedback input digest mismatch")
    for outcome in outcomes:
        observed = (
            _time(outcome.published_at),
            _time(outcome.prediction_recorded_at),
            _time(outcome.training_cutoff),
            _time(outcome.seven_day_observed_at),
            _time(outcome.twenty_eight_day_observed_at),
        )
        if any(value > evaluation_as_of for value in observed):
            raise ContractError("feedback input digest mismatch")


def load_governed_evaluation(root: Path, path: Path) -> GovernedEvaluation:
    encoded = _safe_read(root, path)
    payload = _map(encoded)
    unsigned = dict(payload)
    digest = unsigned.pop("digest", None)
    if (
        frozenset(payload) != _FIELDS
        or payload.get("schema_version") not in {
            "topic-feedback-evaluation-v2",
            "topic-feedback-evaluation-v3",
        }
        or payload.get("baseline_score_version") != "topic-baseline-v1"
        or payload.get("challenger_score_version") != "topic-feedback-v1"
        or payload.get("evaluation_parameters") != _PARAMETERS
        or not isinstance(digest, str)
        or digest != _sha256(_canonical(unsigned))
        or _canonical(payload) != encoded.rstrip(b"\n")
    ):
        raise ContractError("feedback input digest mismatch")
    evaluation_as_of = _time(payload.get("evaluation_as_of"))
    if payload.get("schema_version") == "topic-feedback-evaluation-v2":
        legacy_outcomes = parse_candidate_outcomes(payload.get("outcomes"))
        _validate_outcome_origin(legacy_outcomes, evaluation_as_of)
        legacy_result = evaluate_outcomes(
            legacy_outcomes,
            evaluation_as_of=evaluation_as_of,
            minimum_mature_samples=30,
        )
        if payload.get("result") != legacy_result.as_json():
            raise ContractError("feedback input digest mismatch")
        result = evaluate_outcomes(
            (), evaluation_as_of=evaluation_as_of, minimum_mature_samples=30
        )
        return GovernedEvaluation(path, digest, evaluation_as_of, (), result, (path,), False)
    raw_outcomes = payload.get("outcomes")
    if not isinstance(raw_outcomes, list):
        raise ContractError("feedback input digest mismatch")
    factual = tuple(
        load_factual_outcome(root, item)
        for item in raw_outcomes
        if isinstance(item, dict)
    )
    if len(factual) != len(raw_outcomes):
        raise ContractError("feedback input digest mismatch")
    outcomes = tuple(item.outcome for item in factual)
    _validate_outcome_origin(outcomes, evaluation_as_of)
    result = evaluate_outcomes(
        outcomes,
        evaluation_as_of=evaluation_as_of,
        minimum_mature_samples=30,
    )
    if payload.get("result") != result.as_json():
        raise ContractError("feedback input digest mismatch")
    evidence_paths = (path, *(nested for item in factual for nested in item.paths))
    empty = evaluate_outcomes(
        (), evaluation_as_of=evaluation_as_of, minimum_mature_samples=30
    )
    return GovernedEvaluation(
        path, digest, evaluation_as_of, (), empty, evidence_paths, False
    )


def latest_governed_evaluation(
    root: Path, cutoff: datetime
) -> GovernedEvaluation | None:
    directory = root / "metadata/topic-feedback-evaluations"
    if not directory.exists():
        return None
    if directory.is_symlink() or not directory.is_dir():
        raise ContractError("feedback input digest mismatch")
    candidates: list[GovernedEvaluation] = []
    for path in sorted(directory.iterdir()):
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise ContractError("feedback input digest mismatch")
        item = load_governed_evaluation(root, path)
        if item.evaluation_as_of <= cutoff:
            candidates.append(item)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            item.evaluation_as_of,
            item.actionable,
            item.path.name,
            item.digest,
        ),
    )


__all__ = [
    "GovernedEvaluation",
    "latest_governed_evaluation",
    "load_governed_evaluation",
]

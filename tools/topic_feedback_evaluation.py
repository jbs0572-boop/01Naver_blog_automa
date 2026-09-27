from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite
from statistics import median
from typing import Final

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.topic_feedback_scoring_models import CandidateOutcome, EvaluationResult

KST: Final = timedelta(hours=9)
_OUTCOME_FIELDS: Final = frozenset(
    field for field in CandidateOutcome.__dataclass_fields__
)


def _text(raw: JSONMap, field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise ContractError("evaluation outcome is invalid")
    return value


def _metric(raw: JSONMap, field: str) -> float | None:
    value = raw.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ContractError("evaluation outcome is invalid")
    return float(value)


def _outcome(raw: JSONMap) -> CandidateOutcome:
    if frozenset(raw) != _OUTCOME_FIELDS:
        raise ContractError("evaluation outcome is invalid")
    selected = raw.get("selected")
    if not isinstance(selected, bool):
        raise ContractError("evaluation outcome is invalid")
    return CandidateOutcome(
        _text(raw, "outcome_id"),
        selected,
        _text(raw, "status"),
        _metric(raw, "baseline_search_inflow_7d"),
        _metric(raw, "challenger_search_inflow_7d"),
        _metric(raw, "baseline_search_inflow_28d"),
        _metric(raw, "challenger_search_inflow_28d"),
        _text(raw, "published_at"),
        _text(raw, "prediction_recorded_at"),
        _text(raw, "training_cutoff"),
        _text(raw, "selection_input_digest"),
        _text(raw, "score_version"),
        _text(raw, "seven_day_observed_at"),
        _text(raw, "seven_day_observation_digest"),
        _text(raw, "twenty_eight_day_observed_at"),
        _text(raw, "twenty_eight_day_observation_digest"),
    )


def parse_candidate_outcomes(value: JSONValue) -> tuple[CandidateOutcome, ...]:
    if not isinstance(value, list):
        raise ContractError("evaluation outcomes must be an array")
    outcomes = tuple(_outcome(item) for item in value if isinstance(item, dict))
    if len(outcomes) != len(value):
        raise ContractError("evaluation outcome is invalid")
    return outcomes


def _time(value: str | None) -> datetime | None:
    if value is None or not value.endswith("+09:00"):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.utcoffset() == KST else None


def _digest(value: str | None) -> bool:
    if value is None or len(value) != 71 or not value.startswith("sha256:"):
        return False
    return all(character in "0123456789abcdef" for character in value[7:])


def _number(value: float | None) -> bool:
    return value is not None and isfinite(value) and value >= 0


def _known_time(value: str | None) -> datetime:
    parsed = _time(value)
    if parsed is None:
        raise ContractError("eligible evaluation outcome timestamp is invalid")
    return parsed


def _known_number(value: float | None) -> float:
    if value is None:
        raise ContractError("eligible evaluation outcome metric is missing")
    return value


def _available(outcome: CandidateOutcome, as_of: datetime) -> bool:
    published = _time(outcome.published_at)
    prediction = _time(outcome.prediction_recorded_at)
    cutoff = _time(outcome.training_cutoff)
    seven = _time(outcome.seven_day_observed_at)
    twenty_eight = _time(outcome.twenty_eight_day_observed_at)
    times = (published, prediction, cutoff, seven, twenty_eight)
    values = (
        outcome.baseline_search_inflow_7d,
        outcome.challenger_search_inflow_7d,
        outcome.baseline_search_inflow_28d,
        outcome.challenger_search_inflow_28d,
    )
    if (
        outcome.status != "mature"
        or not all(_number(value) for value in values)
        or not all(value is not None for value in times)
        or not _digest(outcome.selection_input_digest)
        or not _digest(outcome.seven_day_observation_digest)
        or not _digest(outcome.twenty_eight_day_observation_digest)
        or outcome.score_version != "topic-feedback-v1"
    ):
        return False
    assert published is not None
    assert prediction is not None
    assert cutoff is not None
    assert seven is not None
    assert twenty_eight is not None
    return (
        cutoff <= prediction < published <= as_of
        and published + timedelta(days=7)
        <= seven
        <= published + timedelta(days=7, hours=48)
        and published + timedelta(days=28)
        <= twenty_eight
        <= published + timedelta(days=28, hours=48)
        and seven <= twenty_eight <= as_of
    )


def _origins(
    outcomes: tuple[CandidateOutcome, ...],
) -> tuple[tuple[CandidateOutcome, tuple[CandidateOutcome, ...]], ...]:
    folds: list[tuple[CandidateOutcome, tuple[CandidateOutcome, ...]]] = []
    for validation in outcomes:
        origin = _time(validation.prediction_recorded_at)
        if origin is None:
            continue
        training = tuple(
            item
            for item in outcomes
            if item.outcome_id != validation.outcome_id
            and _known_time(item.published_at) < _known_time(validation.published_at)
            and _known_time(item.twenty_eight_day_observed_at) <= origin
        )
        if training:
            folds.append((validation, training))
    return tuple(folds)


def evaluate_outcomes(
    outcomes: tuple[CandidateOutcome, ...],
    *,
    evaluation_as_of: datetime,
    minimum_mature_samples: int = 30,
) -> EvaluationResult:
    if evaluation_as_of.utcoffset() != KST:
        raise ContractError("evaluation_as_of must be a KST timestamp")
    selected = tuple(item for item in outcomes if item.selected)
    identities = [item.outcome_id for item in selected]
    duplicates = len(identities) - len(set(identities))
    eligible = tuple(
        sorted(
            (item for item in selected if _available(item, evaluation_as_of)),
            key=lambda item: (item.published_at, item.outcome_id),
        )
    )
    folds = _origins(eligible)
    validation = tuple(item for item, _ in folds)
    training_ids = {item.outcome_id for _, train in folds for item in train}
    missing = len(selected) - len(eligible)
    improvement_7d = (
        median(_known_number(item.challenger_search_inflow_7d) for item in validation)
        - median(_known_number(item.baseline_search_inflow_7d) for item in validation)
        if validation
        else None
    )
    improvement_28d = (
        median(_known_number(item.challenger_search_inflow_28d) for item in validation)
        - median(_known_number(item.baseline_search_inflow_28d) for item in validation)
        if validation
        else None
    )
    valid = (
        len(eligible) >= minimum_mature_samples
        and bool(validation)
        and duplicates == 0
        and missing == 0
        and improvement_7d is not None
        and improvement_7d > 0
        and improvement_28d is not None
        and improvement_28d > 0
    )
    return EvaluationResult(
        "topic-feedback-evaluation-candidate-v2",
        evaluation_as_of.isoformat(),
        valid,
        len(eligible),
        len(training_ids),
        len(validation),
        improvement_7d,
        improvement_28d,
        missing / len(selected) if selected else 0.0,
        duplicates / len(selected) if selected else 0.0,
        len(folds),
    )


__all__ = ["evaluate_outcomes", "parse_candidate_outcomes"]

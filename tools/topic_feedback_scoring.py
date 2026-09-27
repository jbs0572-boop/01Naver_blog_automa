from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Final

from tools.contract_types import ContractError, JSONMap
from tools.topic_feedback_evaluation import evaluate_outcomes
from tools.topic_feedback_scoring_models import (
    AuxiliarySignal,
    EnrichedTopicScore,
    FeatureValue,
    RankingResult,
)
from tools.topic_metadata import CreatorAdvisorSnapshot, normalize_keyword
from tools.topic_scoring import TopicObservation, rank_topics

FRESH_DAYS: Final = 14
BASELINE_WEIGHT: Final = 0.5
FEATURE_WEIGHT: Final = 0.5
TRUSTED_CONFIDENCE: Final = frozenset({"A", "B"})


def _fresh(signal: AuxiliarySignal, as_of: date) -> bool:
    try:
        captured = date.fromisoformat(signal.as_of_date)
    except ValueError as error:
        raise ContractError("auxiliary signal as_of_date is invalid") from error
    age = (as_of - captured).days
    return 0 <= age <= FRESH_DAYS


def _normalized_features(
    signals: tuple[AuxiliarySignal, ...],
) -> dict[tuple[str, str, str], float]:
    identities = {
        (normalize_keyword(item.keyword), item.source_id, item.unit) for item in signals
    }
    if len(identities) != len(signals):
        raise ContractError("duplicate auxiliary signal identity")
    by_unit: dict[tuple[str, str], list[float]] = {}
    for signal in signals:
        if not isfinite(signal.value):
            raise ContractError("auxiliary signal value must be finite")
        by_unit.setdefault((signal.source_id, signal.unit), []).append(signal.value)
    result: dict[tuple[str, str, str], float] = {}
    for signal in signals:
        values = by_unit[(signal.source_id, signal.unit)]
        low, high = min(values), max(values)
        result[(normalize_keyword(signal.keyword), signal.source_id, signal.unit)] = (
            0.5 if high == low else (signal.value - low) / (high - low)
        )
    return result


def rank_creator_candidates(
    observations: tuple[TopicObservation, ...],
    signals: tuple[AuxiliarySignal, ...],
    as_of: date,
) -> RankingResult:
    baseline = rank_topics(observations)
    candidate_by_key = {normalize_keyword(item.keyword): item for item in observations}
    if len(candidate_by_key) != len(observations):
        raise ContractError(
            "Creator Advisor candidates normalize to duplicate keywords"
        )
    eligible: list[AuxiliarySignal] = []
    excluded: list[str] = []
    for signal in signals:
        key = normalize_keyword(signal.keyword)
        if key not in candidate_by_key:
            excluded.append(f"{signal.keyword}:not_a_creator_candidate")
        elif signal.source_confidence not in TRUSTED_CONFIDENCE:
            excluded.append(f"{signal.keyword}:untrusted_source_confidence")
        elif _fresh(signal, as_of):
            eligible.append(signal)
        else:
            excluded.append(f"{signal.keyword}:stale_signal")
    group_counts: dict[tuple[str, str], int] = {}
    for signal in eligible:
        group = (signal.source_id, signal.unit)
        group_counts[group] = group_counts.get(group, 0) + 1
    eligible = [
        signal
        for signal in eligible
        if group_counts[(signal.source_id, signal.unit)] >= 2
    ]
    if not eligible:
        shadow = tuple(
            EnrichedTopicScore(
                item.keyword,
                normalize_keyword(item.keyword),
                item.score,
                item.score,
                candidate_by_key[normalize_keyword(item.keyword)].candidate_rank,
                candidate_by_key[normalize_keyword(item.keyword)].trend_index,
                (),
                0.0,
            )
            for item in baseline
        )
        return RankingResult(baseline, shadow, tuple(sorted(excluded)))
    normalized = _normalized_features(tuple(eligible))
    baseline_by_key = {normalize_keyword(item.keyword): item.score for item in baseline}
    enriched: list[EnrichedTopicScore] = []
    for key, observation in candidate_by_key.items():
        matched = tuple(
            signal for signal in eligible if normalize_keyword(signal.keyword) == key
        )
        features = tuple(
            FeatureValue(
                signal.source_id,
                signal.source_confidence,
                signal.unit,
                signal.value,
                normalized[(key, signal.source_id, signal.unit)],
            )
            for signal in sorted(matched, key=lambda item: (item.unit, item.source_id))
        )
        baseline_score = baseline_by_key[key]
        if features:
            auxiliary_weight = FEATURE_WEIGHT * len(features)
            numerator = BASELINE_WEIGHT * baseline_score + FEATURE_WEIGHT * sum(
                item.normalized_value for item in features
            )
            score = numerator / (BASELINE_WEIGHT + auxiliary_weight)
            applied_weight = auxiliary_weight / (BASELINE_WEIGHT + auxiliary_weight)
        else:
            score = baseline_score
            applied_weight = 0.0
        enriched.append(
            EnrichedTopicScore(
                observation.keyword,
                key,
                score,
                baseline_score,
                observation.candidate_rank,
                observation.trend_index,
                features,
                applied_weight,
            )
        )
    ranked = tuple(
        sorted(
            enriched,
            key=lambda item: (
                -item.score,
                -(item.trend_index or 0.0),
                item.creator_rank,
                item.keyword,
            ),
        )
    )
    return RankingResult(baseline, ranked, tuple(sorted(excluded)))


def rank_snapshot(
    snapshot: CreatorAdvisorSnapshot,
    excluded_keywords: tuple[str, ...],
    signals: tuple[AuxiliarySignal, ...] = (),
) -> RankingResult:
    excluded = {normalize_keyword(value) for value in excluded_keywords}
    candidates = tuple(
        candidate
        for candidate in snapshot.candidates
        if normalize_keyword(candidate.keyword) not in excluded
    )
    observations = tuple(
        TopicObservation(candidate.keyword, candidate.rank, candidate.trend_index, ())
        for candidate in candidates
    )
    return rank_creator_candidates(
        observations, signals, date.fromisoformat(snapshot.as_of_date)
    )


def scoring_rule_payload() -> JSONMap:
    return {
        "schema_version": "topic-feedback-scoring-rules-v1",
        "baseline_weight": BASELINE_WEIGHT,
        "feature_weight": FEATURE_WEIGHT,
        "fresh_days": FRESH_DAYS,
        "normalization": "per_source_unit_minmax_neutral_constant",
        "missing": "renormalize_available_weights",
    }


__all__ = [
    "evaluate_outcomes",
    "rank_creator_candidates",
    "rank_snapshot",
    "scoring_rule_payload",
]

from __future__ import annotations

from dataclasses import dataclass

from tools.contract_types import JSONMap
from tools.topic_scoring import TopicScore


@dataclass(frozen=True, slots=True)
class AuxiliarySignal:
    keyword: str
    source_id: str
    source_confidence: str
    as_of_date: str
    unit: str
    value: float


@dataclass(frozen=True, slots=True)
class FeatureValue:
    source_id: str
    source_confidence: str
    unit: str
    raw_value: float
    normalized_value: float


@dataclass(frozen=True, slots=True)
class EnrichedTopicScore:
    keyword: str
    normalized_keyword: str
    score: float
    baseline_score: float
    creator_rank: int
    trend_index: float | None
    features: tuple[FeatureValue, ...]
    applied_weight: float


@dataclass(frozen=True, slots=True)
class RankingResult:
    baseline: tuple[TopicScore, ...]
    shadow: tuple[EnrichedTopicScore, ...]
    excluded_signals: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateOutcome:
    outcome_id: str
    selected: bool
    status: str
    baseline_search_inflow_7d: float | None
    challenger_search_inflow_7d: float | None
    baseline_search_inflow_28d: float | None
    challenger_search_inflow_28d: float | None
    published_at: str
    prediction_recorded_at: str | None = None
    training_cutoff: str | None = None
    selection_input_digest: str | None = None
    score_version: str | None = None
    seven_day_observed_at: str | None = None
    seven_day_observation_digest: str | None = None
    twenty_eight_day_observed_at: str | None = None
    twenty_eight_day_observation_digest: str | None = None


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    schema_version: str
    evaluation_as_of: str
    promotion_eligible: bool
    mature_selected_outcomes: int
    training_outcomes: int
    validation_outcomes: int
    search_inflow_7d_improvement: float | None
    search_inflow_28d_improvement: float | None
    missing_rate: float
    duplicate_rate: float
    rolling_origin_folds: int
    ndcg_usage: str = "diagnostic_only"

    def as_json(self) -> JSONMap:
        return {
            "schema_version": self.schema_version,
            "evaluation_as_of": self.evaluation_as_of,
            "promotion_eligible": self.promotion_eligible,
            "mature_selected_outcomes": self.mature_selected_outcomes,
            "training_outcomes": self.training_outcomes,
            "validation_outcomes": self.validation_outcomes,
            "search_inflow_7d_improvement": self.search_inflow_7d_improvement,
            "search_inflow_28d_improvement": self.search_inflow_28d_improvement,
            "missing_rate": self.missing_rate,
            "duplicate_rate": self.duplicate_rate,
            "rolling_origin_folds": self.rolling_origin_folds,
            "ndcg_usage": self.ndcg_usage,
        }


__all__ = [
    "AuxiliarySignal",
    "CandidateOutcome",
    "EnrichedTopicScore",
    "EvaluationResult",
    "FeatureValue",
    "RankingResult",
]

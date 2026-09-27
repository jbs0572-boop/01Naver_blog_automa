from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from tools.contract_types import JSONMap
from tools.topic_feedback_config import RolloutPin
from tools.topic_feedback_evaluation import evaluate_outcomes
from tools.topic_feedback_manifest_reader import load_feedback_path
from tools.topic_feedback_scoring import rank_creator_candidates
from tools.topic_feedback_scoring_models import (
    CandidateOutcome,
    EnrichedTopicScore,
    EvaluationResult,
    FeatureValue,
)
from tools.topic_metadata import CreatorAdvisorSnapshot
from tools.topic_scoring import TopicObservation, TopicScore


@dataclass(frozen=True, slots=True)
class WeeklyRankingRequest:
    root: Path
    snapshot: CreatorAdvisorSnapshot
    evidence_manifests: tuple[Path, ...]
    rollout: RolloutPin
    evaluation_as_of: datetime
    outcomes: tuple[CandidateOutcome, ...] | None = None


@dataclass(frozen=True, slots=True)
class WeeklyRankingEntry:
    keyword: str
    score: float
    baseline_score: float
    creator_rank: int
    trend_index: float | None
    features: tuple[FeatureValue, ...]
    applied_weight: float

    def as_json(self) -> JSONMap:
        return {
            "keyword": self.keyword,
            "score": self.score,
            "baseline_score": self.baseline_score,
            "creator_rank": self.creator_rank,
            "trend_index": self.trend_index,
            "features": [
                {
                    "source_id": item.source_id,
                    "source_confidence": item.source_confidence,
                    "unit": item.unit,
                    "raw_value": item.raw_value,
                    "normalized_value": item.normalized_value,
                }
                for item in self.features
            ],
            "applied_weight": self.applied_weight,
        }


@dataclass(frozen=True, slots=True)
class WeeklyRankingResult:
    baseline: tuple[WeeklyRankingEntry, ...]
    shadow: tuple[WeeklyRankingEntry, ...]
    selected: tuple[WeeklyRankingEntry, ...]
    selection_mode: Literal["baseline", "shadow", "active"]
    excluded_signals: tuple[str, ...]
    evaluation: EvaluationResult
    challenger_verdict: Literal[
        "promotion_eligible", "not_eligible", "insufficient_evidence"
    ]
    evidence_digests: tuple[str, ...]

    def as_json(self) -> JSONMap:
        return {
            "schema_version": "weekly-feedback-ranking-v1",
            "selection_mode": self.selection_mode,
            "baseline": [item.as_json() for item in self.baseline],
            "shadow": [item.as_json() for item in self.shadow],
            "selected": [item.as_json() for item in self.selected],
            "excluded_signals": list(self.excluded_signals),
            "evaluation": self.evaluation.as_json(),
            "challenger_verdict": self.challenger_verdict,
            "evidence_digests": list(self.evidence_digests),
        }


def _baseline_entry(
    item: TopicScore, observations: dict[str, TopicObservation]
) -> WeeklyRankingEntry:
    observation = observations[item.keyword]
    return WeeklyRankingEntry(
        item.keyword,
        item.score,
        item.score,
        observation.candidate_rank,
        observation.trend_index,
        (),
        0.0,
    )


def _shadow_entry(item: EnrichedTopicScore) -> WeeklyRankingEntry:
    return WeeklyRankingEntry(
        item.keyword,
        item.score,
        item.baseline_score,
        item.creator_rank,
        item.trend_index,
        item.features,
        item.applied_weight,
    )


def build_weekly_ranking(request: WeeklyRankingRequest) -> WeeklyRankingResult:
    observations = tuple(
        TopicObservation(item.keyword, item.rank, item.trend_index, ())
        for item in request.snapshot.candidates
    )
    enabled_sources = tuple(
        item.source_id for item in request.rollout.source_modes if item.enabled
    )
    evidence = tuple(
        load_feedback_path(
            request.root,
            path,
            request.evaluation_as_of.isoformat(),
            enabled_sources,
        )
        for path in request.evidence_manifests
    )
    ranking = rank_creator_candidates(
        observations,
        tuple(signal for item in evidence for signal in item.signals),
        request.evaluation_as_of.date(),
    )
    observation_by_keyword = {item.keyword: item for item in observations}
    baseline = tuple(
        _baseline_entry(item, observation_by_keyword) for item in ranking.baseline
    )
    shadow = tuple(_shadow_entry(item) for item in ranking.shadow)
    if request.rollout.selection_mutation:
        selected = shadow
        selection_mode: Literal["baseline", "shadow", "active"] = "active"
    else:
        selected = baseline
        selection_mode = "shadow" if request.rollout.feedback_enabled else "baseline"
    evaluation = evaluate_outcomes(
        () if request.outcomes is None else request.outcomes,
        evaluation_as_of=request.evaluation_as_of,
        minimum_mature_samples=request.rollout.minimum_mature_samples,
    )
    if evaluation.promotion_eligible:
        verdict: Literal[
            "promotion_eligible", "not_eligible", "insufficient_evidence"
        ] = "promotion_eligible"
    elif evaluation.mature_selected_outcomes < request.rollout.minimum_mature_samples:
        verdict = "insufficient_evidence"
    else:
        verdict = "not_eligible"
    return WeeklyRankingResult(
        baseline,
        shadow,
        selected,
        selection_mode,
        ranking.excluded_signals,
        evaluation,
        verdict,
        tuple(item.digest for item in evidence),
    )


__all__ = [
    "WeeklyRankingRequest",
    "WeeklyRankingResult",
    "build_weekly_ranking",
]

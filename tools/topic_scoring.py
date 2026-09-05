from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import median


@dataclass(frozen=True, slots=True)
class TopicObservation:
    keyword: str
    candidate_rank: int
    trend_index: float | None
    history: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class TopicScore:
    keyword: str
    score: float
    stage: str
    surge: float
    momentum: float
    persistence: float


def rank_topics(observations: tuple[TopicObservation, ...]) -> tuple[TopicScore, ...]:
    eligible = tuple(item for item in observations if item.keyword.strip())
    if not eligible:
        return ()
    if not any(item.history for item in eligible):
        return tuple(
            TopicScore(
                item.keyword,
                1.0 / max(item.candidate_rank, 1),
                "cold-start",
                0.0,
                0.0,
                0.0,
            )
            for item in sorted(eligible, key=lambda value: value.candidate_rank)
        )
    scored = tuple(_score(item) for item in eligible)
    return tuple(sorted(scored, key=lambda item: (-item.score, item.keyword)))


def _score(item: TopicObservation) -> TopicScore:
    history = tuple(value for value in item.history if isfinite(value))
    current = item.trend_index if item.trend_index is not None else (history[-1] if history else 0.0)
    baseline = median(history[:-1] or history or (current,))
    scale = median(tuple(abs(value - baseline) for value in history) or (1.0,)) or 1.0
    surge = max(0.0, (current - baseline) / scale)
    momentum = max(-1.0, min(1.0, (history[-1] - history[0]) / max(abs(history[0]), 1.0))) if len(history) > 1 else 0.0
    persistence = sum(value >= baseline for value in history) / len(history)
    rank_score = 1.0 / max(item.candidate_rank, 1)
    score = 0.35 * min(surge / 5.0, 1.0) + 0.25 * ((momentum + 1.0) / 2.0) + 0.25 * persistence + 0.15 * rank_score
    return TopicScore(item.keyword, score, "historical", surge, momentum, persistence)


__all__ = ["TopicObservation", "TopicScore", "rank_topics"]

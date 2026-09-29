from __future__ import annotations

import json
import os
import stat
from datetime import date, datetime
from pathlib import Path

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.topic_feedback_config import load_rollout_config
from tools.topic_feedback_evaluation import evaluate_outcomes, parse_candidate_outcomes
from tools.topic_feedback_manifest_reader import load_feedback_path
from tools.topic_feedback_scoring import rank_creator_candidates
from tools.topic_feedback_scoring_models import (
    EnrichedTopicScore,
)
from tools.topic_metadata import read_snapshot
from tools.topic_scoring import TopicObservation, TopicScore


def _read_json(path: Path) -> JSONValue:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ContractError("scoring input is unreadable or unsafe") from error
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ContractError("scoring input is unreadable or unsafe")
    try:
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("scoring input is invalid JSON") from error


def _text(raw: JSONMap, field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise ContractError("scoring input field is invalid")
    return value


def _topic_score(item: TopicScore) -> JSONMap:
    return {
        "keyword": item.keyword,
        "score": item.score,
        "stage": item.stage,
        "surge": item.surge,
        "momentum": item.momentum,
        "persistence": item.persistence,
    }


def _enriched_score(item: EnrichedTopicScore) -> JSONMap:
    return {
        "keyword": item.keyword,
        "normalized_keyword": item.normalized_keyword,
        "score": item.score,
        "baseline_score": item.baseline_score,
        "creator_rank": item.creator_rank,
        "trend_index": item.trend_index,
        "applied_weight": item.applied_weight,
        "features": [
            {
                "source_id": feature.source_id,
                "source_confidence": feature.source_confidence,
                "unit": feature.unit,
                "raw_value": feature.raw_value,
                "normalized_value": feature.normalized_value,
            }
            for feature in item.features
        ],
    }


def rank_payload(
    root: Path, snapshot_path: Path, signals_path: Path | None, as_of: str
) -> JSONMap:
    try:
        as_of_date = date.fromisoformat(as_of)
    except ValueError as error:
        raise ContractError("ranking as_of date is invalid") from error
    raw = _read_json(snapshot_path)
    if not isinstance(raw, dict):
        raise ContractError("Creator Advisor snapshot must be an object")
    snapshot = read_snapshot(
        snapshot_path,
        expected_capture_id=_text(raw, "capture_id"),
        expected_as_of_date=_text(raw, "as_of_date"),
    )
    observations = tuple(
        TopicObservation(item.keyword, item.rank, item.trend_index, ())
        for item in snapshot.candidates
    )
    rollout = load_rollout_config(root, root / "config/topic-feedback-rollout.json")
    enabled_sources = tuple(
        item.source_id for item in rollout.source_modes if item.enabled
    )
    signals = (
        ()
        if signals_path is None
        else load_feedback_path(
            root,
            signals_path,
            f"{as_of}T23:59:59+09:00",
            enabled_sources,
        ).signals
    )
    ranking = rank_creator_candidates(observations, signals, as_of_date)
    active = ranking.shadow if rollout.selection_mutation else ranking.baseline
    selection_mode = (
        "active"
        if rollout.selection_mutation
        else "shadow"
        if rollout.feedback_enabled
        else "baseline"
    )
    return {
        "schema_version": "topic-feedback-ranking-v1",
        "score_version": rollout.active_score_version,
        "selection_mode": selection_mode,
        "resolved_keyword": active[0].keyword if active else None,
        "baseline": [_topic_score(item) for item in ranking.baseline],
        "shadow": [_enriched_score(item) for item in ranking.shadow],
        "excluded_signals": list(ranking.excluded_signals),
    }


def evaluate_payload(path: Path) -> JSONMap:
    value = _read_json(path)
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "topic-feedback-evaluation-input-v2"
    ):
        raise ContractError("evaluation input is invalid")
    outcomes = parse_candidate_outcomes(value.get("outcomes"))
    evaluation_as_of = _text(value, "evaluation_as_of")
    try:
        parsed_as_of = datetime.fromisoformat(evaluation_as_of)
    except ValueError as error:
        raise ContractError("evaluation_as_of must be a KST timestamp") from error
    return evaluate_outcomes(outcomes, evaluation_as_of=parsed_as_of).as_json()


def canonical_json(value: JSONMap) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


__all__ = [
    "canonical_json",
    "evaluate_payload",
    "rank_payload",
]

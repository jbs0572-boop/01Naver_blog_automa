from __future__ import annotations

from datetime import date
from typing import Literal

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.topic_feedback_scoring import rank_creator_candidates
from tools.topic_feedback_scoring_models import AuxiliarySignal
from tools.topic_metadata import (
    CreatorAdvisorCandidate,
    CreatorAdvisorSnapshot,
    normalize_keyword,
)
from tools.topic_scoring import TopicObservation
from tools.topic_selection_models import (
    DECISION_SCHEMA_VERSION,
    SELECTION_POLICY_VERSION,
    CandidateExclusion,
    TopicSelectionDecision,
    TopicSelectionHistory,
    decision_digest,
)


def _category(candidate: CreatorAdvisorCandidate) -> str | None:
    return candidate.category_key.strip() if candidate.category_key else None


def _candidate_id(candidate: CreatorAdvisorCandidate) -> str:
    return candidate.candidate_id or decision_digest(
        [candidate.category_key, candidate.category_rank, candidate.keyword]
    )


def _duplicate_keys(candidate: CreatorAdvisorCandidate) -> frozenset[str]:
    values = (candidate.keyword, candidate.duplicate_key, *candidate.aliases)
    return frozenset(normalize_keyword(value) for value in values if value)


def _eligible_candidates(
    snapshot: CreatorAdvisorSnapshot, excluded_keys: set[str]
) -> tuple[tuple[CreatorAdvisorCandidate, ...], tuple[CandidateExclusion, ...]]:
    identity_counts: dict[str, int] = {}
    for candidate in snapshot.candidates:
        for identity in _duplicate_keys(candidate):
            identity_counts[identity] = identity_counts.get(identity, 0) + 1
    eligible: list[CreatorAdvisorCandidate] = []
    exclusions: list[CandidateExclusion] = []
    for candidate in snapshot.candidates:
        identities = _duplicate_keys(candidate)
        if _category(candidate) is None or candidate.category_rank is None:
            exclusions.append(CandidateExclusion(candidate.keyword, "ambiguous_category"))
        elif identities & excluded_keys:
            reason = (
                "normalized_keyword_duplicate"
                if normalize_keyword(candidate.keyword) in excluded_keys
                else "explicit_alias_duplicate"
            )
            exclusions.append(CandidateExclusion(candidate.keyword, reason))
        elif any(identity_counts[identity] > 1 for identity in identities):
            exclusions.append(CandidateExclusion(candidate.keyword, "snapshot_duplicate"))
        else:
            eligible.append(candidate)
    return tuple(eligible), tuple(exclusions)


def _category_winners(
    candidates: tuple[CreatorAdvisorCandidate, ...],
    signals: tuple[AuxiliarySignal, ...],
    selection_mode: Literal["baseline", "shadow", "active"],
    as_of_date: str,
) -> dict[str, CreatorAdvisorCandidate]:
    grouped: dict[str, list[CreatorAdvisorCandidate]] = {}
    for candidate in candidates:
        category = _category(candidate)
        assert category is not None
        grouped.setdefault(category, []).append(candidate)
    winners: dict[str, CreatorAdvisorCandidate] = {}
    for category, category_candidates in grouped.items():
        if selection_mode == "active":
            observations = tuple(
                TopicObservation(
                    item.keyword, item.category_rank or item.rank, item.trend_index, ()
                )
                for item in category_candidates
            )
            ranking = rank_creator_candidates(
                observations, signals, date.fromisoformat(as_of_date)
            )
            selected_key = normalize_keyword(ranking.shadow[0].keyword)
            winners[category] = next(
                item
                for item in category_candidates
                if normalize_keyword(item.keyword) == selected_key
            )
        else:
            winners[category] = min(
                category_candidates,
                key=lambda item: (
                    item.category_rank or item.rank,
                    -(item.trend_index if item.trend_index is not None else float("-inf")),
                    normalize_keyword(item.keyword),
                ),
            )
    return winners


def decide_topic(
    snapshot: CreatorAdvisorSnapshot,
    excluded_keywords: tuple[str, ...],
    excluded_aliases: tuple[str, ...],
    history: tuple[TopicSelectionHistory, ...],
    *,
    run_id: str = "unreserved",
    snapshot_path: str = "",
    snapshot_digest: str = "",
    created_at: str = "",
    signals: tuple[AuxiliarySignal, ...] = (),
    selection_mode: Literal["baseline", "shadow", "active"] = "baseline",
    score_version: str | None = None,
    score_config_digest: str | None = None,
    feedback_manifest_digest: str | None = None,
) -> TopicSelectionDecision:
    excluded_keys = {
        normalize_keyword(value) for value in (*excluded_keywords, *excluded_aliases)
    }
    eligible, exclusions = _eligible_candidates(snapshot, excluded_keys)
    if not eligible:
        raise ContractError("Creator Advisor snapshot has no eligible candidates")
    winners = _category_winners(eligible, signals, selection_mode, snapshot.as_of_date)
    accepted_history = tuple(
        item
        for item in history
        if item.completed and item.provenance == "formal_selection_decision" and item.category
    )
    counts = {
        category: sum(item.category == category for item in accepted_history)
        for category in winners
    }
    last_selected = {
        category: max(
            (item.selected_at for item in accepted_history if item.category == category),
            default=None,
        )
        for category in winners
    }
    selected_category = min(
        winners,
        key=lambda category: (
            counts[category],
            last_selected[category] is not None,
            last_selected[category] or "",
            normalize_keyword(category),
        ),
    )
    selected = winners[selected_category]
    exclusion_values: list[JSONValue] = [item.as_json() for item in exclusions]
    history_values: list[JSONValue] = [
        {
            "run_id": item.run_id,
            "category": item.category,
            "selected_at": item.selected_at,
            "provenance": item.provenance,
            "completed": item.completed,
        }
        for item in sorted(accepted_history, key=lambda value: value.run_id)
    ]
    unsigned: JSONMap = {
        "schema_version": DECISION_SCHEMA_VERSION,
        "run_id": run_id,
        "as_of_date": snapshot.as_of_date,
        "selected_category": selected_category,
        "selected_keyword": selected.keyword,
        "selected_candidate_id": _candidate_id(selected),
        "snapshot_path": snapshot_path,
        "snapshot_sha256": snapshot_digest,
        "selection_policy_version": SELECTION_POLICY_VERSION,
        "ranking_policy_version": "creator-advisor-baseline-v1",
        "score_version": score_version,
        "score_config_digest": score_config_digest,
        "feedback_manifest_digest": feedback_manifest_digest,
        "exclusions": exclusion_values,
        "exclusions_digest": decision_digest(exclusion_values),
        "category_history_digest": decision_digest(history_values),
        "category_selection_count": counts[selected_category],
        "category_last_selected_at": last_selected[selected_category],
        "selected_category_rank": selected.category_rank or selected.rank,
        "selected_trend_index": selected.trend_index,
        "created_at": created_at,
    }
    return TopicSelectionDecision(
        run_id, snapshot.as_of_date, selected_category, selected.keyword,
        _candidate_id(selected), snapshot_path, snapshot_digest,
        SELECTION_POLICY_VERSION, "creator-advisor-baseline-v1", score_version,
        score_config_digest, feedback_manifest_digest, exclusions,
        str(unsigned["exclusions_digest"]), str(unsigned["category_history_digest"]),
        counts[selected_category], last_selected[selected_category],
        selected.category_rank or selected.rank, selected.trend_index, created_at,
        decision_digest(unsigned),
    )


__all__ = ["decide_topic"]

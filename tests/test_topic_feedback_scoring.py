from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from tools.contract_types import ContractError
from tools.topic_feedback_scoring import evaluate_outcomes, rank_creator_candidates
from tools.topic_feedback_scoring_models import AuxiliarySignal, CandidateOutcome
from tools.topic_scoring import TopicObservation, rank_topics


def _observations() -> tuple[TopicObservation, ...]:
    return (
        TopicObservation("원문 Alpha", 1, 70.0, (50.0, 60.0, 70.0)),
        TopicObservation("원문 Beta", 2, 60.0, (55.0, 58.0, 60.0)),
    )


def test_rank_preserves_baseline_when_auxiliary_signals_are_empty() -> None:
    # Given: Creator Advisor candidates and no auxiliary observations.
    observations = _observations()

    # When: feedback scoring runs in shadow mode.
    ranked = rank_creator_candidates(observations, (), date(2026, 9, 9))

    # Then: the baseline order and original keywords are preserved exactly.
    assert [item.keyword for item in ranked.baseline] == [
        item.keyword for item in rank_topics(observations)
    ]
    assert [item.keyword for item in ranked.shadow] == [
        item.keyword for item in ranked.baseline
    ]


def test_rank_weak_single_candidate_signal_preserves_baseline() -> None:
    # Given: one fresh candidate signal, one stale signal, and one related keyword.
    signals = (
        AuxiliarySignal(
            "원문 Beta", "naver-datalab", "A", "2026-09-08", "relative_index", 100.0
        ),
        AuxiliarySignal(
            "원문 Alpha", "naver-datalab", "A", "2026-08-01", "relative_index", 999.0
        ),
        AuxiliarySignal(
            "관련어 Gamma", "naver-datalab", "A", "2026-09-08", "relative_index", 999.0
        ),
    )

    # When: the enriched rank is computed at a fixed date.
    ranked = rank_creator_candidates(_observations(), signals, date(2026, 9, 9))

    # Then: the isolated signal cannot invent a normalized advantage.
    assert ranked.excluded_signals == (
        "관련어 Gamma:not_a_creator_candidate",
        "원문 Alpha:stale_signal",
    )
    alpha = next(item for item in ranked.shadow if item.keyword == "원문 Alpha")
    assert alpha.features == ()
    assert alpha.applied_weight == 0.0
    beta = next(item for item in ranked.shadow if item.keyword == "원문 Beta")
    assert beta.features == ()
    assert [item.keyword for item in ranked.shadow] == [
        item.keyword for item in ranked.baseline
    ]


def test_rank_deterministic_tie_break_uses_trend_rank_then_original_keyword() -> None:
    # Given: tied baseline and auxiliary scores with differing trend and Creator rank.
    observations = (
        TopicObservation("나", 2, 80.0, (80.0,)),
        TopicObservation("가", 1, 80.0, (80.0,)),
    )

    # When: scoring is repeated.
    first = rank_creator_candidates(observations, (), date(2026, 9, 9))
    second = rank_creator_candidates(observations, (), date(2026, 9, 9))

    # Then: the stable fallback order is Creator rank, then original keyword.
    assert [item.keyword for item in first.shadow] == ["가", "나"]
    assert first == second


def test_strong_trusted_signal_reorders_only_shadow_creator_candidates() -> None:
    # Given: two near-tied Creator candidates and a strong trusted signal for rank two.
    observations = (
        TopicObservation("첫 원문", 1, None, ()),
        TopicObservation("둘째 원문", 2, None, ()),
    )
    signals = (
        AuxiliarySignal(
            "첫 원문", "naver-datalab", "A", "2026-09-09", "relative_index", 0.0
        ),
        AuxiliarySignal(
            "둘째 원문", "naver-datalab", "A", "2026-09-09", "relative_index", 100.0
        ),
    )

    # When: the same immutable input is scored for baseline and challenger comparison.
    ranked = rank_creator_candidates(observations, signals, date(2026, 9, 9))

    # Then: baseline stays byte-ordered while shadow reorders within the original set.
    assert [item.keyword for item in ranked.baseline] == ["첫 원문", "둘째 원문"]
    assert [item.keyword for item in ranked.shadow] == ["둘째 원문", "첫 원문"]
    assert {item.keyword for item in ranked.shadow} == {"첫 원문", "둘째 원문"}


def test_duplicate_normalized_source_unit_signal_is_rejected() -> None:
    # Given: two values claim the same normalized candidate, source, and unit.
    signals = (
        AuxiliarySignal("원문 Alpha", "naver-datalab", "A", "2026-09-09", "index", 1.0),
        AuxiliarySignal(
            " 원문  Alpha ", "naver-datalab", "A", "2026-09-09", "index", 2.0
        ),
    )

    # When/Then: the scorer refuses order-dependent overwrite semantics.
    with pytest.raises(ContractError, match="duplicate auxiliary signal identity"):
        _ = rank_creator_candidates(_observations(), signals, date(2026, 9, 9))


def test_evaluation_requires_thirty_unique_selected_mature_outcomes() -> None:
    # Given: only 29 leakage-free selected mature outcomes.
    first = datetime(2023, 1, 1, 12, tzinfo=timezone(timedelta(hours=9)))
    outcomes = tuple(
        CandidateOutcome(
            f"OUT-{index:02d}",
            True,
            "mature",
            10.0,
            11.0,
            20.0,
            22.0,
            (first + timedelta(days=35 * index)).isoformat(),
            (first + timedelta(days=35 * index, hours=-1)).isoformat(),
            (first + timedelta(days=35 * index, hours=-2)).isoformat(),
            "sha256:" + f"{index:064x}",
            "topic-feedback-v1",
            (first + timedelta(days=35 * index + 7, hours=1)).isoformat(),
            "sha256:" + f"{index + 100:064x}",
            (first + timedelta(days=35 * index + 28, hours=1)).isoformat(),
            "sha256:" + f"{index + 200:064x}",
        )
        for index in range(29)
    )

    # When: promotion is evaluated.
    result = evaluate_outcomes(
        outcomes,
        evaluation_as_of=first + timedelta(days=35 * 28 + 30),
        minimum_mature_samples=30,
    )

    # Then: selection mutation remains blocked and NDCG is diagnostic-only.
    assert result.promotion_eligible is False
    assert result.mature_selected_outcomes == 29
    assert result.ndcg_usage == "diagnostic_only"


def test_evaluation_uses_only_available_publication_time_rolling_origins() -> None:
    # Given: 31 chronologically spaced outcomes with selection-time predictions and
    # horizon observations available before every later origin.
    kst = timezone(timedelta(hours=9))
    first = datetime(2023, 1, 1, 12, tzinfo=kst)
    outcomes = tuple(
        CandidateOutcome(
            f"OUT-{index:02d}",
            True,
            "mature",
            10.0,
            12.0,
            20.0,
            24.0,
            (first + timedelta(days=35 * index)).isoformat(),
            (first + timedelta(days=35 * index, hours=-1)).isoformat(),
            (first + timedelta(days=35 * index, hours=-2)).isoformat(),
            "sha256:" + f"{index:064x}",
            "topic-feedback-v1",
            (first + timedelta(days=35 * index + 7, hours=1)).isoformat(),
            "sha256:" + f"{index + 100:064x}",
            (first + timedelta(days=35 * index + 28, hours=1)).isoformat(),
            "sha256:" + f"{index + 200:064x}",
        )
        for index in range(31)
    )

    # When: the rolling-origin evaluation is computed.
    result = evaluate_outcomes(
        tuple(reversed(outcomes)),
        evaluation_as_of=first + timedelta(days=35 * 30 + 30),
        minimum_mature_samples=30,
    )

    # Then: both horizons improve and the ordered split is recorded without NDCG promotion.
    assert result.promotion_eligible is True
    assert result.search_inflow_7d_improvement == 2.0
    assert result.search_inflow_28d_improvement == 4.0
    assert result.training_outcomes == 30
    assert result.validation_outcomes == 30
    assert result.rolling_origin_folds == 30
    assert result.ndcg_usage == "diagnostic_only"


def test_thirty_mature_outcomes_promote_with_twenty_nine_true_folds() -> None:
    # Given: exactly 30 mature labels with every prior 28d label available at prediction.
    kst = timezone(timedelta(hours=9))
    first = datetime(2023, 1, 1, 12, tzinfo=kst)
    outcomes = tuple(
        CandidateOutcome(
            f"OUT-minimum-{index:02d}",
            True,
            "mature",
            10.0,
            12.0,
            20.0,
            24.0,
            (first + timedelta(days=35 * index)).isoformat(),
            (first + timedelta(days=35 * index, hours=-1)).isoformat(),
            (first + timedelta(days=35 * index, hours=-2)).isoformat(),
            "sha256:" + f"{index + 301:064x}",
            "topic-feedback-v1",
            (first + timedelta(days=35 * index + 7, hours=1)).isoformat(),
            "sha256:" + f"{index + 401:064x}",
            (first + timedelta(days=35 * index + 28, hours=1)).isoformat(),
            "sha256:" + f"{index + 501:064x}",
        )
        for index in range(30)
    )

    # When: the temporal evaluator reaches the documented minimum.
    result = evaluate_outcomes(
        outcomes,
        evaluation_as_of=first + timedelta(days=35 * 29 + 30),
    )

    # Then: the first label remains training-only and 29 real folds support promotion.
    assert result.mature_selected_outcomes == 30
    assert result.validation_outcomes == 29
    assert result.rolling_origin_folds == 29
    assert result.promotion_eligible is True


def test_evaluation_excludes_future_and_late_horizon_observations() -> None:
    # Given: an outcome whose 28-day observation exists only after evaluation_as_of.
    outcome = CandidateOutcome(
        "OUT-future",
        True,
        "mature",
        10.0,
        12.0,
        20.0,
        24.0,
        "2026-07-01T12:00:00+09:00",
        "2026-07-01T11:00:00+09:00",
        "2026-07-01T10:00:00+09:00",
        "sha256:" + "1" * 64,
        "topic-feedback-v1",
        "2026-07-08T13:00:00+09:00",
        "sha256:" + "2" * 64,
        "2026-07-29T13:00:00+09:00",
        "sha256:" + "3" * 64,
    )

    # When: evaluation is frozen before the 28-day observation arrives.
    result = evaluate_outcomes(
        (outcome,),
        evaluation_as_of=datetime.fromisoformat("2026-07-20T00:00:00+09:00"),
    )

    # Then: it is missing and cannot become a validation fold.
    assert result.mature_selected_outcomes == 0
    assert result.validation_outcomes == 0
    assert result.missing_rate == 1.0
    assert result.promotion_eligible is False


def test_evaluation_rejects_observations_after_horizon_grace() -> None:
    # Given: otherwise mature outcomes observed months after both cohort horizons.
    first = datetime(2020, 1, 1, 12, tzinfo=timezone(timedelta(hours=9)))
    outcomes = tuple(
        CandidateOutcome(
            f"OUT-delayed-{index}",
            True,
            "mature",
            10.0,
            12.0,
            20.0,
            24.0,
            (first + timedelta(days=120 * index)).isoformat(),
            (first + timedelta(days=120 * index, hours=-1)).isoformat(),
            (first + timedelta(days=120 * index, hours=-2)).isoformat(),
            "sha256:" + f"{index + 1:064x}",
            "topic-feedback-v1",
            (first + timedelta(days=120 * index + 80)).isoformat(),
            "sha256:" + f"{index + 101:064x}",
            (first + timedelta(days=120 * index + 100)).isoformat(),
            "sha256:" + f"{index + 201:064x}",
        )
        for index in range(31)
    )

    # When: evaluation occurs after all delayed captures exist.
    result = evaluate_outcomes(
        outcomes,
        evaluation_as_of=first + timedelta(days=120 * 30 + 101),
    )

    # Then: delayed captures cannot masquerade as 7d or 28d labels.
    assert result.mature_selected_outcomes == 0
    assert result.validation_outcomes == 0
    assert result.promotion_eligible is False

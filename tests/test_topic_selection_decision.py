from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.contract_types import ContractError
from tools.topic_metadata import CreatorAdvisorCandidate, CreatorAdvisorSnapshot
from tools.topic_selection_decision import (
    TopicSelectionHistory,
    decide_topic,
    read_selection_decision,
    reserve_topic_decision,
    verify_selection_output,
)
from tools.topic_selection_history import completed_selection_history


def _snapshot(candidates: tuple[CreatorAdvisorCandidate, ...]) -> CreatorAdvisorSnapshot:
    return CreatorAdvisorSnapshot(
        as_of_date="2026-09-13",
        captured_at=datetime(2026, 9, 13, 8, 0, tzinfo=UTC).isoformat(),
        capture_id="CAP-decision",
        candidates=candidates,
    )


def test_decision_balances_categories_independent_of_dom_order() -> None:
    # Given: dining ranks first on screen but has more formal selection history.
    dining = CreatorAdvisorCandidate(
        "한정선 찹쌀떡-",
        3,
        category_key="맛집",
        category_rank=3,
        candidate_id="food-3",
    )
    travel = CreatorAdvisorCandidate(
        "서울 2026",
        1,
        category_key="국내여행",
        category_rank=1,
        candidate_id="travel-1",
    )
    history = (
        TopicSelectionHistory(
            "RUN-old",
            "맛집",
            "2026-09-12T08:00:00+09:00",
            "formal_selection_decision",
            True,
        ),
    )

    # When: the program selects from both DOM orders.
    first = decide_topic(_snapshot((dining, travel)), (), (), history)
    second = decide_topic(_snapshot((travel, dining)), (), (), history)

    # Then: category balance owns the same single decision and punctuation is preserved.
    assert first.selected_keyword == second.selected_keyword == "서울 2026"
    assert first.selected_category == second.selected_category == "국내여행"
    assert first.selection_policy_version == "category-balanced-v1"


def test_decision_excludes_normalized_alias_and_ambiguous_category_candidates() -> None:
    # Given: a normalized duplicate, an explicit alias duplicate, and an ambiguous row.
    snapshot = _snapshot(
        (
            CreatorAdvisorCandidate(
                " KFC   1+1 ", 1, category_key="맛집", category_rank=1, candidate_id="a"
            ),
            CreatorAdvisorCandidate(
                "새 여행", 1, category_key="국내여행", category_rank=1, candidate_id="b"
            ),
            CreatorAdvisorCandidate(
                "여행 추천",
                2,
                category_key="국내여행",
                category_rank=2,
                candidate_id="alias",
                aliases=("trip-alias",),
            ),
            CreatorAdvisorCandidate("분야 없음", 2, candidate_id="c"),
        )
    )

    # When: verified output and alias identities are supplied.
    decision = decide_topic(snapshot, ("kfc 1+1",), ("trip-alias",), ())

    # Then: only the eligible Creator Advisor candidate can be selected.
    assert decision.selected_keyword == "새 여행"
    assert {(item.keyword, item.reason) for item in decision.exclusions} == {
        (" KFC   1+1 ", "normalized_keyword_duplicate"),
        ("여행 추천", "explicit_alias_duplicate"),
        ("분야 없음", "ambiguous_category"),
    }


def test_reservation_reuses_same_run_and_blocks_other_run_duplicate(tmp_path: Path) -> None:
    # Given: one trusted snapshot and separate run work directories.
    snapshot = _snapshot(
        (
            CreatorAdvisorCandidate(
                "첫 주제", 1, category_key="맛집", category_rank=1, candidate_id="one"
            ),
            CreatorAdvisorCandidate(
                "둘째 주제", 2, category_key="맛집", category_rank=2, candidate_id="two"
            ),
        )
    )
    snapshot_path = tmp_path / "snapshot.json"
    _ = snapshot_path.write_text(
        json.dumps(snapshot.as_json(), ensure_ascii=False), encoding="utf-8"
    )

    # When: one run retries, then another run reserves from the same project.
    first = reserve_topic_decision(
        root=tmp_path,
        work_dir=tmp_path / "work/RUN-one",
        run_id="RUN-one",
        snapshot_path=snapshot_path,
        snapshot=snapshot,
        created_at="2026-09-13T08:00:00+09:00",
    )
    retry = reserve_topic_decision(
        root=tmp_path,
        work_dir=tmp_path / "work/RUN-one",
        run_id="RUN-one",
        snapshot_path=snapshot_path,
        snapshot=snapshot,
        created_at="2026-09-13T09:00:00+09:00",
    )
    second = reserve_topic_decision(
        root=tmp_path,
        work_dir=tmp_path / "work/RUN-two",
        run_id="RUN-two",
        snapshot_path=snapshot_path,
        snapshot=snapshot,
        created_at="2026-09-13T08:01:00+09:00",
    )
    late_retry = reserve_topic_decision(
        root=tmp_path,
        work_dir=tmp_path / "work/RUN-one",
        run_id="RUN-one",
        snapshot_path=snapshot_path,
        snapshot=snapshot,
        created_at="2026-09-13T10:00:00+09:00",
    )

    # Then: retry is byte-stable and the second run cannot reserve the first topic.
    assert retry.digest == first.digest
    assert retry.created_at == first.created_at
    assert late_retry.digest == first.digest
    assert second.selected_keyword == "둘째 주제"
    assert read_selection_decision(
        tmp_path / "work/RUN-one/selection-decision.json"
    ) == first


def test_output_checker_consumes_persisted_decision_without_reselection(tmp_path: Path) -> None:
    # Given: a persisted decision for one exact original keyword.
    snapshot = _snapshot(
        (
            CreatorAdvisorCandidate(
                "고정 주제", 1, category_key="맛집", category_rank=1, candidate_id="fixed"
            ),
        )
    )
    snapshot_path = tmp_path / "snapshot.json"
    _ = snapshot_path.write_text(json.dumps(snapshot.as_json()), encoding="utf-8")
    decision = reserve_topic_decision(
        root=tmp_path,
        work_dir=tmp_path / ".automation/work/RUN-fixed",
        run_id="RUN-fixed",
        snapshot_path=snapshot_path,
        snapshot=snapshot,
        created_at="2026-09-13T08:00:00+09:00",
    )

    # When/Then: matching output passes and a model-selected alternative fails early.
    verify_selection_output(decision, "고정 주제")
    with pytest.raises(ContractError, match="selection_decision_mismatch"):
        verify_selection_output(decision, "버거킹 아침메뉴")

    with pytest.raises(ContractError, match="no eligible candidates"):
        _ = decide_topic(snapshot, ("고정 주제",), (), ())


def test_completed_reserved_decision_balances_next_category(tmp_path: Path) -> None:
    snapshot = _snapshot(
        (
            CreatorAdvisorCandidate(
                "국내 첫째", 1, category_key="국내여행", category_rank=1, candidate_id="t1"
            ),
            CreatorAdvisorCandidate(
                "국내 둘째", 2, category_key="국내여행", category_rank=2, candidate_id="t2"
            ),
            CreatorAdvisorCandidate(
                "맛집 첫째", 1, category_key="맛집", category_rank=1, candidate_id="f1"
            ),
        )
    )
    snapshot_path = tmp_path / "snapshot.json"
    _ = snapshot_path.write_text(json.dumps(snapshot.as_json()), encoding="utf-8")
    first = reserve_topic_decision(
        root=tmp_path,
        work_dir=tmp_path / "work/RUN-one",
        run_id="RUN-one",
        snapshot_path=snapshot_path,
        snapshot=snapshot,
        created_at="2026-09-13T08:00:00+09:00",
    )
    selection_path = tmp_path / "research" / f"topic-selection-{first.selected_keyword}.md"
    selection_path.parent.mkdir()
    _ = selection_path.write_text("selected", encoding="utf-8")
    state_path = tmp_path / ".automation/state/RUN-one.json"
    state_path.parent.mkdir()
    _ = state_path.write_text(
        json.dumps(
            {
                "run_id": "RUN-one",
                "topic_source": "auto_selected",
                "stages": {"topic-selector": "passed"},
            }
        ),
        encoding="utf-8",
    )

    second = reserve_topic_decision(
        root=tmp_path,
        work_dir=tmp_path / "work/RUN-two",
        run_id="RUN-two",
        snapshot_path=snapshot_path,
        snapshot=snapshot,
        created_at="2026-09-13T09:00:00+09:00",
    )

    assert first.selected_category == "국내여행"
    assert second.selected_category == "맛집"
    assert second.category_selection_count == 0



@pytest.mark.parametrize("mode", ("demo", "beta", "legacy"))
def test_explicit_nonproduction_history_is_ignored_for_category_balance(
    tmp_path: Path, mode: str
) -> None:
    # Given: a completed reserved decision with explicit non-production provenance.
    history_root = tmp_path / "history"
    history_root.mkdir()
    historical_snapshot = _snapshot(
        (
            CreatorAdvisorCandidate(
                "기록 주제", 1, category_key="국내여행", category_rank=1, candidate_id="history"
            ),
        )
    )
    historical_snapshot_path = history_root / "historical-snapshot.json"
    _ = historical_snapshot_path.write_text(
        json.dumps(historical_snapshot.as_json(), ensure_ascii=False), encoding="utf-8"
    )
    historical = reserve_topic_decision(
        root=history_root,
        work_dir=history_root / "work/RUN-history",
        run_id="RUN-history",
        snapshot_path=historical_snapshot_path,
        snapshot=historical_snapshot,
        created_at="2026-09-13T08:00:00+09:00",
    )
    selection_path = history_root / "research" / f"topic-selection-{historical.selected_keyword}.md"
    selection_path.parent.mkdir()
    _ = selection_path.write_text("selected", encoding="utf-8")
    state_path = history_root / ".automation/state/RUN-history.json"
    state_path.parent.mkdir()
    state = {
        "run_id": "RUN-history",
        "topic_source": "auto_selected",
        "mode": mode,
        "stages": {"topic-selector": "passed"},
    }
    _ = state_path.write_text(json.dumps(state), encoding="utf-8")
    _ = (history_root / "research" / "topic-selection-출처 불명.md").write_text(
        "legacy output without a verified decision", encoding="utf-8"
    )
    current_snapshot = _snapshot(
        (
            CreatorAdvisorCandidate(
                "국내 후보", 1, category_key="국내여행", category_rank=1, candidate_id="travel"
            ),
            CreatorAdvisorCandidate(
                "맛집 후보", 1, category_key="맛집", category_rank=1, candidate_id="food"
            ),
            CreatorAdvisorCandidate(
                "기록 주제",
                2,
                category_key="국내여행",
                category_rank=2,
                candidate_id="duplicate-history",
            ),
            CreatorAdvisorCandidate(
                "출처 불명",
                3,
                category_key="국내여행",
                category_rank=3,
                candidate_id="ambiguous-history",
            ),
        )
    )
    baseline_root = tmp_path / "baseline"
    baseline_root.mkdir()
    baseline_snapshot_path = baseline_root / "snapshot.json"
    _ = baseline_snapshot_path.write_text(
        json.dumps(current_snapshot.as_json(), ensure_ascii=False), encoding="utf-8"
    )

    # When: production selection is computed with and without that history.
    baseline = reserve_topic_decision(
        root=baseline_root,
        work_dir=baseline_root / "work/RUN-current",
        run_id="RUN-current",
        snapshot_path=baseline_snapshot_path,
        snapshot=current_snapshot,
        created_at="2026-09-13T09:00:00+09:00",
    )
    with_history_snapshot_path = history_root / "snapshot.json"
    _ = with_history_snapshot_path.write_text(
        json.dumps(current_snapshot.as_json(), ensure_ascii=False), encoding="utf-8"
    )
    with_history = reserve_topic_decision(
        root=history_root,
        work_dir=history_root / "work/RUN-current",
        run_id="RUN-current",
        snapshot_path=with_history_snapshot_path,
        snapshot=current_snapshot,
        created_at="2026-09-13T09:00:00+09:00",
    )

    # Then: non-production history is excluded and cannot alter production routing or digest.
    assert completed_selection_history(history_root, "RUN-current", (historical,)) == ()
    assert with_history.selected_category == baseline.selected_category
    assert with_history.category_history_digest == baseline.category_history_digest
    assert with_history.category_selection_count == baseline.category_selection_count
    assert with_history.category_last_selected_at is None
    assert ("기록 주제", "normalized_keyword_duplicate") not in {
        (item.keyword, item.reason) for item in with_history.exclusions
    }
    assert ("출처 불명", "normalized_keyword_duplicate") in {
        (item.keyword, item.reason) for item in with_history.exclusions
    }


@pytest.mark.parametrize("mode", ("demo", "beta"))
def test_nonproduction_selected_keyword_remains_eligible(
    tmp_path: Path, mode: str
) -> None:
    history_root = tmp_path / "history"
    history_root.mkdir()
    snapshot = _snapshot(
        (
            CreatorAdvisorCandidate(
                "재사용 후보",
                1,
                category_key="국내여행",
                category_rank=1,
                candidate_id="reuse",
            ),
        )
    )
    historical_path = history_root / "historical.json"
    _ = historical_path.write_text(
        json.dumps(snapshot.as_json(), ensure_ascii=False), encoding="utf-8"
    )
    historical = reserve_topic_decision(
        root=history_root,
        work_dir=history_root / "work/RUN-history",
        run_id="RUN-history",
        snapshot_path=historical_path,
        snapshot=snapshot,
        created_at="2026-09-13T08:00:00+09:00",
    )
    selection_path = history_root / "research/topic-selection-재사용 후보.md"
    selection_path.parent.mkdir()
    _ = selection_path.write_text("selected", encoding="utf-8")
    state_path = history_root / ".automation/state/RUN-history.json"
    state_path.parent.mkdir()
    _ = state_path.write_text(
        json.dumps(
            {
                "run_id": historical.run_id,
                "topic_source": "auto_selected",
                "mode": mode,
                "stages": {"topic-selector": "passed"},
            }
        ),
        encoding="utf-8",
    )
    current_path = history_root / "current.json"
    _ = current_path.write_text(
        json.dumps(snapshot.as_json(), ensure_ascii=False), encoding="utf-8"
    )

    current = reserve_topic_decision(
        root=history_root,
        work_dir=history_root / "work/RUN-current",
        run_id="RUN-current",
        snapshot_path=current_path,
        snapshot=snapshot,
        created_at="2026-09-13T09:00:00+09:00",
    )

    assert current.selected_keyword == "재사용 후보"

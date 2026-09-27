from dataclasses import replace
from pathlib import Path

import pytest

from tools.aside_browser import AsideReplSession
from tools.codex_stage_executor import CodexStageExecutor, reject_error_report
from tools.codex_topic_selection import selection_evidence
from tools.contract_types import ContractError, JSONMap
from tools.dashboard_manual_batch import aggregate_status, new_batch
from tools.dashboard_manual_request import parse_manual_run_payload
from tools.research_browser_capture import capture_research_sources
from tools.runner_types import StageExecutionContext, TopicSelectionContext
from tools.topic_browser_capture import capture_creator_advisor


def test_failed_report_preserves_cause_and_redacts_secret() -> None:
    with pytest.raises(ContractError, match="Aside 연결 실패") as caught:
        reject_error_report("topic-selector", {"status": "failed", "execution": "attempted", "artifacts": [], "message": "Aside 연결 실패 secret_fixture"})
    assert "secret_fixture" not in str(caught.value)


def test_child_failure_is_batch_failure() -> None:
    batch = new_batch(parse_manual_run_payload({"auto_topic": True, "as_of_date": "2026-09-10"}))
    failed = replace(batch, children=(replace(batch.children[0], status="failed"),))
    assert aggregate_status(failed) == "failed"


def test_browser_failure_prevents_model_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "schemas").mkdir()
    _ = (tmp_path / "schemas/stage-result.schema.json").write_text((Path(__file__).parents[1] / "schemas/stage-result.schema.json").read_text())
    _ = (tmp_path / "topic-selector.md").write_text("instruction")

    def unavailable() -> JSONMap:
        raise ContractError("Aside 연결 실패")

    monkeypatch.setattr("tools.codex_topic_selection.capture_creator_advisor", unavailable)
    context = StageExecutionContext(root=tmp_path, stage="topic-selector", run_id="RUN-test", topic_id="TOPIC-test", keyword=None, work_dir=tmp_path / ".automation/work/test")
    with pytest.raises(ContractError, match="Aside 연결 실패"):
        _ = CodexStageExecutor(codex_binary="must-not-be-called").execute(context)


def test_capture_failure_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(self: AsideReplSession, operation: str, *, replay_on_stale: bool = True) -> JSONMap:
        _ = (self, operation, replay_on_stale)
        raise ContractError("daemon auth challenge failed")
    monkeypatch.setattr("tools.topic_browser_capture.resolve_aside_cli", lambda: Path("aside"))
    monkeypatch.setattr(AsideReplSession, "run_json", unavailable)
    with pytest.raises(ContractError, match="모델은 호출하지 않았습니다"):
        _ = capture_creator_advisor()


def test_fresh_host_capture_becomes_canonical_snapshot_before_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: JSONMap = {
        "capture_id": "RAW-test",
        "captured_at": "2026-09-13T08:00:00+09:00",
        "source_url": "https://creator-advisor.naver.com/naver_blog/sola_note",
        "tree": "raw tree",
        "candidates": [
            {
                "keyword": "서울 2026",
                "keyword_text": "서울 2026",
                "rank": 1,
                "category_key": "국내여행",
                "category_rank": 1,
                "candidate_id": "travel-1",
                "raw_observation": {"keyword_text": "서울 2026", "badge": "new"},
                "missing_fields": [],
                "aliases": [],
            }
        ],
    }
    monkeypatch.setattr(
        "tools.codex_topic_selection.capture_creator_advisor", lambda: observed
    )
    selection = TopicSelectionContext(
        category="",
        audience="",
        publish_purpose="",
        as_of_date="2026-09-13",
        batch_id="BATCH-test",
        batch_slot=1,
        snapshot_policy="capture_once",
        capture_id="CAP-test",
        snapshot_path="metadata/creator-advisor/2026-09-13/CAP-test.json",
    )
    context = StageExecutionContext(
        root=tmp_path,
        stage="topic-selector",
        run_id="RUN-test",
        topic_id="TOPIC-test",
        keyword=None,
        work_dir=tmp_path / ".automation/work/RUN-test/topic-selector",
        selection_context=selection,
    )
    context.work_dir.mkdir(parents=True)

    evidence = selection_evidence(context)

    assert evidence is not None and evidence.from_context
    assert selection.snapshot_path is not None
    assert evidence.relative_path == selection.snapshot_path
    assert (tmp_path / selection.snapshot_path).is_file()
    assert (context.work_dir / "RAW-test.json").is_file()


def test_research_capture_records_read_only_source_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: JSONMap = {
        "requested_keyword": "한정선 찹쌀떡",
        "requested_url": "https://search.naver.com/search.naver?query=%ED%95%9C%EC%A0%95%EC%84%A0%20%EC%B0%B9%EC%8C%80%EB%96%A1",
        "source_url": "https://search.naver.com/search.naver?query=%ED%95%9C%EC%A0%95%EC%84%A0%20%EC%B0%B9%EC%8C%80%EB%96%A1",
        "tree": "한정선 찹쌀떡 공식 정보",
    }
    def fake_capture(_keyword: str) -> JSONMap:
        return observed

    monkeypatch.setattr("tools.research_browser_capture._capture", fake_capture)
    result = capture_research_sources("한정선 찹쌀떡")
    assert result["keyword"] == "한정선 찹쌀떡"
    observations = result["observations"]
    assert isinstance(observations, list)
    assert observations[0] == {**observed, "source_kind": "search_results", "evidence_status": "observed"}

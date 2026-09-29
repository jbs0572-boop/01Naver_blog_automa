from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import override

import pytest

from tests.test_runner_prd import FixtureExecutor
from tools.contract_types import ContractError, JSONValue
from tools.log_contract import read_events
from tools.runner_execution import (
    confirm_job,
    pin_topic_feedback_context,
    resume_job,
    run_job,
)
from tools.runner_records import initial_state
from tools.runner_stages import event
from tools.runner_state import atomic_write_json, read_state
from tools.runner_types import (
    ConfirmationInput,
    JobName,
    RunnerRequest,
    RunStatus,
    StageEventContext,
    StageEventOutcome,
    StageExecution,
    StageExecutionContext,
    StageResult,
    TopicSelectionContext,
)
from tools.topic_feedback_config import create_rollback
from tools.topic_feedback_models import compute_digest
from tools.topic_feedback_pinning import signals_for_pinned_context
from tools.topic_feedback_scoring import rank_snapshot
from tools.topic_metadata import (
    CreatorAdvisorCandidate,
    CreatorAdvisorSnapshot,
    snapshot_sha256,
    write_snapshot,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)
APPROVAL_ROOT = PROJECT_ROOT / "tests/fixtures/feedback/task10-approval-root"


def _write_feedback_manifest(root: Path) -> None:
    signal: JSONValue = {
        "schema_version": "topic-signal-snapshot-v1",
        "captured_at": "2026-09-07T09:00:00+09:00",
        "as_of_date": "2026-09-07",
        "timezone": "Asia/Seoul",
        "limitations": ["relative_index_only"],
        "input_digests": [],
        "missing_fields": [],
        "status": "mature",
        "digest": "",
        "source_id": "naver-datalab",
        "source_confidence": "A",
        "access_mode": "official_api",
        "query_period": "2026-08-01/2026-09-07",
        "terms_checked_at": "2026-09-08T00:00:00+09:00",
        "raw_payload": {},
        "derived": {
            "ranking_features": [
                {"keyword": "첫 후보", "unit": "relative_index", "value": 0},
                {"keyword": "둘째 후보", "unit": "relative_index", "value": 100},
            ]
        },
    }
    assert isinstance(signal, dict)
    signal["digest"] = compute_digest(signal)
    encoded = json.dumps(
        signal, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    signal_path = root / "metadata/topic-signals/naver-datalab/2026-09-07/s.json"
    signal_path.parent.mkdir(parents=True)
    _ = signal_path.write_bytes(encoded)
    manifest: JSONValue = {
        "schema_version": "feedback-evidence-manifest-v1",
        "feedback_id": "task10-signals",
        "created_at": "2026-09-08T09:00:00+09:00",
        "data_as_of": "2026-09-08T23:59:00+09:00",
        "files": [
            {
                "path": signal_path.relative_to(root).as_posix(),
                "size_bytes": len(encoded),
                "sha256": "sha256:" + hashlib.sha256(encoded).hexdigest(),
                "schema_version": "topic-signal-snapshot-v1",
            }
        ],
        "digest": "",
    }
    assert isinstance(manifest, dict)
    unsigned = dict(manifest)
    _ = unsigned.pop("digest")
    canonical = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    manifest["digest"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    manifest_path = root / "metadata/feedback-manifests/task10-signals.json"
    manifest_path.parent.mkdir(parents=True)
    _ = manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _root(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    config.mkdir()
    _ = shutil.copy(PROJECT_ROOT / "config/topic-feedback-rollout.json", config)
    return tmp_path


def _set_rollout(root: Path, *, mode: str) -> None:
    path = root / "config/topic-feedback-rollout.json"
    value: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    value["feedback_enabled"] = mode in {"shadow", "active"}
    value["rollout_tier"] = "canary" if mode == "active" else mode
    value["active_score_version"] = (
        "topic-feedback-v1" if mode == "active" else "topic-baseline-v1"
    )
    _ = path.write_text(json.dumps(value), encoding="utf-8")


class FeedbackFixtureExecutor(FixtureExecutor):
    @override
    def execute(self, context: StageExecutionContext) -> StageResult:
        if context.stage != "topic-selector" or context.selection_context is None:
            return super().execute(context)
        selection = context.selection_context
        snapshot = CreatorAdvisorSnapshot(
            selection.as_of_date,
            NOW.isoformat(),
            (
                CreatorAdvisorCandidate("첫 후보", 1, channel_inflow=0.0),
                CreatorAdvisorCandidate("둘째 후보", 2, channel_inflow=100.0),
            ),
            capture_id=selection.capture_id or "capture-feedback",
        )
        path = write_snapshot(context.root, snapshot)
        ranking = rank_snapshot(
            snapshot,
            selection.excluded_keywords,
            signals_for_pinned_context(context.root, selection),
        )
        resolved = (
            ranking.shadow[0].keyword
            if selection.feedback_selection_mode == "active"
            else ranking.baseline[0].keyword
        )
        result = super().execute(context)
        return StageResult(
            result.status,
            result.execution,
            result.message,
            result.artifacts,
            resolved,
            details={
                "selection_snapshot_path": path.relative_to(context.root).as_posix(),
                "selection_snapshot_sha256": snapshot_sha256(path),
                "capture_id": snapshot.capture_id,
                "baseline_resolved_keyword": ranking.baseline[0].keyword,
                "shadow_resolved_keyword": ranking.shadow[0].keyword,
            },
        )


def _feedback_request(root: Path) -> RunnerRequest:
    capture_id = "capture-feedback"
    return RunnerRequest(
        root=root,
        job="daily-generate",
        auto_topic=True,
        dry_run=True,
        now=NOW,
        selection_context=TopicSelectionContext(
            "",
            "",
            "",
            "2026-09-09",
            batch_id="batch-feedback",
            batch_slot=1,
            snapshot_policy="capture_once",
            capture_id=capture_id,
            snapshot_path=f"metadata/creator-advisor/2026-09-09/{capture_id}.json",
        ),
        executor=FeedbackFixtureExecutor(),
    )


def _request(root: Path, context: TopicSelectionContext | None = None) -> RunnerRequest:
    return RunnerRequest(
        root=root,
        job="daily-generate",
        auto_topic=True,
        dry_run=True,
        now=NOW,
        selection_context=context or TopicSelectionContext("", "", "", "2026-09-09"),
    )


def test_cli_initial_state_and_first_event_share_immutable_pins(tmp_path: Path) -> None:
    # Given: an unpinned daily request at the shared runner boundary.
    pinned = pin_topic_feedback_context(
        _request(_root(tmp_path)), JobName.DAILY_GENERATE
    )
    assert pinned.selection_context is not None

    # When: the initial state and first stage event are projected.
    state = initial_state(pinned, "RUN-pin", "sha256:input", NOW.isoformat())
    first_event = event(
        StageEventContext(
            pinned,
            "RUN-pin",
            "BATCH-pin",
            "topic-selector",
            NOW.isoformat(),
            NOW.isoformat(),
            1,
            0.0,
            (),
        ),
        StageEventOutcome(RunStatus.PASSED, StageExecution.PRODUCED, "passed"),
    )

    # Then: all three pins and the baseline decision are byte-identical.
    selection = state["selection_context"]
    assert isinstance(selection, dict)
    for key in ("score_version", "score_config_digest", "feedback_manifest_digest"):
        assert isinstance(selection[key], str)
        assert first_event[key] == selection[key]
    assert first_event["feedback_selection_mode"] == "baseline"


def test_resume_rejects_mid_run_score_config_change(tmp_path: Path) -> None:
    # Given: a persisted baseline pin and a later shadow rollout config.
    root = _root(tmp_path)
    pinned = pin_topic_feedback_context(_request(root), JobName.DAILY_GENERATE)
    config_path = root / "config/topic-feedback-rollout.json"
    value: JSONValue = json.loads(config_path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    value["feedback_enabled"] = True
    value["rollout_tier"] = "shadow"
    _ = config_path.write_text(json.dumps(value), encoding="utf-8")

    # When/Then: a resumed request cannot silently swap its score pins.
    with pytest.raises(ContractError, match="topic feedback pins changed"):
        _ = pin_topic_feedback_context(pinned, JobName.DAILY_GENERATE)


def test_sentinel_signal_load_revalidates_pinned_config(tmp_path: Path) -> None:
    # Given: a baseline sentinel pin whose rollout file changes before selection.
    root = _root(tmp_path)
    pinned = pin_topic_feedback_context(_request(root), JobName.DAILY_GENERATE)
    assert pinned.selection_context is not None
    config_path = root / "config/topic-feedback-rollout.json"
    value: JSONValue = json.loads(config_path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    value["feedback_enabled"] = True
    value["rollout_tier"] = "shadow"
    _ = config_path.write_text(json.dumps(value), encoding="utf-8")

    # When/Then: even an absent-manifest sentinel cannot bypass config validation.
    with pytest.raises(ContractError, match="topic feedback pins changed"):
        _ = signals_for_pinned_context(root, pinned.selection_context)


def test_resume_rejects_legacy_state_without_feedback_pins(tmp_path: Path) -> None:
    # Given: a resumed daily request recovered from a legacy unpinned state.
    request = _request(_root(tmp_path))
    request = RunnerRequest(
        root=request.root,
        job=request.job,
        auto_topic=request.auto_topic,
        dry_run=request.dry_run,
        resume=True,
        now=request.now,
        selection_context=request.selection_context,
    )

    # When/Then: resume fails closed instead of inventing current pins.
    with pytest.raises(
        ContractError, match="persisted topic feedback pins are missing"
    ):
        _ = pin_topic_feedback_context(request, JobName.DAILY_GENERATE)


def test_user_defined_daily_keeps_keyword_and_still_receives_pins(
    tmp_path: Path,
) -> None:
    # Given: a user-defined topic on the same daily path.
    request = RunnerRequest(
        root=_root(tmp_path),
        job="daily-generate",
        keyword="사용자 원문",
        dry_run=True,
        selection_context=TopicSelectionContext("", "", "", "2026-09-09"),
    )

    # When: feedback context is pinned.
    pinned = pin_topic_feedback_context(request, JobName.DAILY_GENERATE)

    # Then: the topic is untouched while provenance is complete.
    assert pinned.keyword == "사용자 원문"
    assert pinned.selection_context is not None
    assert pinned.selection_context.score_version == "topic-baseline-v1"


def test_actual_daily_run_persists_same_pins_through_every_stage(
    tmp_path: Path,
) -> None:
    # Given: a real daily runner request with the fixture stage executor.
    request = _request(_root(tmp_path))

    # When: the shared runner completes the local daily pipeline.
    result = run_job(
        RunnerRequest(
            root=request.root,
            job=request.job,
            auto_topic=request.auto_topic,
            dry_run=True,
            now=request.now,
            selection_context=request.selection_context,
            executor=FixtureExecutor(),
        )
    )

    # Then: state and all stage events retain one immutable pin tuple.
    state = read_state(result.state_path)
    events = [
        item
        for item in read_events(result.log_path)
        if item.get("event_type") == "stage"
    ]
    keys = ("score_version", "score_config_digest", "feedback_manifest_digest")
    expected = tuple(state[key] for key in keys)
    assert len(events) == 7
    assert all(tuple(item[key] for key in keys) == expected for item in events)


def test_feedback_shadow_daily_keeps_baseline_resolved_keyword(tmp_path: Path) -> None:
    # Given: shadow rollout and a frozen snapshot whose challenger ranks second first.
    root = _root(tmp_path)
    _set_rollout(root, mode="shadow")
    _write_feedback_manifest(root)

    # When: the real daily runner completes its local pipeline.
    result = run_job(_feedback_request(root))

    # Then: shadow telemetry is pinned but baseline remains the selected keyword.
    state = read_state(result.state_path)
    assert state["feedback_selection_mode"] == "shadow"
    assert state["keyword"] == "첫 후보"


def test_feedback_active_daily_reorders_only_frozen_creator_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an active rollout authorized by a trusted evaluation decision.
    root = _root(tmp_path)
    _set_rollout(root, mode="active")
    _write_feedback_manifest(root)
    monkeypatch.setattr(
        "tools.topic_feedback_approval.APPROVAL_REGISTRY_PATH",
        APPROVAL_ROOT / "config/topic-feedback-approved-evaluations.json",
    )

    # When: the daily runner selects from the immutable snapshot.
    result = run_job(_feedback_request(root))

    # Then: only the second input Creator candidate becomes active.
    state = read_state(result.state_path)
    assert state["feedback_selection_mode"] == "active"
    assert state["keyword"] == "둘째 후보"


def test_feedback_rollback_daily_restores_baseline_selection_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: matching baseline and rolled-back roots with the same frozen input.
    baseline_root = tmp_path / "baseline"
    rollback_root = tmp_path / "rollback"
    baseline_root.mkdir()
    rollback_root.mkdir()
    _ = _root(baseline_root)
    _ = _root(rollback_root)
    _set_rollout(rollback_root, mode="active")
    _write_feedback_manifest(baseline_root)
    _write_feedback_manifest(rollback_root)
    monkeypatch.setattr(
        "tools.topic_feedback_approval.APPROVAL_REGISTRY_PATH",
        APPROVAL_ROOT / "config/topic-feedback-approved-evaluations.json",
    )
    _ = create_rollback(
        rollback_root,
        rollback_root / "config/topic-feedback-rollout.json",
        "RB-task10",
        "2026-09-09T09:00:00+09:00",
    )

    # When: both daily runs complete from the same Creator candidate snapshot.
    baseline = read_state(run_job(_feedback_request(baseline_root)).state_path)
    rollback = read_state(run_job(_feedback_request(rollback_root)).state_path)

    # Then: all effective baseline selection fields and the result are byte-equal.
    keys = (
        "score_version",
        "feedback_manifest_digest",
        "feedback_selection_mode",
        "keyword",
    )
    assert {key: baseline[key] for key in keys} == {key: rollback[key] for key in keys}


def test_confirmation_pin_drift_leaves_state_and_log_bytes_unchanged(
    tmp_path: Path,
) -> None:
    # Given: an awaiting-confirmation state whose rollout pin is later changed.
    root = _root(tmp_path)
    pinned = pin_topic_feedback_context(
        RunnerRequest(
            root,
            "daily-generate",
            keyword="확인 주제",
            selection_context=TopicSelectionContext("", "", "", "2026-09-09"),
        ),
        JobName.DAILY_GENERATE,
    )
    state = initial_state(pinned, "RUN-confirm-pin", "sha256:input", NOW.isoformat())
    state.update(
        {
            "status": "awaiting_user_confirmation",
            "target_blog_id": "blog",
            "naver_title": "title",
            "artifact_digest": "sha256:" + "1" * 64,
            "confirmation_requested_at": NOW.isoformat(),
            "confirmation_nonce": "nonce-confirm-pin",
        }
    )
    state_path = root / ".automation/state/RUN-confirm-pin.json"
    log_path = root / ".automation/logs/RUN-confirm-pin.jsonl"
    state_path.parent.mkdir(parents=True)
    log_path.parent.mkdir(parents=True)
    atomic_write_json(state_path, state)
    _ = log_path.write_bytes(b"existing-log\n")
    _set_rollout(root, mode="shadow")
    before_state, before_log = state_path.read_bytes(), log_path.read_bytes()

    # When/Then: confirmation fails before either durable record is changed.
    with pytest.raises(ContractError, match="topic feedback pins changed"):
        _ = confirm_job(ConfirmationInput(root, "RUN-confirm-pin", "naver-draft-save"))
    assert state_path.read_bytes() == before_state
    assert log_path.read_bytes() == before_log


def test_resume_rejects_changed_creator_snapshot_without_rewriting_history(
    tmp_path: Path,
) -> None:
    # Given: a completed run whose captured Creator snapshot is modified afterward.
    root = _root(tmp_path)
    result = run_job(_feedback_request(root))
    state = read_state(result.state_path)
    selection = state["selection_context"]
    assert isinstance(selection, dict)
    assert isinstance(selection.get("snapshot_sha256"), str)
    snapshot_path = root / str(selection["snapshot_path"])
    _ = snapshot_path.write_text(snapshot_path.read_text(encoding="utf-8") + " ")
    before_state, before_log = (
        result.state_path.read_bytes(),
        result.log_path.read_bytes(),
    )

    # When/Then: resume fails closed and both durable histories remain byte-stable.
    with pytest.raises(ContractError, match="pinned Creator Advisor snapshot changed"):
        _ = resume_job(RunnerRequest(root, "", run_id=result.run_id))
    assert result.state_path.read_bytes() == before_state
    assert result.log_path.read_bytes() == before_log

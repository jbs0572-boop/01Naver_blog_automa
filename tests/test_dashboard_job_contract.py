from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest

from tools.codex_process import run_codex
from tools.contract_types import ContractError
from tools.dashboard_manual_batch import new_batch
from tools.dashboard_manual_models import ManualBatchView, ManualRunInput
from tools.dashboard_manual_request import parse_manual_run_payload
from tools.dashboard_manual_store import ManualBatchStore
from tools.dashboard_model_settings import ModelSettingsStore
from tools.dashboard_schedule import DailySchedule
from tools.dashboard_usage import usage_for
from tools.model_presets import ModelConfigSnapshot


def _request(nonce: str, keyword: str = "예약 주제") -> ManualRunInput:
    return parse_manual_run_payload(
        {
            "keyword": keyword,
            "as_of_date": "2026-09-13",
            "request_nonce": nonce,
        }
    )


def test_accept_allocates_one_durable_display_id_when_nonce_is_replayed(
    tmp_path: Path,
) -> None:
    # Given: the same normalized request is accepted twice.
    store = ManualBatchStore(tmp_path)
    request = _request("11111111-1111-4111-8111-111111111111")

    # When: acceptance crosses the durable store boundary twice.
    first, first_created = store.accept(request, lambda display_id: new_batch(request, tmp_path, display_id=display_id))
    second, second_created = store.accept(request, lambda display_id: new_batch(request, tmp_path, display_id=display_id))

    # Then: replay returns the original job without allocating or saving another.
    assert first_created is True
    assert second_created is False
    assert second.batch_id == first.batch_id
    assert first.children[0].display_id == "2026-09-13_001"
    assert len(store.snapshot()) == 1


def test_accept_rejects_same_nonce_with_different_normalized_payload(
    tmp_path: Path,
) -> None:
    # Given: one accepted nonce and a changed topic using that nonce.
    store = ManualBatchStore(tmp_path)
    nonce = "22222222-2222-4222-8222-222222222222"
    first = _request(nonce, "첫 주제")
    changed = _request(nonce, "다른 주제")
    _ = store.accept(first, lambda display_id: new_batch(first, tmp_path, display_id=display_id))

    # When / Then: the store rejects the identity conflict without another write.
    with pytest.raises(ContractError, match="different payload"):
        _ = store.accept(changed, lambda display_id: new_batch(changed, tmp_path, display_id=display_id))
    assert len(store.snapshot()) == 1


def test_accept_allocates_unique_display_ids_under_process_lock(
    tmp_path: Path,
) -> None:
    # Given: independent store objects accept jobs for the same KST date.
    requests = tuple(
        _request(f"00000000-0000-4000-8000-{index:012d}", f"주제 {index}")
        for index in range(1, 21)
    )

    # When: callers race at the process-safe acceptance boundary.
    def accept(request: ManualRunInput) -> ManualBatchView:
        store = ManualBatchStore(tmp_path)
        return store.accept(request, lambda display_id: new_batch(request, tmp_path, display_id=display_id))[0]

    with ThreadPoolExecutor(max_workers=8) as pool:
        batches = tuple(pool.map(accept, requests))

    # Then: every accepted job owns one non-reused daily sequence.
    assert {batch.children[0].display_id for batch in batches} == {
        f"2026-09-13_{index:03d}" for index in range(1, 21)
    }
    assert len(ManualBatchStore(tmp_path).snapshot()) == 20


def test_display_sequence_continues_after_999_without_reuse(tmp_path: Path) -> None:
    # Given: an existing accepted job already owns the last three-digit sequence.
    store = ManualBatchStore(tmp_path)
    old_request = _request("33333333-3333-4333-8333-333333333333", "기존 주제")
    old = new_batch(old_request, tmp_path, display_id="2026-09-13_999")
    store.save(old)
    request = _request("44444444-4444-4444-8444-444444444444", "새 주제")

    # When: the next request is accepted for the same KST date.
    batch, created = store.accept(
        request, lambda display_id: new_batch(request, tmp_path, display_id=display_id)
    )

    # Then: the sequence grows to four digits and never wraps or reuses an ID.
    assert created is True
    assert batch.children[0].display_id == "2026-09-13_1000"


def test_usage_reports_observed_not_final_and_ignores_partial_line(tmp_path: Path) -> None:
    # Given: one complete usage event followed by a still-streaming JSON fragment.
    path = tmp_path / ".automation/work/RUN-test/writer/attempt-1/codex-attempt-1.jsonl"
    path.parent.mkdir(parents=True)
    event = {
        "type": "turn.completed",
        "usage": {"input_tokens": 11, "output_tokens": 3, "cached_input_tokens": 5},
    }
    _ = path.write_text(json.dumps(event) + "\n{\"type\":\"turn.completed\"", encoding="utf-8")

    # When: the active log is projected.
    usage = usage_for(tmp_path, "RUN-test")

    # Then: only a complete event contributes and the projection stays interim.
    assert usage is not None
    assert usage["total_tokens"] == 14
    assert usage["recorded_turns"] == 1
    assert usage["usage_state"] == "observed"
    assert usage["usage_observed_at"] is not None


def test_codex_attempt_usage_is_visible_before_process_exit(tmp_path: Path) -> None:
    # Given: a child emits one complete JSONL usage event and keeps running.
    event = json.dumps(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 7, "output_tokens": 2, "cached_input_tokens": 1},
        }
    )
    def invoke() -> None:
        run_codex(
            [sys.executable, "-u", "-c", f"import time; print({event!r}, flush=True); time.sleep(2)"],
            root=tmp_path,
            environment=dict(os.environ),
            timeout=10,
            work_dir=tmp_path / ".automation/work/RUN-stream/writer",
            max_attempts=1,
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(invoke)
        deadline = time.monotonic() + 1.5
        usage = None
        while usage is None and time.monotonic() < deadline:
            usage = usage_for(tmp_path, "RUN-stream")
            time.sleep(0.02)

        # Then: polling observes the flushed event while the producer is still alive.
        assert usage is not None and usage["total_tokens"] == 9
        assert not future.done()
        future.result(timeout=5)


def test_parser_rejects_a_non_uuid_request_nonce() -> None:
    # Given / When / Then: supplied idempotency identity must be a canonical UUID.
    with pytest.raises(ContractError, match="request_nonce"):
        _ = parse_manual_run_payload(
            {"auto_topic": True, "as_of_date": "2026-09-13", "request_nonce": "reused label"}
        )


def test_due_schedule_is_submitted_while_worker_is_busy(tmp_path: Path) -> None:
    # Given: an enabled per-time entry becomes due while another job is active.
    scheduler = DailySchedule(tmp_path)
    start = datetime.fromisoformat("2026-09-13T07:59:00+09:00")
    current = scheduler.view(start)
    entries = current["entries"]
    assert isinstance(entries, list) and isinstance(entries[0], dict)
    entry = {**entries[0], "time": "08:00"}
    _ = scheduler.save({"enabled": True, "entries": [entry]}, start)
    accepted: list[tuple[str, str, str]] = []

    # When: the scheduler observes the due time with a busy worker.
    def launch(
        day: str,
        scheduled_at: str,
        occurrence_id: str,
        model_config: ModelConfigSnapshot,
    ) -> str:
        accepted.append((day, scheduled_at, model_config.preset_id))
        return f"BATCH-{occurrence_id}"

    scheduler.tick(
        datetime.fromisoformat("2026-09-13T08:00:00+09:00"), lambda: True, launch
    )

    # Then: busy execution changes queue position, not occurrence acceptance.
    assert accepted == [("2026-09-13", "2026-09-13T08:00:00+09:00", "default")]
    history = scheduler.data["history"]
    assert isinstance(history, list) and isinstance(history[0], dict)
    assert history[0]["status"] == "submitted"


def test_legacy_schedule_is_backed_up_only_on_first_v2_save(tmp_path: Path) -> None:
    # Given: a live v1 schedule with history and a pinned model snapshot.
    initial = DailySchedule(tmp_path)
    model_config = initial.saved_model_config().as_json()
    legacy = {
        "enabled": True,
        "times": ["08:00"],
        "since": "2026-09-12T00:00:00+09:00",
        "history": [{"at": "2026-09-12T08:00:00+09:00", "status": "submitted"}],
        "preset_id": "default",
        "model_config": model_config,
    }
    path = tmp_path / ".automation/dashboard/schedule.json"
    path.parent.mkdir(parents=True)
    original = json.dumps(legacy, ensure_ascii=False).encode()
    _ = path.write_bytes(original)

    # When: loading is read-only and the first explicit save migrates the schema.
    loaded = DailySchedule(tmp_path)
    assert path.read_bytes() == original
    view = loaded.view(datetime.fromisoformat("2026-09-13T07:00:00+09:00"))
    entries = view["entries"]
    assert isinstance(entries, list)
    _ = loaded.save({"enabled": True, "entries": entries}, datetime.fromisoformat("2026-09-13T07:01:00+09:00"))

    # Then: exact legacy bytes survive and history remains unchanged in v2.
    backups = tuple(path.parent.glob("schedule.legacy-*.json"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    migrated = json.loads(path.read_text())
    assert migrated["version"] == 2
    assert migrated["history"] == legacy["history"]


def test_each_schedule_entry_keeps_its_own_pinned_preset(tmp_path: Path) -> None:
    # Given: two schedule rows select distinct model presets.
    settings = ModelSettingsStore(tmp_path)
    document = settings.view()
    presets = document.get("presets")
    assert isinstance(presets, list) and presets
    default = presets[0]
    assert isinstance(default, dict)
    alternate = json.loads(json.dumps(default))
    alternate["id"] = "alternate"
    alternate["name"] = "대체"
    _ = settings.save(
        {
            "revision": document["revision"],
            "active_preset_id": "default",
            "presets": [default, alternate],
        }
    )
    scheduler = DailySchedule(tmp_path, settings)
    now = datetime.fromisoformat("2026-09-13T07:59:00+09:00")
    _ = scheduler.save(
        {
            "enabled": True,
            "entries": [
                {"entry_id": "morning", "time": "08:00", "enabled": True, "preset_id": "default"},
                {"entry_id": "later", "time": "08:01", "enabled": True, "preset_id": "alternate"},
            ],
        },
        now,
    )
    launched: list[tuple[str, str]] = []

    # When: both entries become due in one scheduler observation.
    scheduler.tick(
        datetime.fromisoformat("2026-09-13T08:01:00+09:00"),
        lambda: False,
        lambda _day, scheduled, _occurrence, config: launched.append((scheduled, config.preset_id)) or f"BATCH-{config.preset_id}",
    )

    # Then: each occurrence submits the preset pinned on its own row.
    assert launched == [
        ("2026-09-13T08:00:00+09:00", "default"),
        ("2026-09-13T08:01:00+09:00", "alternate"),
    ]
    history = scheduler.data["history"]
    assert isinstance(history, list)
    assert all(isinstance(item, dict) and item.get("submitted_at") for item in history)

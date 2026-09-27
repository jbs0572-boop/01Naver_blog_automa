from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tools.contract_types import ContractError
from tools.dashboard_manual_batch import new_batch
from tools.dashboard_manual_models import (
    ManualActionView,
    ManualBatchView,
    ManualRunView,
    ManualSnapshotView,
    ManualTopicSource,
)
from tools.dashboard_manual_request import parse_manual_run_payload
from tools.dashboard_manual_store import ManualBatchStore


def persist_legacy_pending_batch(root: Path) -> ManualBatchView:
    batch = new_batch(
        parse_manual_run_payload({"auto_topic": True, "as_of_date": "2026-09-07"})
    )
    first = batch.children[0]
    context = first.selection_context
    assert context is not None
    children = tuple(
        replace(
            first,
            task_id=f"{batch.batch_id}-{slot}",
            child_id=f"CHILD-{slot:02d}",
            run_id=f"{first.run_id}-{slot}",
            slot=slot,
            selection_context=replace(
                context,
                batch_slot=slot,
                snapshot_policy="capture_once" if slot == 1 else "reuse_only",
            ),
        )
        for slot in range(1, 4)
    )
    legacy = replace(batch, children=children)
    ManualBatchStore(root).save(legacy)
    return legacy


def _child(batch_id: str, slot: int, keyword: str) -> ManualRunView:
    return ManualRunView(
        task_id=f"{batch_id}-{slot}",
        status="completed",
        submitted_at="2026-09-07T00:00:00+00:00",
        updated_at="2026-09-07T00:01:00+00:00",
        run_id=f"RUN-20260907-{slot:02d}",
        result_status="local-only",
        message="local pipeline completed",
        child_id=f"CHILD-{slot:02d}",
        slot=slot,
        keyword=keyword,
        next_action=ManualActionView("external", f"nonce-{slot}"),
    )


def _batch(
    *,
    batch_id: str = "BATCH-20260907-abc123def456",
    topic_source: ManualTopicSource = "auto_selected",
    submitted_at: str = "2026-09-07T00:00:00+00:00",
    children: tuple[ManualRunView, ...] | None = None,
) -> ManualBatchView:
    if children is None:
        child_count = 3 if topic_source == "auto_selected" else 1
        children = tuple(
            _child(batch_id, slot, f"주제 {slot}")
            for slot in range(1, child_count + 1)
        )
    return ManualBatchView(
        batch_id=batch_id,
        status="completed",
        topic_source=topic_source,
        as_of_date="2026-09-07",
        submitted_at=submitted_at,
        updated_at="2026-09-07T00:01:00+00:00",
        snapshot=(
            ManualSnapshotView(
                capture_id="CAPTURE-abc123def456",
                path="metadata/creator-advisor/2026-09-07/CAPTURE-abc123def456.json",
                sha256="sha256:0123456789abcdef",
            )
            if topic_source == "auto_selected"
            else None
        ),
        children=children,
    )


def test_store_round_trips_auto_batch_in_slot_order(tmp_path: Path) -> None:
    # Given: one persisted auto batch with all preallocated children.
    view = _batch()
    store = ManualBatchStore(tmp_path)

    # When: the batch is atomically saved and reloaded.
    store.save(view)
    loaded = store.get(view.batch_id)

    # Then: the durable record retains the ordered child contract exactly.
    assert loaded == view
    assert loaded is not None
    assert [child.slot for child in loaded.children] == [1, 2, 3]
    assert store.store_path(view.batch_id).is_file()


def test_store_round_trips_single_auto_child(tmp_path: Path) -> None:
    view = _batch(children=(_child("BATCH-20260907-abc123def456", 1, "주제 1"),))
    store = ManualBatchStore(tmp_path)
    store.save(view)
    assert store.get(view.batch_id) == view


@pytest.mark.parametrize(
    ("topic_source", "children"),
    [
        ("auto_selected", tuple(_child("BATCH-20260907-abc123def456", slot, f"주제 {slot}") for slot in (1, 2))),
        (
            "auto_selected",
            tuple(
                _child("BATCH-20260907-abc123def456", slot, f"주제 {slot}")
                for slot in range(1, 5)
            ),
        ),
        (
            "user_defined",
            (
                _child("BATCH-20260907-abc123def456", 1, "주제 1"),
                _child("BATCH-20260907-abc123def456", 2, "주제 2"),
            ),
        ),
    ],
)
def test_store_requires_exact_auto_and_user_child_counts(
    tmp_path: Path,
    topic_source: ManualTopicSource,
    children: tuple[ManualRunView, ...],
) -> None:
    # Given: an invalid source-specific child count.
    store = ManualBatchStore(tmp_path)
    view = _batch(topic_source=topic_source, children=children)

    # When: persistence is requested.
    # Then: the store fails closed before it creates a file.
    with pytest.raises(ContractError):
        store.save(view)
    assert not store.store_path(view.batch_id).exists()


def test_store_atomic_failure_preserves_previous_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a durable prior record and a replacement failure at the atomic seam.
    store = ManualBatchStore(tmp_path)
    original = _batch()
    store.save(original)
    path = store.store_path(original.batch_id)
    before = path.read_bytes()

    def fail_replace(source: str | bytes | Path, destination: str | bytes | Path) -> None:
        _ = (source, destination)
        raise OSError("interrupted replacement")

    monkeypatch.setattr("tools.runner_state.os.replace", fail_replace)

    # When: a replacement save is interrupted.
    with pytest.raises(ContractError):
        store.save(_batch(submitted_at="2026-09-07T00:02:00+00:00"))

    # Then: callers can still observe only the previously committed bytes.
    assert path.read_bytes() == before
    assert store.get(original.batch_id) == original


def test_store_rejects_filename_identity_mismatch(tmp_path: Path) -> None:
    # Given: a valid payload placed under another batch's filename.
    store = ManualBatchStore(tmp_path)
    view = _batch()
    path = store.store_path("BATCH-20260907-otheridentity")
    path.parent.mkdir(parents=True)
    payload = {"schema_version": "manual-batch-v1", **view.as_json()}
    _ = path.write_text(json.dumps(payload), encoding="utf-8")

    # When: the mismatched named resource is loaded.
    # Then: its identity conflict is not silently accepted.
    with pytest.raises(ContractError):
        _ = store.get("BATCH-20260907-otheridentity")


def test_store_fails_closed_on_malformed_batch(tmp_path: Path) -> None:
    # Given: invalid JSON in a discovered batch file.
    store = ManualBatchStore(tmp_path)
    path = store.store_path("BATCH-20260907-malformed")
    path.parent.mkdir(parents=True)
    _ = path.write_text("{not valid json", encoding="utf-8")

    # When: the dashboard requests the stored batch collection.
    # Then: malformed durable state raises instead of disappearing from history.
    with pytest.raises(ContractError):
        _ = store.list(limit=20)


def test_store_lists_newest_first_with_limit(tmp_path: Path) -> None:
    # Given: three separately submitted persisted batches.
    store = ManualBatchStore(tmp_path)
    older = _batch(
        batch_id="BATCH-20260907-oldestidentity",
        submitted_at="2026-09-07T00:00:00+00:00",
    )
    middle = _batch(
        batch_id="BATCH-20260907-middleidentity",
        submitted_at="2026-09-07T00:01:00+00:00",
    )
    newest = _batch(
        batch_id="BATCH-20260907-newestidentity",
        submitted_at="2026-09-07T00:02:00+00:00",
    )
    for view in (older, middle, newest):
        store.save(view)

    # When: the caller requests a bounded collection.
    listed = store.list(limit=2)

    # Then: only the two latest batches are returned in newest-first order.
    assert [view.batch_id for view in listed] == [newest.batch_id, middle.batch_id]

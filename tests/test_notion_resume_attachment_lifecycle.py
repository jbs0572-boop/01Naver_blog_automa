from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._notion_resume_test_support import _adapter, _request
from tests._notion_resume_transport import MemoryTransport
from tools.contract_types import ContractError, JSONMap
from tools.notion_resume import AttachmentSpec


def test_uncertain_attachment_initialize_requires_manual_recovery(
    tmp_path: Path,
) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    transport.uncertain_attachment_create = True

    # When
    with pytest.raises(ContractError, match="manual recovery"):
        _ = _adapter(transport).write_and_verify(request)

    # Then
    assert transport.attachment_initialize_attempts == 1
    assert transport.create_page_calls == 0


def test_upload_names_are_isolated_between_runs_with_same_asset_names(
    tmp_path: Path,
) -> None:
    first_request, _ = _request(tmp_path / "first", run_id="RUN-first")
    second_request, _ = _request(tmp_path / "second", run_id="RUN-second")

    first_specs = _adapter(MemoryTransport()).attachment_specs(first_request)
    second_specs = _adapter(MemoryTransport()).attachment_specs(second_request)

    assert [spec.path.relative_to(first_request.root) for spec in first_specs] == [
        spec.path.relative_to(second_request.root) for spec in second_specs
    ]
    assert all(spec.path.is_file() for spec in (*first_specs, *second_specs))
    assert {spec.name for spec in first_specs}.isdisjoint(
        {spec.name for spec in second_specs}
    )


def test_attachment_id_and_hash_are_checkpointed_before_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    adapter = _adapter(transport)
    original_complete = transport.complete_attachment

    def assert_checkpoint_then_complete(
        target_id: str,
        upload_id: str,
        spec: AttachmentSpec,
        *,
        timeout_seconds: float,
    ) -> JSONMap:
        assert request.checkpoint_path is not None
        checkpoint = json.loads(request.checkpoint_path.read_text(encoding="utf-8"))
        record = checkpoint["attachments"][spec.name]
        assert record == {
            "upload_id": upload_id,
            "sha256": spec.sha256,
            "status": "pending",
        }
        return original_complete(
            target_id, upload_id, spec, timeout_seconds=timeout_seconds
        )

    monkeypatch.setattr(transport, "complete_attachment", assert_checkpoint_then_complete)

    # When
    result = adapter.write_and_verify(request)

    # Then
    assert result["storage_integrity"] == "passed"
    assert transport.attachment_initialize_attempts == 2
    assert transport.attachment_complete_attempts == 2


def test_all_attachment_hash_provenance_is_durable_before_page_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    adapter = _adapter(transport)
    specs = adapter.attachment_specs(request)
    original_create_page = transport.create_page

    def assert_provenance_then_create(
        target_id: str,
        run_id: str,
        artifact_digest: str,
        attachment_ids: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> JSONMap:
        assert request.checkpoint_path is not None
        checkpoint = json.loads(request.checkpoint_path.read_text(encoding="utf-8"))
        records = checkpoint["attachments"]
        assert [records[spec.name]["upload_id"] for spec in specs] == list(
            attachment_ids
        )
        assert [records[spec.name]["sha256"] for spec in specs] == [
            spec.sha256 for spec in specs
        ]
        assert {records[spec.name]["status"] for spec in specs} == {"uploaded"}
        return original_create_page(
            target_id,
            run_id,
            artifact_digest,
            attachment_ids,
            timeout_seconds=timeout_seconds,
        )

    monkeypatch.setattr(transport, "create_page", assert_provenance_then_create)

    # When
    result = adapter.write_and_verify(request)

    # Then
    assert result["storage_integrity"] == "passed"


def test_pending_recorded_attachment_is_completed_before_reuse(tmp_path: Path) -> None:
    # Given
    request, manifest = _request(tmp_path)
    transport = MemoryTransport()
    adapter = _adapter(transport)
    pending = adapter.attachment_specs(request)[0]
    transport.attachments[pending.name] = [{"id": "pending", "status": "pending"}]
    digest = manifest["artifact_digest"]
    assert isinstance(digest, str)
    assert request.checkpoint_path is not None
    request.checkpoint_path.parent.mkdir(parents=True)
    _ = request.checkpoint_path.write_text(
        json.dumps(
            {
                "version": 1,
                "run_id": request.run_id,
                "target_id": request.target_id,
                "artifact_digest": digest,
                "attachments": {
                    pending.name: {
                        "upload_id": "pending",
                        "sha256": pending.sha256,
                        "status": "pending",
                    }
                },
                "page_id": None,
                "page_create_pending": False,
                "appended_root_count": 0,
                "append_pending_at": None,
                "q2_status": "pending",
                "q2_result": None,
            }
        ),
        encoding="utf-8",
    )

    # When
    result = adapter.write_and_verify(request)

    # Then
    assert result["storage_integrity"] == "passed"
    assert transport.attachment_complete_attempts == 2
    assert transport.attachment_initialize_attempts == 1


def test_resume_rejects_uploaded_metadata_mismatch_before_page_create(
    tmp_path: Path,
) -> None:
    # Given
    request, manifest = _request(tmp_path)
    transport = MemoryTransport()
    adapter = _adapter(transport)
    spec = adapter.attachment_specs(request)[0]
    transport.attachments[spec.name] = [
        {
            "id": "recorded",
            "status": "uploaded",
            "filename": spec.name,
            "content_type": "image/png",
            "content_length": True,
        }
    ]
    digest = manifest["artifact_digest"]
    assert isinstance(digest, str)
    assert request.checkpoint_path is not None
    request.checkpoint_path.parent.mkdir(parents=True)
    _ = request.checkpoint_path.write_text(
        json.dumps(
            {
                "version": 1,
                "run_id": request.run_id,
                "target_id": request.target_id,
                "artifact_digest": digest,
                "attachments": {
                    spec.name: {
                        "upload_id": "recorded",
                        "sha256": spec.sha256,
                        "status": "uploaded",
                        "filename": spec.name,
                        "content_type": "image/png",
                        "content_length": spec.path.stat().st_size,
                    }
                },
                "page_id": None,
                "page_create_pending": False,
                "appended_root_count": 0,
                "append_pending_at": None,
                "q2_status": "pending",
                "q2_result": None,
            }
        ),
        encoding="utf-8",
    )

    # When / Then
    with pytest.raises(ContractError, match="metadata"):
        _ = adapter.write_and_verify(request)
    assert transport.create_page_calls == 0

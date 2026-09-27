from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests._notion_resume_test_support import _adapter, _request
from tests._notion_resume_transport import MemoryTransport, _page
from tools.contract_types import ContractError, JSONMap
from tools.external_adapter import ExternalWriteRequest
from tools.notion_resume import ResumableNotionAdapter


def test_each_create_is_authorized_with_bound_operation(tmp_path: Path) -> None:
    # Given
    request, manifest = _request(tmp_path)
    transport = MemoryTransport()
    operations: list[str] = []

    def record_authorization(
        candidate: ExternalWriteRequest, operation: str
    ) -> JSONMap:
        assert candidate is request
        operations.append(operation)
        digest = manifest["artifact_digest"]
        assert isinstance(digest, str)
        return {"verified_artifact_digest": digest}

    adapter = ResumableNotionAdapter(
        transport,
        _page("topic"),
        lambda: datetime(2026, 8, 31, 9, 5, tzinfo=UTC),
        record_authorization,
    )

    # When
    _ = adapter.write_and_verify(request)

    # Then
    assert operations == [
        "create_attachment",
        "create_attachment",
        "create_attachment",
        "create_attachment",
        "create_page",
    ]


def test_first_create_authorization_failure_makes_no_create_or_checkpoint_marker(
    tmp_path: Path,
) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    calls = 0

    def mismatched_authorization(
        candidate: ExternalWriteRequest, operation: str
    ) -> JSONMap:
        nonlocal calls
        _ = (candidate, operation)
        calls += 1
        return {"verified_artifact_digest": "sha256:" + "0" * 64}

    adapter = ResumableNotionAdapter(
        transport,
        _page("topic"),
        lambda: datetime(2026, 8, 31, 9, 5, tzinfo=UTC),
        mismatched_authorization,
    )

    # When / Then
    with pytest.raises(ContractError, match="artifact digest"):
        _ = adapter.write_and_verify(request)
    assert transport.attachment_create_attempts == 0
    assert transport.create_page_calls == 0
    assert request.checkpoint_path is not None
    assert not request.checkpoint_path.exists()

    # Then: a subsequent authorized execution may create the resources.
    result = _adapter(transport).write_and_verify(request)
    assert result["storage_integrity"] == "passed"
    assert transport.create_page_calls == 1


def test_second_create_authorization_failure_preserves_prior_checkpoint_without_pending_marker(
    tmp_path: Path,
) -> None:
    # Given
    request, manifest = _request(tmp_path)
    transport = MemoryTransport()
    operations: list[str] = []

    def first_only_authorization(
        candidate: ExternalWriteRequest, operation: str
    ) -> JSONMap:
        assert candidate is request
        operations.append(operation)
        digest = manifest["artifact_digest"]
        assert isinstance(digest, str)
        return {
            "verified_artifact_digest": (
                digest if len(operations) == 1 else "sha256:" + "0" * 64
            )
        }

    adapter = ResumableNotionAdapter(
        transport,
        _page("topic"),
        lambda: datetime(2026, 8, 31, 9, 5, tzinfo=UTC),
        first_only_authorization,
    )
    specs = adapter.attachment_specs(request)
    first_spec, second_spec = specs

    # When / Then
    with pytest.raises(ContractError, match="artifact digest"):
        _ = adapter.write_and_verify(request)
    assert operations == ["create_attachment", "create_attachment"]
    assert request.checkpoint_path is not None
    checkpoint = json.loads(request.checkpoint_path.read_text(encoding="utf-8"))
    attachments = checkpoint["attachments"]
    assert isinstance(attachments, dict)
    assert attachments[first_spec.name] == {
        "upload_id": "attachment-1",
        "sha256": first_spec.sha256,
        "status": "pending",
    }
    assert second_spec.name not in attachments
    assert transport.created_attachments == [first_spec.name]

    # Then: a valid resume creates only the uncompleted attachment and page.
    result = _adapter(transport).write_and_verify(request)
    assert result["storage_integrity"] == "passed"
    assert transport.created_attachments == [first_spec.name, second_spec.name]
    assert transport.create_page_calls == 1

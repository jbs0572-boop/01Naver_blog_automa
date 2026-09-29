from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from tests._notion_resume_test_support import _adapter, _request
from tests._notion_resume_transport import MemoryTransport, _page
from tests.test_notion_resume_identity_reconciliation import (
    MismatchedCreatedPageTransport,
    MismatchedPageTransport,
    UnreconciledPageTransport,
)
from tools.contract_types import ContractError


def test_ambiguous_active_upload_match_fails_closed(tmp_path: Path) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    spec = _adapter(transport).attachment_specs(request)[0]
    transport.attachments[spec.name] = [
        {"id": "pending", "status": "pending"},
        {"id": "uploaded", "status": "uploaded"},
    ]

    # When / Then
    with pytest.raises(ContractError, match="ambiguous"):
        _ = _adapter(transport).write_and_verify(request)


def test_unreconciled_attachment_create_is_not_retried(tmp_path: Path) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    transport.uncertain_attachment_create = True
    transport.persist_uncertain_attachment = False
    adapter = _adapter(transport)

    # When / Then
    with pytest.raises(ContractError, match="manual recovery"):
        _ = adapter.write_and_verify(request)
    with pytest.raises(ContractError, match="manual recovery"):
        _ = adapter.write_and_verify(request)
    assert transport.attachment_initialize_attempts == 1


def test_uncertain_page_create_without_match_fails_closed(tmp_path: Path) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = UnreconciledPageTransport()

    # When / Then
    with pytest.raises(ContractError, match="could not be reconciled"):
        _ = _adapter(transport).write_and_verify(request)


def test_page_adoption_rejects_mismatched_returned_identity(tmp_path: Path) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = MismatchedPageTransport()

    # When / Then
    with pytest.raises(ContractError, match="identity does not match"):
        _ = _adapter(transport).write_and_verify(request)


def test_page_create_rejects_mismatched_returned_identity(tmp_path: Path) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = MismatchedCreatedPageTransport()

    # When / Then
    with pytest.raises(ContractError, match="identity does not match"):
        _ = _adapter(transport).write_and_verify(request)


def test_failed_q2_is_checkpointed_and_retry_never_recreates_page(
    tmp_path: Path,
) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    transport.actual_page = _page("wrong")
    adapter = _adapter(transport)

    # When / Then
    with pytest.raises(ContractError, match="digest"):
        _ = adapter.write_and_verify(request)
    checkpoint_path = request.checkpoint_path
    assert checkpoint_path is not None
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["q2_status"] == "failed"
    assert checkpoint["page_id"] == "page-created"
    transport.actual_page = _page("topic")
    result = adapter.write_and_verify(request)
    assert result["storage_integrity"] == "passed"
    assert transport.create_page_calls == 1


def test_checkpoint_identity_mismatch_fails_before_transport(tmp_path: Path) -> None:
    # Given
    request, _ = _request(tmp_path)
    assert request.checkpoint_path is not None
    request.checkpoint_path.parent.mkdir(parents=True)
    _ = request.checkpoint_path.write_text(
        json.dumps(
            {
                "run_id": request.run_id,
                "target_id": "other-target",
                "artifact_digest": "sha256:" + "0" * 64,
                "attachments": {},
                "page_id": None,
                "q2_status": "pending",
                "q2_result": None,
            }
        ),
        encoding="utf-8",
    )
    transport = MemoryTransport()

    # When / Then
    with pytest.raises(ContractError, match="identity"):
        _ = _adapter(transport).write_and_verify(request)
    assert transport.create_page_calls == 0


def test_resume_rejects_tampered_recorded_attachment_identity(tmp_path: Path) -> None:
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    adapter = _adapter(transport)
    _ = adapter.write_and_verify(request)
    assert request.checkpoint_path is not None
    checkpoint = json.loads(request.checkpoint_path.read_text(encoding="utf-8"))
    raw_attachments = checkpoint["attachments"]
    assert isinstance(raw_attachments, dict)
    attachments = cast(dict[str, object], raw_attachments)
    first_name = next(iter(attachments))
    attachments[first_name] = "unrelated-upload"
    checkpoint["q2_status"] = "pending"
    checkpoint["page_id"] = None
    _ = request.checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    with pytest.raises(ContractError, match="provenance record"):
        _ = adapter.write_and_verify(request)
    assert transport.attachment_initialize_attempts == 2
    assert transport.create_page_calls == 1


def test_resume_rejects_tampered_recorded_page_identity_before_append(
    tmp_path: Path,
) -> None:
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    adapter = _adapter(transport)
    _ = adapter.write_and_verify(request)
    assert request.checkpoint_path is not None
    checkpoint = json.loads(request.checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["q2_status"] = "pending"
    checkpoint["page_id"] = "unrelated-page"
    _ = request.checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    with pytest.raises(ContractError, match="recorded page identity"):
        _ = adapter.write_and_verify(request)
    assert transport.create_page_calls == 1

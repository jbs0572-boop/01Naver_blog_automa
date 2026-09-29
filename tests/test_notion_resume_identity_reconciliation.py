from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import override

import pytest

from tests._notion_resume_test_support import _adapter, _request
from tests._notion_resume_transport import MemoryTransport, _page
from tools.contract_types import ContractError, JSONMap
from tools.notion_resume import NotionCreateUncertain, ResumableNotionAdapter


class UnreconciledPageTransport(MemoryTransport):
    @override
    def create_page(
        self,
        target_id: str,
        run_id: str,
        artifact_digest: str,
        attachment_ids: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> JSONMap:
        _ = (target_id, run_id, artifact_digest, attachment_ids, timeout_seconds)
        raise NotionCreateUncertain(resource="page")


class MismatchedPageTransport(MemoryTransport):
    @override
    def find_pages(
        self,
        target_id: str,
        run_id: str,
        artifact_digest: str,
        *,
        timeout_seconds: float,
    ) -> list[JSONMap]:
        _ = (target_id, run_id, artifact_digest, timeout_seconds)
        return [
            {
                "id": "wrong-page",
                "run_id": "different-run",
                "artifact_digest": artifact_digest,
            }
        ]


class MismatchedCreatedPageTransport(MemoryTransport):
    @override
    def create_page(
        self,
        target_id: str,
        run_id: str,
        artifact_digest: str,
        attachment_ids: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> JSONMap:
        _ = (target_id, run_id, attachment_ids, timeout_seconds)
        return {
            "id": "wrong-created-page",
            "run_id": "different-run",
            "artifact_digest": artifact_digest,
        }


@pytest.mark.parametrize(
    ("parent", "reason"),
    [
        (None, "parent data source is missing"),
        ({"type": "database_id"}, "parent type is not data_source_id"),
        (
            {"type": "data_source_id", "data_source_id": "other-source"},
            "does not match expected target",
        ),
    ],
)
def test_adapter_refuses_append_when_page_parent_is_not_the_planned_data_source(
    tmp_path: Path, parent: JSONMap | None, reason: str
) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    transport.root_blocks = [
        {"type": "paragraph", "index": index} for index in range(101)
    ]
    transport.page_parent = parent

    # When / Then
    with pytest.raises(ContractError, match=reason):
        _ = _adapter(transport).write_and_verify(request)
    assert transport.remote_roots == transport.root_blocks[:100]
    assert transport.append_attempts == 0


@pytest.mark.parametrize("resource", ["attachment", "page"])
def test_adapter_fails_closed_on_ambiguous_identity(
    tmp_path: Path, resource: str
) -> None:
    # Given
    request, manifest = _request(tmp_path)
    transport = MemoryTransport()
    if resource == "attachment":
        spec = _adapter(transport).attachment_specs(request)[0]
        transport.attachments[spec.name] = [
            {"id": "one", "status": "pending"},
            {"id": "two", "status": "uploaded"},
        ]
    else:
        digest = manifest["artifact_digest"]
        assert isinstance(digest, str)
        transport.pages.extend(
            [
                {"id": "one", "run_id": request.run_id, "artifact_digest": digest},
                {"id": "two", "run_id": request.run_id, "artifact_digest": digest},
            ]
        )

    # When / Then
    with pytest.raises(ContractError, match="ambiguous"):
        _ = _adapter(transport).write_and_verify(request)


def test_uncertain_page_create_reconciles_without_duplicate(tmp_path: Path) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    transport.uncertain_page_create = True

    # When
    result = _adapter(transport).write_and_verify(request)

    # Then
    assert result["notion_page_id"] == "page-created"
    assert transport.create_page_calls == 1


def test_restart_rebinds_reconciled_attachment_ids_before_page_verification(
    tmp_path: Path,
) -> None:
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    transport.actual_page = _page("wrong")
    with pytest.raises(ContractError, match="content digest"):
        _ = _adapter(transport).write_and_verify(request)
    created_attachments = transport.attachment_create_attempts
    created_pages = transport.create_page_calls
    bound: list[tuple[str, ...]] = []
    transport.actual_page = _page("topic")

    result = ResumableNotionAdapter(
        transport,
        _page("topic"),
        lambda: datetime(2026, 8, 31, 9, 5, tzinfo=UTC),
        attachment_binder=bound.append,
    ).write_and_verify(request)

    assert result["storage_integrity"] == "passed"
    assert len(bound) == 1
    assert len(bound[0]) == 2
    assert transport.attachment_create_attempts == created_attachments
    assert transport.create_page_calls == created_pages


def test_first_write_q2_fails_when_concurrent_duplicate_appears(tmp_path: Path) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    transport.duplicate_after_create = True

    # When / Then
    with pytest.raises(ContractError, match="ambiguous"):
        _ = _adapter(transport).write_and_verify(request)
    assert transport.create_page_calls == 1


def test_precreate_reconciliation_rejects_same_run_with_other_digest(
    tmp_path: Path,
) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    transport.pages.append(
        {
            "id": "page-stale",
            "target_id": request.target_id,
            "run_id": request.run_id,
            "artifact_digest": "sha256:" + "0" * 64,
        }
    )

    # When / Then
    with pytest.raises(ContractError, match="does not match this write"):
        _ = _adapter(transport).write_and_verify(request)
    assert transport.create_page_calls == 0


def test_fresh_checkpoint_does_not_adopt_preexisting_matching_page(
    tmp_path: Path,
) -> None:
    # Given
    request, manifest = _request(tmp_path)
    transport = MemoryTransport()
    transport.pages.append(
        {
            "id": "page-existing",
            "target_id": request.target_id,
            "run_id": request.run_id,
            "artifact_digest": manifest["artifact_digest"],
        }
    )

    # When / Then
    with pytest.raises(ContractError, match="manual recovery"):
        _ = _adapter(transport).write_and_verify(request)
    assert transport.create_page_calls == 0
    assert transport.append_attempts == 0


def test_precreate_reconciliation_rejects_page_without_target_identity(
    tmp_path: Path,
) -> None:
    # Given
    request, manifest = _request(tmp_path)
    transport = MemoryTransport()
    transport.pages.append(
        {
            "id": "page-unbound",
            "run_id": request.run_id,
            "artifact_digest": manifest["artifact_digest"],
        }
    )

    # When / Then
    with pytest.raises(ContractError, match="does not match this write"):
        _ = _adapter(transport).write_and_verify(request)
    assert transport.create_page_calls == 0

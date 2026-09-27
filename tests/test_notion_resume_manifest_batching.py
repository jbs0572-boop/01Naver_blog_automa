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


def test_adapter_derives_deterministic_manifest_attachment_names(
    tmp_path: Path,
) -> None:
    # Given
    request, manifest = _request(tmp_path)
    transport = MemoryTransport()

    # When
    _ = _adapter(transport).write_and_verify(request)

    # Then
    files = manifest["files"]
    assert isinstance(files, list)
    expected: list[str] = []
    for entry in files:
        assert isinstance(entry, dict)
        role = entry.get("role")
        sha256 = entry.get("sha256")
        path = entry.get("path")
        if role not in {"body_image", "thumbnail"}:
            continue
        assert isinstance(role, str)
        assert isinstance(sha256, str)
        assert isinstance(path, str)
        expected.append(f"RUN-notion-resume--{role}--{sha256}--{Path(path).name}")
    expected.sort(key=lambda name: "--thumbnail--" not in name)
    assert transport.created_attachments == expected


def test_adapter_rejects_filename_only_existing_attachments_before_page(
    tmp_path: Path,
) -> None:
    # Given
    request, manifest = _request(tmp_path)
    transport = MemoryTransport()
    digest = manifest["artifact_digest"]
    assert isinstance(digest, str)
    transport.pages.append(
        {
            "id": "page-existing",
            "run_id": request.run_id,
            "artifact_digest": digest,
        }
    )
    for spec in _adapter(transport).attachment_specs(request):
        transport.attachments[spec.name] = [
            {"id": f"existing-{spec.role}", "status": "uploaded"}
        ]

    # When
    with pytest.raises(ContractError, match="provenance"):
        _ = _adapter(transport).write_and_verify(request)

    # Then
    assert transport.created_attachments == []
    assert transport.create_page_calls == 0


def test_adapter_appends_root_blocks_in_batches_and_persists_progress(
    tmp_path: Path,
) -> None:
    # Given
    request, manifest = _request(tmp_path)
    transport = MemoryTransport()
    transport.root_blocks = [
        {"type": "paragraph", "index": index} for index in range(205)
    ]
    digest = manifest["artifact_digest"]
    assert isinstance(digest, str)

    def record_authorization(
        candidate: ExternalWriteRequest, operation: str
    ) -> JSONMap:
        assert candidate is request
        assert operation == "create_page" or operation == "create_attachment"
        transport.append_events.append("authorize")
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
    checkpoint_path = request.checkpoint_path
    assert checkpoint_path is not None
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert len(transport.remote_roots) == 205
    assert checkpoint["appended_root_count"] == 205
    assert transport.append_events == [
        "authorize",
        "authorize",
        "authorize",
        "authorize",
        "authorize",
        "parent",
        "authorize",
        "patch",
        "parent",
        "authorize",
        "patch",
    ]

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast, override

import pytest

from tools.contract_types import PIPELINE_VERSION, ContractError, JSONMap
from tools.external_adapter import (
    ExternalAction,
    ExternalSystem,
    ExternalWritePlan,
    ExternalWriteRequest,
)
from tools.manifest import ManifestBuildInput, build_manifest
from tools.notion_checkpoint import load_checkpoint
from tools.notion_resume import (
    AttachmentSpec,
    NotionCreateUncertain,
    ResumableNotionAdapter,
)
from tools.runner_actions import stage_action
from tools.runner_types import JobName, RunnerRequest, StageRunContext


class MemoryTransport:
    def __init__(self) -> None:
        self.attachments: dict[str, list[JSONMap]] = {}
        self.pages: list[JSONMap] = []
        self.created_attachments: list[str] = []
        self.attachment_create_attempts: int = 0
        self.attachment_complete_attempts: int = 0
        self.attachment_initialize_attempts: int = 0
        self.uncertain_attachment_create: bool = False
        self.uncertain_attachment_pending: bool = False
        self.persist_uncertain_attachment: bool = True
        self.create_page_calls: int = 0
        self.uncertain_page_create: bool = False
        self.actual_page: JSONMap = _page("topic")
        self.transport_calls: int = 0
        self.timeouts: list[float] = []
        self.root_blocks: list[JSONMap] = []
        self.remote_roots: list[JSONMap] = []
        self.page_parent: JSONMap | None = {
            "type": "data_source_id",
            "data_source_id": "datasource-resume",
        }
        self.append_attempts: int = 0
        self.append_events: list[str] = []
        self.duplicate_after_create: bool = False

    def _record_timeout(self, timeout_seconds: float) -> None:
        self.transport_calls += 1
        self.timeouts.append(timeout_seconds)

    def find_attachments(
        self, target_id: str, name: str, *, timeout_seconds: float
    ) -> list[JSONMap]:
        _ = target_id
        self._record_timeout(timeout_seconds)
        return list(self.attachments.get(name, []))

    def create_attachment(
        self, target_id: str, spec: AttachmentSpec, *, timeout_seconds: float
    ) -> JSONMap:
        _ = target_id
        self._record_timeout(timeout_seconds)
        self.attachment_create_attempts += 1
        self.created_attachments.append(spec.name)
        attachment: JSONMap = {
            "id": f"attachment-{len(self.created_attachments)}",
            "status": "pending" if self.uncertain_attachment_pending else "uploaded",
        }
        if self.persist_uncertain_attachment or not self.uncertain_attachment_create:
            self.attachments.setdefault(spec.name, []).append(attachment)
        if self.uncertain_attachment_create or self.uncertain_attachment_pending:
            raise NotionCreateUncertain(resource="attachment")
        return attachment

    def initialize_attachment(
        self, target_id: str, spec: AttachmentSpec, *, timeout_seconds: float
    ) -> JSONMap:
        _ = target_id
        self._record_timeout(timeout_seconds)
        self.attachment_initialize_attempts += 1
        self.created_attachments.append(spec.name)
        attachment: JSONMap = {
            "id": f"attachment-{len(self.created_attachments)}",
            "status": "pending",
        }
        if self.persist_uncertain_attachment or not self.uncertain_attachment_create:
            self.attachments.setdefault(spec.name, []).append(attachment)
        if self.uncertain_attachment_create:
            raise NotionCreateUncertain(resource="attachment")
        return attachment

    def complete_attachment(
        self,
        target_id: str,
        upload_id: str,
        spec: AttachmentSpec,
        *,
        timeout_seconds: float,
    ) -> JSONMap:
        _ = target_id
        self._record_timeout(timeout_seconds)
        self.attachment_complete_attempts += 1
        for attachments in self.attachments.values():
            for attachment in attachments:
                if attachment.get("id") == upload_id:
                    attachment.update(
                        status="uploaded",
                        filename=spec.name,
                        content_type="image/png",
                        content_length=spec.path.stat().st_size,
                    )
                    return attachment
        raise ContractError("pending attachment does not exist")

    def find_pages(
        self,
        target_id: str,
        run_id: str,
        artifact_digest: str,
        *,
        timeout_seconds: float,
    ) -> list[JSONMap]:
        _ = (target_id, artifact_digest)
        self._record_timeout(timeout_seconds)
        return [
            page
            for page in self.pages
            if page.get("run_id") == run_id
        ]

    def create_page(
        self,
        target_id: str,
        run_id: str,
        artifact_digest: str,
        attachment_ids: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> JSONMap:
        _ = (target_id, attachment_ids)
        self._record_timeout(timeout_seconds)
        self.create_page_calls += 1
        page: JSONMap = {
            "id": "page-created",
            "target_id": target_id,
            "run_id": run_id,
            "artifact_digest": artifact_digest,
        }
        self.pages.append(page)
        if self.duplicate_after_create:
            self.pages.append(
                {
                    "id": "page-concurrent",
                    "target_id": target_id,
                    "run_id": run_id,
                    "artifact_digest": artifact_digest,
                }
            )
        self.remote_roots = list(self.root_blocks[:100])
        if self.uncertain_page_create:
            raise NotionCreateUncertain(resource="page")
        return page

    def fetch_page(self, page_id: str, *, timeout_seconds: float) -> JSONMap:
        _ = page_id
        self._record_timeout(timeout_seconds)
        return self.actual_page

    def verify_page_parent(
        self, page_id: str, target_id: str, *, timeout_seconds: float
    ) -> None:
        _ = page_id
        self._record_timeout(timeout_seconds)
        self.append_events.append("parent")
        parent = self.page_parent
        if parent is None:
            raise ContractError("Notion page parent data source is missing")
        if parent.get("type") != "data_source_id":
            raise ContractError("Notion page parent type is not data_source_id")
        if parent.get("data_source_id") != target_id:
            raise ContractError(
                "Notion page parent data source does not match expected target"
            )

    def page_root_count(self) -> int:
        return len(self.root_blocks)

    def verified_root_count(self, page_id: str, *, timeout_seconds: float) -> int:
        _ = page_id
        self._record_timeout(timeout_seconds)
        if self.remote_roots != self.root_blocks[: len(self.remote_roots)]:
            raise ContractError("Notion page root prefix mismatch")
        return len(self.remote_roots)

    def append_page(
        self, page_id: str, start_index: int, *, timeout_seconds: float
    ) -> JSONMap:
        _ = page_id
        self._record_timeout(timeout_seconds)
        self.append_attempts += 1
        self.append_events.append("patch")
        self.remote_roots.extend(self.root_blocks[start_index : start_index + 100])
        return {"id": page_id}


def _page(title: str) -> JSONMap:
    return {
        "title": title,
        "properties": {},
        "blocks": [
            {"type": "image", "image": {"artifact_role": "thumbnail"}},
            {"type": "paragraph", "plain_text": "body"},
        ],
    }


def _request(tmp_path: Path) -> tuple[ExternalWriteRequest, JSONMap]:
    keyword = "topic"
    run_id = "RUN-notion-resume"
    asset_dir = tmp_path / "assets" / keyword
    final_dir = tmp_path / "final"
    asset_dir.mkdir(parents=True)
    final_dir.mkdir()
    _ = (asset_dir / "body.png").write_bytes(b"body")
    _ = (asset_dir / "thumbnail.png").write_bytes(b"thumbnail")
    _ = (asset_dir / "image-map.md").write_text("# map\n", encoding="utf-8")
    _ = (final_dir / f"{keyword}.md").write_text(
        "![body](../assets/topic/body.png)\n", encoding="utf-8"
    )
    for suffix in ("-naver-layout.md", "-naver-copy.md"):
        _ = (final_dir / f"{keyword}{suffix}").write_text("# file\n", encoding="utf-8")
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-resume`\n", encoding="utf-8"
    )
    manifest = build_manifest(
        ManifestBuildInput(
            tmp_path, keyword, run_id, "TOPIC-resume", "2026-08-31T09:00:00+09:00"
        )
    )
    manifest_path = tmp_path / "manifest.json"
    _ = manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    run_log = tmp_path / "run.jsonl"
    event = {
        "event_type": "stage",
        "pipeline_version": PIPELINE_VERSION,
        "batch_id": "BATCH-resume",
        "run_id": run_id,
        "topic_id": "TOPIC-resume",
        "stage": "content-assembler",
        "started_at": "2026-08-31T09:00:00+09:00",
        "ended_at": "2026-08-31T09:01:00+09:00",
        "status": "passed",
        "attempt": 1,
        "telemetry_version": 2,
        "duration_ms": 60_000,
        "depends_on": ["image-maker"],
        "quality": {"artifact_digest": manifest["artifact_digest"]},
    }
    _ = run_log.write_text(json.dumps(event) + "\n", encoding="utf-8")
    return (
        ExternalWriteRequest(
            root=tmp_path,
            manifest_path=manifest_path,
            run_log=run_log,
            system=ExternalSystem.NOTION,
            gate="notion_write",
            run_id=run_id,
            target_id="datasource-resume",
            dry_run=False,
            checkpoint_path=tmp_path / "state" / "notion.json",
        ),
        manifest,
    )


def _adapter(transport: MemoryTransport) -> ResumableNotionAdapter:
    return ResumableNotionAdapter(
        transport,
        _page("topic"),
        lambda: datetime(2026, 8, 31, 9, 5, tzinfo=UTC),
    )


def test_checkpoint_rejects_boolean_appended_root_count(tmp_path: Path) -> None:
    # Given
    request, manifest = _request(tmp_path)
    assert request.checkpoint_path is not None
    request.checkpoint_path.parent.mkdir(parents=True)
    digest = manifest["artifact_digest"]
    assert isinstance(digest, str)
    _ = request.checkpoint_path.write_text(
        json.dumps(
            {
                "version": 1,
                "run_id": request.run_id,
                "target_id": request.target_id,
                "artifact_digest": digest,
                "attachments": {},
                "appended_root_count": True,
            }
        ),
        encoding="utf-8",
    )
    plan = ExternalWritePlan(
        ExternalSystem.NOTION,
        ExternalAction.NOTION_WRITE,
        request.run_id,
        request.target_id,
        digest,
        (),
        False,
        True,
    )

    # When / Then
    with pytest.raises(ContractError, match="appended root count is invalid"):
        _ = load_checkpoint(request.checkpoint_path, plan)


def test_checkpoint_converts_non_utf8_json_to_contract_error(tmp_path: Path) -> None:
    request, _ = _request(tmp_path)
    assert request.checkpoint_path is not None
    request.checkpoint_path.parent.mkdir(parents=True)
    _ = request.checkpoint_path.write_bytes(b"\xff")
    plan = ExternalWritePlan(
        ExternalSystem.NOTION,
        ExternalAction.NOTION_WRITE,
        request.run_id,
        request.target_id,
        "digest",
        (),
        False,
        True,
    )
    with pytest.raises(ContractError, match="could not read Notion checkpoint"):
        _ = load_checkpoint(request.checkpoint_path, plan)


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


def test_passed_q2_resume_revalidates_remote_page_without_recreating_resources(
    tmp_path: Path,
) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    adapter = _adapter(transport)
    first = adapter.write_and_verify(request)
    attachment_creates = transport.attachment_create_attempts
    page_creates = transport.create_page_calls
    remote_calls = transport.transport_calls

    # When
    resumed = adapter.write_and_verify(request)

    # Then
    assert resumed["notion_page_id"] == first["notion_page_id"]
    assert transport.attachment_create_attempts == attachment_creates
    assert transport.create_page_calls == page_creates
    assert transport.transport_calls > remote_calls


def test_passed_q2_resume_rejects_missing_recorded_remote_page_identity(
    tmp_path: Path,
) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    adapter = _adapter(transport)
    _ = adapter.write_and_verify(request)
    transport.pages.clear()
    attachment_creates = transport.attachment_create_attempts
    page_creates = transport.create_page_calls

    # When / Then
    with pytest.raises(ContractError, match="recorded page"):
        _ = adapter.write_and_verify(request)
    assert transport.attachment_create_attempts == attachment_creates
    assert transport.create_page_calls == page_creates


def test_transport_calls_receive_remaining_overall_timeout_and_fail_closed(
    tmp_path: Path,
) -> None:
    # Given
    request, _ = _request(tmp_path)
    transport = MemoryTransport()
    adapter = _adapter(transport)

    # When
    _ = adapter.write_and_verify(request)

    # Then
    assert transport.timeouts
    assert all(
        0 < timeout <= request.notion_timeout_seconds for timeout in transport.timeouts
    )

    # Given: elapsed monotonic time consumes the overall deadline before any call.
    exhausted_request = replace(
        request,
        checkpoint_path=tmp_path / "state" / "exhausted.json",
        notion_timeout_seconds=1.0,
    )
    ticks = iter((0.0, 1.0))
    exhausted_transport = MemoryTransport()
    exhausted_adapter = ResumableNotionAdapter(
        exhausted_transport,
        _page("topic"),
        lambda: datetime(2026, 8, 31, 9, 5, tzinfo=UTC),
        monotonic_clock=lambda: next(ticks),
    )

    # When / Then
    with pytest.raises(ContractError, match="deadline"):
        _ = exhausted_adapter.write_and_verify(exhausted_request)
    assert exhausted_transport.transport_calls == 0


class CapturingAdapter:
    def __init__(self) -> None:
        self.request: ExternalWriteRequest | None = None

    def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
        self.request = request
        content_digest = "sha256:" + "1" * 64
        return {
            "storage_integrity": "passed",
            "notion_page_id": "page",
            "notion_last_verified_at": "2026-08-31T09:05:00+00:00",
            "expected_notion_content_digest": content_digest,
            "notion_content_digest": content_digest,
            "notion_roundtrip_digest": content_digest,
            "artifact_digest": "sha256:" + "2" * 64,
        }


def test_runner_passes_deterministic_checkpoint_under_configured_state_dir(
    tmp_path: Path,
) -> None:
    # Given
    state_dir = tmp_path / "custom-state"
    adapter = CapturingAdapter()
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-resume`\n", encoding="utf-8"
    )
    request = RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword="topic",
        state_dir=state_dir,
        notion_target_id="datasource-resume",
        notion_adapter=adapter,
    )

    # When
    _ = stage_action(
        StageRunContext(
            request,
            JobName.DAILY_GENERATE,
            "notion-rider",
            "RUN-checkpoint",
            "2026-08-31T09:00:00+09:00",
        )
    )

    # Then
    assert adapter.request is not None
    assert adapter.request.checkpoint_path == (
        state_dir / "notion" / "RUN-checkpoint.json"
    )

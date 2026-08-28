from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from tools.contract_types import ContractError, JSONMap
from tools.gate import GateRequest, authorize_external_write
from tools.manifest import verify_manifest


class ExternalSystem(StrEnum):
    NOTION = "notion"
    NAVER = "naver"


class ExternalAction(StrEnum):
    NOTION_WRITE = "notion_write"
    NAVER_DRAFT_SAVE = "naver_draft_save"


@dataclass(frozen=True, slots=True)
class ExternalWriteRequest:
    root: Path
    manifest_path: Path
    run_log: Path
    system: ExternalSystem
    gate: str
    run_id: str
    target_id: str
    dry_run: bool
    notion_page_id: str | None = None
    notion_verified_at: str | None = None
    blog_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExternalWritePlan:
    system: ExternalSystem
    action: ExternalAction
    run_id: str
    target_id: str
    artifact_digest: str
    artifact_paths: tuple[str, ...]
    dry_run: bool
    would_execute: bool

    def as_json(self) -> JSONMap:
        return {
            "system": self.system.value,
            "action": self.action.value,
            "run_id": self.run_id,
            "target_id": self.target_id,
            "artifact_digest": self.artifact_digest,
            "artifact_paths": list(self.artifact_paths),
            "dry_run": self.dry_run,
            "would_execute": self.would_execute,
        }


def plan_external_write(request: ExternalWriteRequest) -> ExternalWritePlan:
    expected_gate = (
        ExternalAction.NOTION_WRITE
        if request.system is ExternalSystem.NOTION
        else ExternalAction.NAVER_DRAFT_SAVE
    )
    if request.gate != expected_gate.value:
        raise ContractError(
            f"external adapter gate does not match system: {request.system.value}"
        )
    verified = authorize_external_write(
        GateRequest(
            root=request.root,
            manifest_path=request.manifest_path,
            run_log=request.run_log,
            gate=request.gate,
            run_id=request.run_id,
            target_id=request.target_id,
            notion_connector=request.system is ExternalSystem.NOTION,
            notion_operation="create_pages",
            notion_resource_id=request.target_id,
            notion_page_id=request.notion_page_id,
            notion_verified_at=request.notion_verified_at,
            blog_id=request.blog_id,
        )
    )
    manifest = verify_manifest(request.root, request.manifest_path)
    if not request.dry_run:
        raise ContractError("external writes are not implemented; use dry-run")
    action = expected_gate
    paths = tuple(entry.path for entry in manifest.files)
    digest = verified.get("verified_artifact_digest")
    if not isinstance(digest, str):
        raise ContractError("verified gate did not return an artifact digest")
    return ExternalWritePlan(
        request.system,
        action,
        request.run_id,
        request.target_id,
        digest,
        paths,
        True,
        False,
    )


__all__ = [
    "ExternalAction",
    "ExternalSystem",
    "ExternalWritePlan",
    "ExternalWriteRequest",
    "plan_external_write",
]

from __future__ import annotations

import secrets
from hashlib import sha256
from pathlib import Path

from tools.contract_types import ContractError, JSONMap
from tools.gate import GateRequest, verify_gate
from tools.manifest import verify_manifest
from tools.naver_adapter import StructuredNaverBrowserAdapter
from tools.notion_copy_grammar import closed_tag
from tools.notion_copy_parser import parse_naver_copy_text
from tools.runner_stages import now
from tools.runner_state import atomic_write_json, read_state, state_paths
from tools.runner_types import (
    RunStatus,
    StageExecution,
    StageResult,
    StageRunContext,
)


def naver_input_title(body: str, fallback: str) -> str:
    lines = iter(body.splitlines())
    for raw_line in lines:
        line = raw_line.strip()
        inline_title = closed_tag(line, "TITLE")
        if inline_title is not None and inline_title[1]:
            return inline_title[1]
        if line != "[TITLE]":
            continue
        for candidate_line in lines:
            title = candidate_line.strip()
            if not title:
                continue
            if title.startswith("[") and title.endswith("]"):
                break
            return title
        break
    return next(
        (line[2:].strip() for line in body.splitlines() if line.startswith("# ")),
        fallback,
    )


def _verify_naver_gate(
    context: StageRunContext,
    manifest_path: Path,
    blog_id: str,
) -> JSONMap:
    state_path, run_log, _ = state_paths(
        context.request.root, context.run_id, context.request.state_dir
    )
    state = read_state(state_path)
    page_id = state.get("notion_page_id")
    verified_at = state.get("notion_last_verified_at")
    expected_digest = state.get("expected_notion_content_digest")
    content_digest = state.get("notion_content_digest")
    roundtrip_digest = state.get("notion_roundtrip_digest")
    q2_artifact_digest = state.get("artifact_digest")
    manifest = verify_manifest(context.request.root, manifest_path)
    input_entry = next(
        (item for item in manifest.files if item.role == "naver_input"), None
    )
    copy_entry = next(
        (item for item in manifest.files if item.role == "naver_copy"), None
    )
    if input_entry is None or copy_entry is None:
        raise ContractError("Q2-reviewed Naver copy or canonical input is missing")
    input_document = parse_naver_copy_text(
        (context.request.root / input_entry.path).read_text(encoding="utf-8")
    )
    q2_document = parse_naver_copy_text(
        (context.request.root / copy_entry.path).read_text(encoding="utf-8")
    )
    if input_document != q2_document:
        raise ContractError("canonical Naver input differs from the Q2-reviewed copy")
    if (
        not isinstance(expected_digest, str)
        or not isinstance(content_digest, str)
        or not isinstance(roundtrip_digest, str)
        or not isinstance(q2_artifact_digest, str)
    ):
        raise ContractError("persisted Notion Q2 digest identity is missing")
    if (
        expected_digest != content_digest
        or content_digest != roundtrip_digest
        or q2_artifact_digest != manifest.artifact_digest
    ):
        raise ContractError("persisted Notion Q2 digest identity is stale")
    if context.request.confirmed and state.get("target_blog_id") != blog_id:
        raise ContractError("confirmed Naver target blog ID changed")
    return verify_gate(
        GateRequest(
            root=context.request.root,
            manifest_path=manifest_path,
            run_log=run_log,
            gate="naver_draft_save",
            run_id=context.run_id,
            target_id=blog_id,
            notion_page_id=page_id if isinstance(page_id, str) else None,
            notion_verified_at=verified_at if isinstance(verified_at, str) else None,
            expected_notion_content_digest=expected_digest,
            notion_content_digest=content_digest,
            notion_roundtrip_digest=roundtrip_digest,
            q2_artifact_digest=q2_artifact_digest,
            blog_id=blog_id,
        )
    )


def naver_stage_action(context: StageRunContext) -> StageResult:
    request = context.request
    adapter = request.naver_adapter
    if adapter is None:
        raise ContractError("Naver adapter is not configured")
    if request.keyword is None:
        raise ContractError("Naver input requires a resolved keyword")
    input_path = request.root / "final" / f"{request.keyword}-naver-input.md"
    if not input_path.is_file():
        raise ContractError(f"Naver input is missing: {input_path}")
    body = input_path.read_text(encoding="utf-8")
    title = naver_input_title(body, request.keyword)
    manifest_path = (
        request.root / "manifests" / f"{context.run_id}-workflow-manifest.json"
    )
    manifest = verify_manifest(request.root, manifest_path)
    blog_id = adapter.target_blog_id
    article_quality = _verify_naver_gate(context, manifest_path, blog_id)
    if not request.confirmed:
        if isinstance(adapter, StructuredNaverBrowserAdapter):
            preview = adapter.prepare_document(
                input_path,
                request.root / "assets" / request.keyword,
                manifest.artifact_digest,
            )
        else:
            preview = adapter.prepare(title, body, manifest.artifact_digest)
        details = dict(preview)
        details["article_quality"] = article_quality
        if details.get("target_blog_id") != blog_id:
            raise ContractError("Naver adapter target blog ID changed")
        details["external_call"] = True
        details["confirmation_requested_at"] = now(request).isoformat()
        confirmation_nonce = secrets.token_urlsafe(32)
        details["confirmation_nonce"] = confirmation_nonce
        details["confirmation_request_digest"] = sha256(
            confirmation_nonce.encode()
        ).hexdigest()
        details["naver_title"] = title
        return StageResult(
            RunStatus.PASSED,
            StageExecution.PRODUCED,
            "awaiting_user_confirmation",
            (),
            None,
            RunStatus.AWAITING_USER_CONFIRMATION,
            details,
        )
    state_path, _, _ = state_paths(
        request.root, context.run_id, request.state_dir
    )
    state = read_state(state_path)
    state["naver_save_outcome_uncertain"] = True
    atomic_write_json(state_path, state)
    saved = adapter.save(title, manifest.artifact_digest)
    if saved.get("draft_status") != "saved":
        raise ContractError("Naver adapter did not confirm draft save")
    details = dict(saved)
    details["article_quality"] = article_quality
    details["external_call"] = True
    return StageResult(
        RunStatus.PASSED,
        StageExecution.PRODUCED,
        "임시저장 완료",
        (),
        None,
        RunStatus.DRAFT_SAVED,
        details,
    )


def invalidate_naver_preparation(root: Path, run_id: str) -> None:
    state_path, _, _ = state_paths(root, run_id, None)
    state = read_state(state_path)
    if state.get("status") == RunStatus.DRAFT_SAVED.value:
        raise ContractError("saved Naver draft preparation cannot be invalidated")
    if state.get("naver_save_outcome_uncertain") is True:
        raise ContractError(
            "Naver save outcome is uncertain; reconcile the existing draft before retrying"
        )
    stages = state.get("stages")
    executions = state.get("stage_execution")
    if not isinstance(stages, dict) or not isinstance(executions, dict):
        raise ContractError("Naver preparation state is incomplete")
    stages["naver-rider"] = RunStatus.PENDING.value
    executions["naver-rider"] = StageExecution.NOT_CALLED.value
    state["status"] = RunStatus.LOCAL_ONLY.value
    state["message"] = "content workflow completed; external storage is pending"
    for key in (
        "confirmation",
        "confirmation_requested_at",
        "confirmation_request_digest",
        "confirmation_nonce",
        "naver_evidence_path",
        "naver_layout_digest",
        "naver_prepared_path",
        "naver_tab_target_id",
        "naver_title",
        "target_blog_id",
    ):
        if key in state:
            del state[key]
    atomic_write_json(state_path, state)


__all__ = [
    "invalidate_naver_preparation",
    "naver_input_title",
    "naver_stage_action",
]

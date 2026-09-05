from __future__ import annotations

from pathlib import Path

from tools.contract_types import ContractError
from tools.gate import GateRequest, verify_gate
from tools.manifest import verify_manifest
from tools.runner_stages import now
from tools.runner_state import read_state, state_paths
from tools.runner_types import (
    RunStatus,
    StageExecution,
    StageResult,
    StageRunContext,
)


def naver_input_title(body: str, fallback: str) -> str:
    lines = iter(body.splitlines())
    for line in lines:
        if line.strip() != "[TITLE]":
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
        (
            line[2:].strip()
            for line in body.splitlines()
            if line.startswith("# ")
        ),
        fallback,
    )


def _verify_naver_gate(
    context: StageRunContext,
    manifest_path: Path,
    blog_id: str,
) -> None:
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
    _ = verify_gate(GateRequest(
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
    ))


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
    _verify_naver_gate(context, manifest_path, blog_id)
    if not request.confirmed:
        preview = adapter.prepare(title, body, manifest.artifact_digest)
        details = dict(preview)
        if details.get("target_blog_id") != blog_id:
            raise ContractError("Naver adapter target blog ID changed")
        details["external_call"] = True
        details["confirmation_requested_at"] = now(request).isoformat()
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
    saved = adapter.save(title, manifest.artifact_digest)
    if saved.get("draft_status") != "saved":
        raise ContractError("Naver adapter did not confirm draft save")
    details = dict(saved)
    details["external_call"] = True
    return StageResult(
        RunStatus.PASSED,
        StageExecution.PRODUCED,
        "draft_saved",
        (),
        None,
        RunStatus.DRAFT_SAVED,
        details,
    )


__all__ = ["naver_input_title", "naver_stage_action"]

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from tools.article_quality import ArticleQualityFailure, load_assessment
from tools.contract_types import ContractError, JSONMap
from tools.gate_models import GateRequest, parse_aware_datetime
from tools.manifest import Manifest
from tools.manifest_parsing import as_map
from tools.naver_adapter import NaverConfig


@dataclass(frozen=True, slots=True)
class NaverToolRequest:
    target_id: str
    blog_id: str | None
    connector: str | None
    operation: str | None
    url: str | None
    locator: str | None
    value: str | None
    phase: str | None


@dataclass(frozen=True, slots=True)
class NaverCanonicalInput:
    title: str
    body: str


def _latest_q2_event(events: list[JSONMap], manifest: Manifest) -> JSONMap:
    for event in reversed(events):
        if (
            event.get("run_id") == manifest.run_id
            and event.get("topic_id") == manifest.topic_id
            and event.get("stage") == "notion-rider"
        ):
            return event
    raise ContractError("latest Notion Q2 result is missing")


def _latest_q2_quality(events: list[JSONMap], manifest: Manifest) -> JSONMap:
    event = _latest_q2_event(events, manifest)
    quality = event.get("quality")
    if (
        event.get("telemetry_version") != 2
        or event.get("status") not in {"passed", "success", "completed"}
        or not isinstance(quality, dict)
    ):
        raise ContractError("latest Notion Q2 result did not pass")
    if quality.get("storage_integrity") != "passed":
        raise ContractError("latest Notion Q2 storage integrity did not pass")
    return quality


def verify_q2_identity(
    events: list[JSONMap],
    manifest: Manifest,
    request: GateRequest,
    configured_data_source_id: str,
) -> None:
    notion_page_id = request.notion_page_id
    notion_verified_at = request.notion_verified_at
    blog_id = request.blog_id
    if not isinstance(notion_page_id, str):
        raise ContractError("Notion page ID is missing before Naver write")
    if not isinstance(notion_verified_at, str):
        raise ContractError(
            "Notion verification timestamp is missing before Naver write"
        )
    if not isinstance(blog_id, str):
        raise ContractError("Naver blog ID is missing before Naver write")
    quality = _latest_q2_quality(events, manifest)
    expected_page_id = quality.get("notion_page_id")
    expected_verified_at = quality.get("notion_last_verified_at")
    expected_content_digest = quality.get("expected_notion_content_digest")
    content_digest = quality.get("notion_content_digest")
    roundtrip_digest = quality.get("notion_roundtrip_digest")
    artifact_digest = quality.get("artifact_digest")
    if not isinstance(expected_page_id, str) or not isinstance(
        expected_verified_at, str
    ):
        raise ContractError("Notion Q2 identity details are incomplete")
    if not all(
        isinstance(value, str)
        for value in (
            expected_content_digest,
            content_digest,
            roundtrip_digest,
            artifact_digest,
        )
    ):
        raise ContractError("Notion Q2 digest details are incomplete")
    q2_target_id = quality.get("notion_target_id")
    if not isinstance(q2_target_id, str):
        raise ContractError("Notion Q2 target ID is missing")
    if q2_target_id != configured_data_source_id:
        raise ContractError("Notion Q2 target ID does not match current data source")
    verified_at = parse_aware_datetime(notion_verified_at, "notion_verified_at")
    expected_verified = parse_aware_datetime(
        expected_verified_at, "notion_last_verified_at"
    )
    if (
        request.target_id != blog_id
        or notion_page_id != expected_page_id
        or verified_at != expected_verified
        or artifact_digest != manifest.artifact_digest
        or expected_content_digest != content_digest
        or content_digest != roundtrip_digest
        or request.expected_notion_content_digest != expected_content_digest
        or request.notion_content_digest != content_digest
        or request.notion_roundtrip_digest != roundtrip_digest
        or request.q2_artifact_digest != artifact_digest
    ):
        raise ContractError(
            "Notion Q2 page, verification time, blog id, or digest identity does not match"
        )


def verify_article_quality(
    root: Path, events: list[JSONMap], manifest: Manifest
) -> JSONMap:
    """Require a current, evidenced article review before any Naver operation."""
    q2_quality = _latest_q2_quality(events, manifest)
    q2_verified_at = q2_quality.get("notion_last_verified_at")
    if not isinstance(q2_verified_at, str):
        raise ContractError("Notion Q2 verification time is missing")
    assessment = load_assessment(
        root,
        run_id=manifest.run_id,
        topic_id=manifest.topic_id,
        artifact_digest=manifest.artifact_digest,
        q2_verified_at=q2_verified_at,
    )
    if not assessment.passed:
        raise ArticleQualityFailure(assessment)
    from tools.image_quality import validate_image_quality

    image_quality_path = (
        root / "assets" / _manifest_keyword(manifest) / "image-quality.jsonl"
    )
    image_result = validate_image_quality(image_quality_path)
    expected_images = [
        f"sha256:{entry.sha256}"
        for entry in manifest.files
        if entry.role in {"body_image", "thumbnail"}
    ]
    image_records = _load_image_quality_records(image_quality_path)
    image_digests = [record.get("image_sha256") for record in image_records]
    string_image_digests = {
        digest for digest in image_digests if isinstance(digest, str)
    }
    for record in image_records:
        reviewed_at = record.get("reviewed_at")
        if (
            record.get("run_id") != manifest.run_id
            or record.get("article_quality_report_digest")
            != assessment.report_digest
            or not isinstance(reviewed_at, str)
        ):
            raise ContractError(
                "Q3 image assessments are not bound to the current run review"
            )
        if parse_aware_datetime(reviewed_at, "Q3 reviewed_at") <= parse_aware_datetime(
            q2_verified_at, "notion_last_verified_at"
        ):
            raise ContractError("Q3 image review must occur after Notion Q2")
    if (
        any(not isinstance(digest, str) for digest in image_digests)
        or len(image_digests) != len(expected_images)
        or len(string_image_digests) != len(image_digests)
        or string_image_digests != set(expected_images)
        or len(set(expected_images)) != len(expected_images)
    ):
        raise ContractError(
            "Q3 image assessments do not match the current manifest images"
        )
    return {
        "article_quality_score": assessment.total_score,
        "article_quality_rubric_version": "article-quality-v1",
        "article_quality_reviewer": assessment.reviewer,
        "article_quality_report_digest": assessment.report_digest,
        "image_quality_records": image_result["records"],
        "image_quality_passed": image_result["passed"],
    }


def _manifest_keyword(manifest: Manifest) -> str:
    final = next(entry for entry in manifest.files if entry.role == "final_markdown")
    return Path(final.path).stem


def _load_image_quality_records(path: Path) -> list[JSONMap]:
    records: list[JSONMap] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ContractError(f"invalid Q3 image record at {path}:{line_number}") from error
        records.append(as_map(record, f"Q3 image record at {path}:{line_number}"))
    return records


def canonical_naver_input(manifest: Manifest, root: Path) -> NaverCanonicalInput:
    input_files = [item for item in manifest.files if item.role == "naver_input"]
    if len(input_files) != 1:
        raise ContractError("manifest is missing canonical Naver input")
    body = (root / input_files[0].path).read_text(encoding="utf-8")
    lines = iter(body.splitlines())
    title: str | None = None
    for line in lines:
        if line.strip() != "[TITLE]":
            continue
        for candidate in lines:
            value = candidate.strip()
            if value:
                title = value
                break
        break
    if title is None:
        raise ContractError("canonical Naver input title is missing")
    return NaverCanonicalInput(title, body)


def authorize_naver_target(
    request: NaverToolRequest, config: NaverConfig, canonical: NaverCanonicalInput
) -> None:
    if request.target_id != config.blog_id or request.blog_id != config.blog_id:
        raise ContractError("Naver write target does not match naver-config.md")
    if request.connector != config.browser_connector:
        raise ContractError("Naver writes are limited to the configured browser connector")
    target = (
        request.operation,
        request.url,
        request.locator,
        request.value,
    )
    prepare_targets = {
        ("goto", config.write_url, None, None),
        ("text", None, config.auth_locator, None),
        ("fill", None, config.title_locator, canonical.title),
        ("fill", None, config.body_locator, canonical.body),
    }
    save_targets = {
        ("click", None, config.save_locator, None),
        ("goto", config.drafts_url, None, None),
        ("text", None, config.draft_list_locator, None),
    }
    if request.phase == "prepare" and target in prepare_targets:
        return
    if request.phase == "save" and target in save_targets:
        return
    raise ContractError("Naver browser operation, phase, or target is not allowed")


def verify_naver_confirmation(
    events: list[JSONMap], manifest: Manifest, blog_id: str | None, title: str
) -> None:
    latest: JSONMap | None = None
    for event in reversed(events):
        if (
            event.get("run_id") == manifest.run_id
            and event.get("event_type") == "confirmation"
        ):
            latest = event
            break
    if latest is None:
        raise ContractError("Naver draft save requires explicit user confirmation")
    latest_preparation: JSONMap | None = None
    for event in reversed(events):
        if (
            event.get("run_id") == manifest.run_id
            and event.get("topic_id") == manifest.topic_id
            and event.get("stage") == "naver-rider"
        ):
            latest_preparation = event
            break
    if latest_preparation is None:
        raise ContractError("latest Naver preparation identity is missing")
    preparation_quality = latest_preparation.get("quality")
    if (
        latest_preparation.get("status")
        not in {"passed", "success", "completed"}
        or not isinstance(preparation_quality, dict)
        or not isinstance(
            preparation_quality.get("confirmation_request_digest"), str
        )
    ):
        raise ContractError("latest Naver preparation identity is missing")
    quality = _latest_q2_quality(events, manifest)
    verified_at = quality.get("notion_last_verified_at")
    if not isinstance(verified_at, str):
        raise ContractError("Notion Q2 verification time is missing")
    confirmed_at = parse_aware_datetime(latest.get("confirmed_at"), "confirmed_at")
    if (
        latest.get("confirmation_request_digest")
        != preparation_quality.get("confirmation_request_digest")
    ):
        raise ContractError(
            "Naver confirmation does not match latest Naver preparation"
        )
    if (
        latest.get("action") != "naver-draft-save"
        or latest.get("target_blog_id") != blog_id
        or latest.get("title") != title
        or latest.get("artifact_digest") != manifest.artifact_digest
        or confirmed_at < parse_aware_datetime(verified_at, "notion_last_verified_at")
    ):
        raise ContractError("Naver confirmation does not match run, target, or artifact")


__all__ = [
    "NaverCanonicalInput",
    "NaverToolRequest",
    "authorize_naver_target",
    "canonical_naver_input",
    "verify_article_quality",
    "verify_naver_confirmation",
    "verify_q2_identity",
]

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.manifest import Manifest, verify_manifest
from tools.publication_metrics_store import (
    PublicationCommit,
    PublicationMetricsStore,
    validate_publication_run_id,
)
from tools.runner_state import state_paths
from tools.topic_feedback_models import (
    compute_digest,
    parse_artifact,
    serialize_artifact,
)

KST = ZoneInfo("Asia/Seoul")
CURRENT_SCORE_VERSION = "topic-baseline-v1"


@dataclass(frozen=True, slots=True)
class PublicationAttributionRequest:
    root: Path
    run_id: str
    blog_post_id: str | None
    published_at: str | None
    captured_at: str
    source_identity: Literal["current-run", "legacy-import"]
    topic_id: str | None = None
    keyword: str | None = None
    artifact_digest: str | None = None
    score_version: str | None = None
    legacy_identity: str | None = None
    naver_post_url: str | None = None
    url_rule_approval: Path | None = None
    url_rule_approval_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class _Identity:
    run_id: str
    topic_id: str
    keyword: str
    artifact_digest: str
    score_version: str
    legacy_identity: str | None
    historical_url: str | None
    historical_published_at: str | None
    target_blog_id: str | None


def _json_map(path: Path, label: str) -> JSONMap:
    if path.is_symlink():
        raise ContractError(f"{label} must not be a symlink")
    try:
        value: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"{label} is unreadable") from error
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be a JSON object")
    return value


def _required_text(value: JSONValue, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} is required")
    return value


def _kst_timestamp(value: str | None, label: str) -> str:
    if value is None:
        raise ContractError(f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContractError(f"{label} is not a valid ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{label} must include a timezone")
    return parsed.astimezone(KST).isoformat(timespec="seconds")


def _safe_relative(root: Path, raw_path: str, label: str) -> Path:
    relative = Path(raw_path)
    if relative.is_absolute() or len(relative.parts) == 0 or ".." in relative.parts:
        raise ContractError(f"{label} path escapes root")
    path = root / relative
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ContractError(f"{label} must not use a symlink")
    return path


def _manifest_identity(root: Path, state: JSONMap, run_id: str) -> tuple[Manifest, str]:
    manifest_value = _required_text(state.get("manifest_path"), "manifest_path")
    manifest_path = _safe_relative(root, manifest_value, "manifest")
    manifest = verify_manifest(root, manifest_path)
    keyword = _required_text(state.get("keyword"), "keyword")
    final_path = f"final/{keyword}.md"
    if not any(entry.role == "final_markdown" and entry.path == final_path for entry in manifest.files):
        raise ContractError("run keyword does not match the frozen manifest")
    if manifest.run_id != run_id or manifest.topic_id != state.get("topic_id"):
        raise ContractError("run identity does not match the frozen manifest")
    if manifest.artifact_digest != state.get("artifact_digest"):
        raise ContractError("run artifact digest does not match the frozen manifest")
    return manifest, keyword


def _historical_link(root: Path, run_id: str) -> tuple[str | None, str | None]:
    path = root / ".automation" / "publications" / f"{run_id}.json"
    if not path.exists():
        return None, None
    value = _json_map(path, "historical publication link")
    if value.get("run_id") != run_id or value.get("publication_link_status") != "matched":
        raise ContractError("historical publication link identity is invalid")
    url = value.get("naver_post_url")
    published_at = value.get("published_at")
    return (
        url if isinstance(url, str) and url else None,
        published_at if isinstance(published_at, str) and published_at else None,
    )


def _current_identity(request: PublicationAttributionRequest) -> _Identity:
    state_path, _, _ = state_paths(request.root, request.run_id)
    state = _json_map(state_path, "runner state")
    if state.get("run_id") != request.run_id or state.get("status") != "draft_saved":
        raise ContractError("publication attribution requires the frozen draft_saved run")
    manifest, keyword = _manifest_identity(request.root, state, request.run_id)
    if request.artifact_digest is not None and request.artifact_digest != manifest.artifact_digest:
        raise ContractError("stale artifact digest")
    if state.get("score_version") != CURRENT_SCORE_VERSION:
        raise ContractError("current run score_version is not the trusted baseline")
    if request.score_version != CURRENT_SCORE_VERSION:
        raise ContractError("caller score_version is not the trusted baseline")
    score_version = CURRENT_SCORE_VERSION
    if any(value is not None and value != (manifest.topic_id, keyword, score_version)[index] for index, value in enumerate((request.topic_id, request.keyword, request.score_version))):
        raise ContractError("caller identity does not match the frozen run")
    target_blog_id_value = state.get("target_blog_id")
    target_blog_id = (
        _required_text(target_blog_id_value, "target_blog_id")
        if target_blog_id_value is not None
        else None
    )
    url, published_at = _historical_link(request.root, request.run_id)
    return _Identity(
        request.run_id,
        manifest.topic_id,
        keyword,
        manifest.artifact_digest,
        score_version,
        None,
        url,
        published_at,
        target_blog_id,
    )


def _legacy_identity(request: PublicationAttributionRequest) -> _Identity:
    _ = state_paths(request.root, request.run_id)
    return _Identity(
        _required_text(request.run_id, "run_id"),
        _required_text(request.topic_id, "topic_id"),
        _required_text(request.keyword, "keyword"),
        _required_text(request.artifact_digest, "artifact_digest"),
        _required_text(request.score_version, "score_version"),
        _required_text(request.legacy_identity, "legacy_identity"),
        None,
        None,
        None,
    )


def _approved_url_post_id(
    request: PublicationAttributionRequest,
    url: str | None,
    expected_blog_id: str | None,
) -> str | None:
    if request.url_rule_approval is None and request.url_rule_approval_sha256 is None:
        return None
    if request.url_rule_approval is None or request.url_rule_approval_sha256 is None:
        raise ContractError("URL rule approval path and digest must be supplied together")
    raw = request.url_rule_approval.read_bytes()
    actual = "sha256:" + hashlib.sha256(raw).hexdigest()
    if actual != request.url_rule_approval_sha256:
        raise ContractError("URL rule approval digest mismatch")
    approval = _json_map(request.url_rule_approval, "URL rule approval")
    expected_keys = {
        "schema_version", "approval_status", "official_reference_url",
        "host", "path_template", "approved_at",
    }
    if set(approval) != expected_keys or approval.get("schema_version") != "naver-url-rule-approval-v1" or approval.get("approval_status") != "approved":
        raise ContractError("URL rule approval is invalid")
    reference = _required_text(approval.get("official_reference_url"), "official_reference_url")
    if not reference.startswith("https://help.naver.com/"):
        raise ContractError("URL rule approval is not backed by an official reference")
    _ = _kst_timestamp(_required_text(approval.get("approved_at"), "approved_at"), "approved_at")
    if approval.get("host") != "blog.naver.com" or approval.get("path_template") != "/{blog_id}/{blog_post_id}":
        raise ContractError("URL rule approval is invalid")
    if url is None:
        return None
    parsed = urlsplit(url)
    parts = tuple(unquote(part) for part in parsed.path.split("/") if part)
    if parsed.scheme != "https" or parsed.hostname != "blog.naver.com" or parsed.query or parsed.fragment or len(parts) != 2:
        raise ContractError("Naver URL does not exactly match the approved rule")
    blog_id = _required_text(parts[0], "approved blog_id")
    if request.source_identity == "current-run" and (
        expected_blog_id is None or blog_id != expected_blog_id
    ):
        raise ContractError("Naver URL blog_id does not match frozen target_blog_id")
    return _required_text(parts[1], "approved blog_post_id")


def link_publication(request: PublicationAttributionRequest) -> JSONMap:
    _ = validate_publication_run_id(request.run_id)
    match request.source_identity:  # noqa: RUF100  # noqa: MATCH_OK
        case "current-run":
            identity = _current_identity(request)
        case "legacy-import":
            identity = _legacy_identity(request)
    published_at = _kst_timestamp(request.published_at, "published_at")
    if identity.historical_published_at is not None and _kst_timestamp(identity.historical_published_at, "published_at") != published_at:
        raise ContractError("published_at conflicts with the historical publication link")
    source_url = request.naver_post_url or identity.historical_url
    parsed_post_id = _approved_url_post_id(
        request, source_url, identity.target_blog_id
    )
    explicit_post_id = _required_text(request.blog_post_id, "blog_post_id") if request.blog_post_id is not None else None
    if explicit_post_id is not None and parsed_post_id is not None and explicit_post_id != parsed_post_id:
        raise ContractError("explicit blog_post_id conflicts with approved URL identity")
    post_id = explicit_post_id or parsed_post_id
    missing: list[JSONValue] = (
        ["legacy_identity"] if identity.legacy_identity is None else []
    )
    if post_id is None:
        missing.insert(0, "blog_post_id")
    payload: JSONMap = {
        "schema_version": "publication-link-v1",
        "captured_at": _kst_timestamp(request.captured_at, "captured_at"),
        "as_of_date": published_at[:10],
        "timezone": "Asia/Seoul",
        "limitations": ([] if post_id is not None else ["blog_post_id remains unlinked without an approved explicit identity"]),
        "input_digests": [identity.artifact_digest],
        "missing_fields": missing,
        "status": "mature" if post_id is not None else "pending",
        "run_id": identity.run_id,
        "topic_id": identity.topic_id,
        "keyword": identity.keyword,
        "blog_post_id": post_id,
        "published_at": published_at,
        "artifact_digest": identity.artifact_digest,
        "score_version": identity.score_version,
        "source_identity": request.source_identity,
        "legacy_identity": identity.legacy_identity,
        "digest": "",
    }
    payload["digest"] = compute_digest(payload)
    artifact = parse_artifact(payload)
    encoded = serialize_artifact(artifact).encode("utf-8")
    commit = PublicationCommit(request.run_id, post_id, encoded)
    with PublicationMetricsStore(request.root) as store:
        stored = store.commit(commit)
    stored_value: JSONValue = json.loads(stored.encoded)
    if not isinstance(stored_value, dict):
        raise ContractError("stored publication attribution is invalid")
    stored_payload = parse_artifact(stored_value).payload
    return {
        "path": str(stored.path),
        "digest": stored_payload["digest"],
        "publication_link": stored_payload,
    }

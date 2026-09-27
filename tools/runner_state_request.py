from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Final, Literal

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.model_presets import (
    ModelConfigSnapshot,
    parse_model_config_snapshot,
)
from tools.runner_types import RunnerRequest, TopicSelectionContext

_FEEDBACK_MODES: Final[dict[str, Literal["baseline", "shadow", "active"]]] = {
    "baseline": "baseline",
    "shadow": "shadow",
    "active": "active",
}
_SNAPSHOT_POLICIES: Final[dict[str, Literal["capture_once", "reuse_only"]]] = {
    "capture_once": "capture_once",
    "reuse_only": "reuse_only",
}


def _safe_now(now: datetime | None) -> datetime:
    value = now if now is not None else datetime.now().astimezone()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError("runner timestamps must include a timezone")
    return value


def stable_run_id(request: RunnerRequest) -> str:
    now = _safe_now(request.now)
    topic = "auto-topic" if request.auto_topic else request.keyword or ""
    context = request.selection_context
    material = "|".join(
        (
            request.job,
            topic,
            now.date().isoformat(),
            context.batch_id
            if context is not None and context.batch_id is not None
            else "",
            str(context.batch_slot)
            if context is not None and context.batch_slot is not None
            else "",
        )
    )
    return "RUN-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_fingerprint(request: RunnerRequest) -> str:
    records: list[JSONValue] = []
    if request.keyword is not None and not request.auto_topic:
        keyword_parts = Path(request.keyword).parts
        if (
            not request.keyword
            or Path(request.keyword).is_absolute()
            or len(keyword_parts) != 1
            or keyword_parts[0] in {".", ".."}
        ):
            raise ContractError("keyword must be a single safe path component")
        keyword_dir = request.root / "assets" / request.keyword
        candidates = [
            request.root / "research" / f"{request.keyword}.md",
            request.root / "research" / f"topic-selection-{request.keyword}.md",
            request.root / "drafts" / f"{request.keyword}.md",
            request.root / "final" / f"{request.keyword}.md",
            request.root / "final" / f"{request.keyword}-naver-layout.md",
            request.root / "final" / f"{request.keyword}-naver-copy.md",
        ]
        if keyword_dir.is_dir():
            candidates.extend(
                path
                for path in sorted(keyword_dir.rglob("*"))
                if path.is_file()
                and path.name != "image-quality.jsonl"
                and not path.name.startswith("q3-mobile")
            )
        for path in sorted(set(candidates)):
            record: JSONMap = {
                "path": path.relative_to(request.root).as_posix(),
                "sha256": file_digest(path) if path.is_file() else None,
            }
            records.append(record)
    else:
        directories = (
            ()
            if request.auto_topic
            else ("research", "drafts", "final", "assets", "runs")
        )
        for directory in directories:
            path = request.root / directory
            files = (
                [item for item in sorted(path.rglob("*")) if item.is_file()]
                if path.is_dir()
                else []
            )
            records.extend(
                {
                    "path": item.relative_to(request.root).as_posix(),
                    "sha256": file_digest(item),
                }
                for item in files
            )
        if request.auto_topic:
            for name in (
                "notion-config.md",
                "naver-config.md",
                "schemas/workflow-contract.schema.json",
            ):
                path = request.root / name
                if path.is_file():
                    records.append({"path": name, "sha256": file_digest(path)})
    material: JSONMap = {
        "job": request.job,
        "keyword": "auto-topic" if request.auto_topic else request.keyword,
        "notion_target_id": request.notion_target_id,
        "selection_context": (
            request.selection_context.as_json()
            if request.selection_context is not None
            else None
        ),
        "inputs": records,
    }
    if request.model_config is not None:
        material["model_config"] = request.model_config.as_json()
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _selection_context(value: JSONValue) -> TopicSelectionContext | None:
    if not isinstance(value, dict):
        return None
    category = value.get("category", "")
    audience = value.get("audience", "")
    publish_purpose = value.get("publish_purpose", "")
    as_of_date = value.get("as_of_date")
    timezone = value.get("timezone")
    batch_id = value.get("batch_id")
    batch_slot = value.get("batch_slot")
    snapshot_policy = value.get("snapshot_policy")
    capture_id = value.get("capture_id")
    snapshot_path = value.get("snapshot_path")
    snapshot_sha256 = value.get("snapshot_sha256")
    excluded_keywords = value.get("excluded_keywords", [])
    score_version = value.get("score_version")
    score_config_digest = value.get("score_config_digest")
    feedback_manifest_digest = value.get("feedback_manifest_digest")
    feedback_selection_mode = value.get("feedback_selection_mode")
    parsed_feedback_mode = (
        _FEEDBACK_MODES.get(feedback_selection_mode)
        if isinstance(feedback_selection_mode, str)
        else None
    )
    parsed_snapshot_policy = (
        _SNAPSHOT_POLICIES.get(snapshot_policy)
        if isinstance(snapshot_policy, str)
        else None
    )
    parsed_exclusions = (
        tuple(item for item in excluded_keywords if isinstance(item, str))
        if isinstance(excluded_keywords, list)
        else ()
    )
    if not (
        isinstance(category, str)
        and isinstance(audience, str)
        and isinstance(publish_purpose, str)
        and isinstance(as_of_date, str)
        and isinstance(timezone, str)
        and (batch_id is None or isinstance(batch_id, str))
        and (batch_slot is None or isinstance(batch_slot, int))
        and (snapshot_policy is None or parsed_snapshot_policy is not None)
        and (capture_id is None or isinstance(capture_id, str))
        and (snapshot_path is None or isinstance(snapshot_path, str))
        and (snapshot_sha256 is None or isinstance(snapshot_sha256, str))
        and isinstance(excluded_keywords, list)
        and len(parsed_exclusions) == len(excluded_keywords)
        and (score_version is None or isinstance(score_version, str))
        and (score_config_digest is None or isinstance(score_config_digest, str))
        and (
            feedback_manifest_digest is None
            or isinstance(feedback_manifest_digest, str)
        )
        and (feedback_selection_mode is None or parsed_feedback_mode is not None)
    ):
        return None
    return TopicSelectionContext(
        category,
        audience,
        publish_purpose,
        as_of_date,
        timezone,
        batch_id,
        batch_slot,
        parsed_snapshot_policy,
        capture_id,
        snapshot_path,
        snapshot_sha256,
        parsed_exclusions,
        score_version,
        score_config_digest,
        feedback_manifest_digest,
        parsed_feedback_mode,
    )


def _model_config(value: JSONValue) -> ModelConfigSnapshot | None:
    if value is None:
        return None
    return parse_model_config_snapshot(value)


def request_from_state(
    state: JSONMap, root: Path, state_dir: Path | None = None
) -> RunnerRequest:
    job = state.get("job")
    keyword = state.get("keyword")
    run_id = state.get("run_id")
    notion_target_id = state.get("notion_target_id")
    if (
        not isinstance(job, str)
        or (keyword is not None and not isinstance(keyword, str))
        or not isinstance(run_id, str)
        or (notion_target_id is not None and not isinstance(notion_target_id, str))
    ):
        raise ContractError(
            "runner state cannot be recovered: request fields are invalid"
        )
    return RunnerRequest(
        root=root,
        job=job,
        keyword=keyword,
        run_id=run_id,
        dry_run=state.get("dry_run") is True,
        auto_save_naver=state.get("auto_save_naver") is True,
        state_dir=state_dir,
        auto_topic=state.get("auto_topic") is True,
        selection_context=_selection_context(state.get("selection_context")),
        confirmed=state.get("confirmation") is not None,
        resume=True,
        notion_target_id=notion_target_id,
        model_config=_model_config(state.get("model_config")),
    )


__all__ = ["file_digest", "input_fingerprint", "request_from_state", "stable_run_id"]

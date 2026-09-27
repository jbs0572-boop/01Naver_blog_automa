from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.article_quality import (
    QUALITY_REPORT_VERSION,
    RUBRIC_VERSION,
    SCORE_MAXIMA,
    assessment_digest,
    assessment_path,
)
from tools.contract_types import JSONMap

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    + "0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082"
)


def install_passing_quality_review(
    root: Path,
    *,
    run_id: str,
    topic_id: str,
    artifact_digest: str,
    manifest: JSONMap,
    reviewed_at: str,
) -> Path:
    """Install a passing human-review fixture bound to its run artifacts."""
    payload: JSONMap = {
        "report_version": QUALITY_REPORT_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "run_id": run_id,
        "topic_id": topic_id,
        "artifact_digest": artifact_digest,
        "reviewer": "test-reviewer",
        "reviewed_at": reviewed_at,
        "scores": dict(SCORE_MAXIMA),
        "evidence": {name: "fixture evidence checked" for name in SCORE_MAXIMA},
        "immediate_failures": [],
        "cause_type": None,
        "failure_stage": None,
        "next_action": "none",
    }
    payload["report_digest"] = assessment_digest(payload)
    path = assessment_path(root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(payload), encoding="utf-8")
    image_quality_path = root / "assets" / _manifest_keyword(manifest) / "image-quality.jsonl"
    image_quality_path.parent.mkdir(parents=True, exist_ok=True)
    render_path = image_quality_path.parent / "q3-mobile.png"
    _ = render_path.write_bytes(_PNG)
    render_digest = "sha256:" + hashlib.sha256(_PNG).hexdigest()
    files = manifest.get("files")
    image_records: list[JSONMap] = []
    if isinstance(files, list):
        for entry in files:
            if not isinstance(entry, dict) or entry.get("role") not in {"body_image", "thumbnail"}:
                continue
            image_sha = entry.get("sha256")
            if not isinstance(image_sha, str):
                continue
            image_records.append({
                "image_sha256": "sha256:" + image_sha,
                "automated_checks": {key: "passed" for key in (
                    "decode_check", "duplicate_check", "ocr_check",
                    "visual_contract_check", "mobile_render_check",
                )},
                "scores": {
                    "subject_relevance": 4,
                    "composition_legibility": 4,
                    "rendering_completion": 4,
                    "information_contribution": 4,
                    "style_consistency": 4,
                },
                "immediate_failure": False,
                "mobile_rendered": True,
                "mobile_viewport": "390x844",
                "mobile_render_path": "q3-mobile.png",
                "mobile_render_sha256": render_digest,
                "human_verdict": "passed",
            })
    _ = image_quality_path.write_text(
        "".join(json.dumps(record) + "\n" for record in image_records),
        encoding="utf-8",
    )
    return path


def _manifest_keyword(manifest: JSONMap) -> str:
    files = manifest.get("files")
    if isinstance(files, list):
        for entry in files:
            if isinstance(entry, dict) and entry.get("role") == "final_markdown":
                path = entry.get("path")
                if isinstance(path, str):
                    return Path(path).stem
    raise ValueError("manifest has no final markdown")

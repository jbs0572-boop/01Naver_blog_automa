from __future__ import annotations

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


def install_passing_quality_review(
    root: Path,
    *,
    run_id: str,
    topic_id: str,
    artifact_digest: str,
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
    return path

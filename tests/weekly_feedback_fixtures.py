from __future__ import annotations

import json
from pathlib import Path

from tools.contract_types import JSONMap
from tools.topic_feedback_models import compute_digest
from tools.topic_metadata import (
    CreatorAdvisorCandidate,
    CreatorAdvisorSnapshot,
    write_snapshot,
)


def _artifact(root: Path, relative: str, payload: JSONMap) -> Path:
    payload["digest"] = compute_digest(payload)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    return path


def install_weekly_inputs(root: Path, *, search_inflow: int = 12) -> tuple[Path, ...]:
    published = "2026-08-01T09:00:00+09:00"
    link: JSONMap = {
        "schema_version": "publication-link-v1",
        "captured_at": published,
        "as_of_date": "2026-08-01",
        "timezone": "Asia/Seoul",
        "limitations": [],
        "input_digests": ["sha256:" + "a" * 64],
        "missing_fields": ["legacy_identity"],
        "status": "mature",
        "digest": "",
        "run_id": "RUN-WEEKLY-001",
        "topic_id": "TOPIC-WEEKLY-001",
        "keyword": "주간 후보",
        "blog_post_id": "POST-WEEKLY-001",
        "published_at": published,
        "artifact_digest": "sha256:" + "a" * 64,
        "score_version": "topic-baseline-v1",
        "source_identity": "current-run",
        "legacy_identity": None,
    }
    link_path = _artifact(
        root, "metadata/publication-links/RUN-WEEKLY-001/link.json", link
    )
    link_digest = str(link["digest"])
    paths = [link_path]
    for day, capture, views, inflow in (
        ("2026-08-08", "CAP-7D", 30, 5),
        ("2026-08-29", "CAP-28D", 90, search_inflow),
    ):
        stat: JSONMap = {
            "schema_version": "blog-stat-snapshot-v2",
            "captured_at": f"{day}T09:00:00+09:00",
            "as_of_date": day,
            "timezone": "Asia/Seoul",
            "limitations": ["manual_import"],
            "input_digests": [link_digest],
            "missing_fields": ["average_exposure_rank", "exposure"],
            "status": "mature",
            "digest": "",
            "mapping_version": "blog-stats-columns-v1",
            "capture_id": capture,
            "blog_id": "owner",
            "blog_post_id": "POST-WEEKLY-001",
            "publication_run_id": "RUN-WEEKLY-001",
            "publication_link_digest": link_digest,
            "coverage_start": "2026-08-01",
            "coverage_end": day,
            "views": views,
            "search_inflow": inflow,
            "exposure": None,
            "average_exposure_rank": None,
            "demographics": [],
        }
        paths.append(
            _artifact(root, f"metadata/blog-stats/owner/{day}/{capture}.json", stat)
        )
    paths.append(
        write_snapshot(
            root,
            CreatorAdvisorSnapshot(
                as_of_date="2026-09-07",
                captured_at="2026-09-07T09:00:00+09:00",
                capture_id="CAP-WEEKLY",
                candidates=(
                    CreatorAdvisorCandidate("첫 후보", 1),
                    CreatorAdvisorCandidate("둘째 후보", 2),
                ),
            ),
        )
    )
    return tuple(paths)

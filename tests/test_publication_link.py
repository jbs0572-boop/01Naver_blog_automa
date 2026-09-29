from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tools.contract_types import JSONMap
from tools.external_adapter import notion_content_digest, verify_notion_round_trip
from tools.publication_link import PublicationCandidate, collect_publication_link


def _state_root(tmp_path: Path) -> tuple[Path, str]:
    run_id = "RUN-publication"
    (tmp_path / "final").mkdir()
    _ = (tmp_path / "final" / "topic-naver-input.md").write_text(
        "# Topic title\n\nbody\n", encoding="utf-8"
    )
    state = {
        "run_id": run_id,
        "status": "draft_saved",
        "keyword": "topic",
        "topic_id": "TOPIC-topic",
        "target_blog_id": "blog-1",
        "naver_title": "Topic title",
        "updated_at": "2026-08-29T09:00:00+00:00",
    }
    state_path = tmp_path / ".automation" / "state" / f"{run_id}.json"
    state_path.parent.mkdir(parents=True)
    _ = state_path.write_text(json.dumps(state), encoding="utf-8")
    return tmp_path, run_id


def test_publication_link_matches_one_candidate_without_writes(tmp_path: Path) -> None:
    root, run_id = _state_root(tmp_path)
    candidate = PublicationCandidate(
        "blog-1",
        "https://blog.example/1",
        "2026-08-29T10:00:00+00:00",
        "Topic title",
        "# Topic title\n\nbody\n",
    )

    result = collect_publication_link(
        root, run_id, (candidate,), "2026-08-29T11:00:00+00:00"
    )

    assert result["publication_link_status"] == "matched"
    assert result["match_method"] == "title_and_body"
    assert result["primary_keyword"] == "topic"


def test_notion_round_trip_digest_excludes_block_identity() -> None:
    expected: JSONMap = {
        "title": "Title",
        "blocks": [
            {
                "id": "one",
                "type": "image",
                "image": {"artifact_role": "thumbnail", "original_sha256": "abc"},
            }
        ],
    }
    actual: JSONMap = {
        "title": "Title",
        "blocks": [
            {
                "id": "two",
                "created_time": "later",
                "type": "image",
                "image": {"artifact_role": "thumbnail", "original_sha256": "abc"},
            }
        ],
    }

    assert notion_content_digest(expected) == notion_content_digest(actual)
    result = verify_notion_round_trip(
        expected, actual, "page-1", datetime.now(UTC).isoformat(), "sha256:" + "a" * 64
    )
    assert result["storage_integrity"] == "passed"

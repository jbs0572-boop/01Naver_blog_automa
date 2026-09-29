from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.contract_types import ContractError, JSONMap
from tools.topic_feedback_models import compute_digest, parse_artifact
from tools.topic_feedback_store import (
    SnapshotLocation,
    TopicFeedbackStore,
    encode_snapshot,
)


def _signal() -> JSONMap:
    payload: JSONMap = {
        "schema_version": "topic-signal-snapshot-v1",
        "captured_at": "2026-09-01T00:00:00+09:00",
        "as_of_date": "2026-09-01",
        "timezone": "Asia/Seoul",
        "limitations": ["relative_index_only", "no_candidate_expansion"],
        "input_digests": [],
        "missing_fields": [],
        "status": "pending",
        "source_id": "naver-datalab",
        "source_confidence": "A",
        "access_mode": "official_api",
        "query_period": "2026-08-01/2026-08-31",
        "terms_checked_at": "2026-09-08T00:00:00+09:00",
        "raw_payload": {"results": [{"relative_ratio": 72, "keyword": "alpha"}]},
        "derived": {},
        "digest": "",
    }
    payload["digest"] = compute_digest(payload)
    return payload


def test_store_publishes_one_canonical_snapshot_and_strictly_rereads(
    tmp_path: Path,
) -> None:
    # Given: a valid typed topic-signal capture and safe logical location.
    artifact = parse_artifact(_signal())
    location = SnapshotLocation.topic_signal(
        "naver-datalab", "2026-09-01", "CAPTURE-001"
    )

    # When: the repository commits it under an existing project root.
    with TopicFeedbackStore(tmp_path) as store:
        stored = store.store(location, artifact)
        reread = store.read_strict(location)

    # Then: canonical bytes and the validated digest are identical.
    assert stored.path == (
        tmp_path / "metadata/topic-signals/naver-datalab/2026-09-01/CAPTURE-001.json"
    )
    assert stored.encoded == reread.encoded == encode_snapshot(artifact)
    assert stored.digest == reread.digest == _signal()["digest"]
    assert stored.path.read_bytes() == stored.encoded


def test_store_rejects_duplicate_capture_even_when_payload_is_identical(
    tmp_path: Path,
) -> None:
    # Given: one immutable capture already committed.
    artifact = parse_artifact(_signal())
    location = SnapshotLocation.topic_signal(
        "naver-datalab", "2026-09-01", "CAPTURE-001"
    )
    with TopicFeedbackStore(tmp_path) as store:
        first = store.store(location, artifact)
        before = hashlib.sha256(first.path.read_bytes()).hexdigest()

        # When/Then: replaying the identity is an explicit append-only conflict.
        with pytest.raises(ContractError, match="snapshot is append-only"):
            _ = store.store(location, artifact)

    assert hashlib.sha256(first.path.read_bytes()).hexdigest() == before
    assert not tuple(first.path.parent.glob(".tmp-*"))


@pytest.mark.parametrize("unsafe", ["../escape", ".hidden", "a/b", "/absolute"])
def test_location_rejects_path_escape_components(tmp_path: Path, unsafe: str) -> None:
    # Given: an attacker-controlled capture identity containing path syntax.
    artifact = parse_artifact(_signal())

    # When/Then: path construction fails before creating metadata.
    with pytest.raises(ContractError, match="snapshot path escapes root"):
        location = SnapshotLocation.topic_signal("naver-datalab", "2026-09-01", unsafe)
        with TopicFeedbackStore(tmp_path) as store:
            _ = store.store(location, artifact)
    assert not (tmp_path / "metadata").exists()


def test_strict_reread_rejects_tampered_bytes(tmp_path: Path) -> None:
    # Given: a valid committed capture whose authoritative bytes are changed.
    artifact = parse_artifact(_signal())
    location = SnapshotLocation.topic_signal(
        "naver-datalab", "2026-09-01", "CAPTURE-001"
    )
    with TopicFeedbackStore(tmp_path) as store:
        stored = store.store(location, artifact)
        value = json.loads(stored.path.read_text(encoding="utf-8"))
        value["raw_payload"]["results"][0]["relative_ratio"] = 99
        _ = stored.path.write_text(json.dumps(value), encoding="utf-8")

        # When/Then: schema/digest validation detects the modification.
        with pytest.raises(ContractError, match="digest mismatch"):
            _ = store.read_strict(location)


def test_store_rejects_symlinked_metadata_without_touching_target(
    tmp_path: Path,
) -> None:
    # Given: metadata redirects outside the project root.
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "metadata").symlink_to(outside, target_is_directory=True)
    artifact = parse_artifact(_signal())
    location = SnapshotLocation.topic_signal(
        "naver-datalab", "2026-09-01", "CAPTURE-001"
    )

    # When/Then: no-follow directory opening fails closed.
    with (
        pytest.raises(ContractError, match="path is unsafe"),
        TopicFeedbackStore(tmp_path) as store,
    ):
        _ = store.store(location, artifact)
    assert list(outside.iterdir()) == []


def test_blog_stat_v2_location_uses_existing_blog_stats_namespace() -> None:
    location = SnapshotLocation.blog_stat("owner", "2026-09-07", "CAP-1")

    assert location.relative_path == Path(
        "metadata/blog-stats/owner/2026-09-07/CAP-1.json"
    )

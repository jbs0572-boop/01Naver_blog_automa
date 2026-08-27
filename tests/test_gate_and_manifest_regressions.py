from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.contract_types import PIPELINE_VERSION, ContractError, JSONMap, JSONValue
from tools.gate import verify_gate
from tools.manifest import build_manifest


def _fixture(tmp_path: Path, mode: str = "beta") -> tuple[Path, Path, JSONMap, str]:
    keyword = "fixture-topic"
    run_id = "RUN-fixture"
    final_dir = tmp_path / "final"
    asset_dir = tmp_path / "assets" / keyword
    final_dir.mkdir(parents=True)
    asset_dir.mkdir(parents=True)
    _ = (asset_dir / "body.png").write_bytes(b"body")
    _ = (asset_dir / "thumbnail.png").write_bytes(b"thumbnail")
    _ = (asset_dir / "image-map.md").write_text("# map\n", encoding="utf-8")
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    _ = (final_dir / f"{keyword}.md").write_text(
        "![body](../assets/fixture-topic/body.png)\n", encoding="utf-8"
    )
    _ = (final_dir / f"{keyword}-naver-layout.md").write_text(
        "# layout\n", encoding="utf-8"
    )
    _ = (final_dir / f"{keyword}-naver-copy.md").write_text(
        "# copy\n", encoding="utf-8"
    )
    manifest = build_manifest(
        tmp_path, keyword, run_id, "TOPIC-fixture", mode, "2026-08-27T00:00:00+00:00"
    )
    manifest_path = tmp_path / "manifest.json"
    _ = manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path, manifest_path, manifest, run_id


def _stage(run_id: str) -> JSONMap:
    return {
        "event_type": "stage",
        "pipeline_version": PIPELINE_VERSION,
        "batch_id": "BATCH-fixture",
        "run_id": run_id,
        "topic_id": "TOPIC-fixture",
        "stage": "content-assembler",
        "started_at": "2026-08-27T00:00:00+00:00",
        "ended_at": "2026-08-27T00:01:00+00:00",
        "status": "passed",
        "attempt": 1,
    }


def _approval(manifest: JSONMap, run_id: str, **overrides: JSONValue) -> JSONMap:
    approval: JSONMap = {
        "event_type": "approval",
        "pipeline_version": PIPELINE_VERSION,
        "run_id": run_id,
        "gate": "notion_write",
        "decision": "approved",
        "scope": "per-run",
        "target_id": "datasource-fixture",
        "artifact_digest": manifest["artifact_digest"],
        "requested_at": "2026-08-27T00:02:00+00:00",
        "decided_at": "2026-08-27T00:03:00+00:00",
    }
    approval.update(overrides)
    return approval


def _write_log(path: Path, events: list[JSONMap]) -> None:
    _ = path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )


def test_mixed_offset_approval_order_uses_actual_time(tmp_path: Path) -> None:
    root, manifest_path, manifest, run_id = _fixture(tmp_path)
    run_log = root / "run.jsonl"
    approved_later = _approval(manifest, run_id, decided_at="2026-08-27T08:00:00+00:00")
    rejected_earlier = _approval(
        manifest, run_id, decision="rejected", decided_at="2026-08-27T16:30:00+09:00"
    )
    _write_log(run_log, [_stage(run_id), approved_later, rejected_earlier])

    result = verify_gate(
        root, manifest_path, run_log, "notion_write", run_id, "datasource-fixture"
    )

    assert result["decision"] == "approved"


def test_rejected_approval_after_approved_blocks(tmp_path: Path) -> None:
    root, manifest_path, manifest, run_id = _fixture(tmp_path)
    run_log = root / "run.jsonl"
    approved = _approval(manifest, run_id, decided_at="2026-08-27T08:00:00+00:00")
    rejected_later = _approval(
        manifest, run_id, decision="rejected", decided_at="2026-08-27T17:30:00+09:00"
    )
    _write_log(run_log, [_stage(run_id), approved, rejected_later])

    with pytest.raises(ContractError, match="not approved"):
        _ = verify_gate(
            root, manifest_path, run_log, "notion_write", run_id, "datasource-fixture"
        )


def test_naive_approval_timestamp_is_not_accepted(tmp_path: Path) -> None:
    root, manifest_path, manifest, run_id = _fixture(tmp_path)
    run_log = root / "run.jsonl"
    legacy_approval = _approval(
        manifest, run_id, pipeline_version=None, decided_at="2026-08-27T08:00:00"
    )
    _write_log(run_log, [_stage(run_id), legacy_approval])

    with pytest.raises(ContractError):
        _ = verify_gate(
            root, manifest_path, run_log, "notion_write", run_id, "datasource-fixture"
        )


_BATCH_CASES: tuple[tuple[JSONMap, str], ...] = (
    (
        {"per_run_artifact_digests": {"RUN-other": "sha256:" + "0" * 64}},
        "exactly match",
    ),
    (
        {"per_run_artifact_digests": {"RUN-fixture": "sha256:" + "0" * 63 + "1"}},
        "does not match",
    ),
    ({"per_run_artifact_digests": None}, "missing"),
)


@pytest.mark.parametrize(
    ("override", "message"),
    _BATCH_CASES,
)
def test_batch_digest_map_requires_exact_keys_and_current_digest(
    tmp_path: Path, override: JSONMap, message: str
) -> None:
    root, manifest_path, manifest, run_id = _fixture(tmp_path)
    run_log = root / "run.jsonl"
    batch = _approval(
        manifest,
        run_id,
        scope="batch",
        run_ids=[run_id],
        max_items=1,
        per_run_artifact_digests={run_id: manifest["artifact_digest"]},
        pipeline_version=None,
    )
    batch.update(override)
    _write_log(run_log, [_stage(run_id), batch])

    with pytest.raises(ContractError, match=message):
        _ = verify_gate(
            root, manifest_path, run_log, "notion_write", run_id, "datasource-fixture"
        )


def test_manifest_rejects_path_traversal(tmp_path: Path) -> None:
    root, manifest_path, manifest, _ = _fixture(tmp_path)
    mutated: JSONMap = dict(manifest)
    manifest_files = manifest["files"]
    assert isinstance(manifest_files, list)
    files: list[JSONValue] = [item for item in manifest_files if isinstance(item, dict)]
    assert files and isinstance(files[0], dict)
    files[0]["path"] = "../outside.md"
    mutated["files"] = files
    _ = manifest_path.write_text(json.dumps(mutated), encoding="utf-8")

    with pytest.raises(ContractError):
        from tools.manifest import verify_manifest

        _ = verify_manifest(root, manifest_path)


def test_manifest_rejects_thumbnail_as_body_image(tmp_path: Path) -> None:
    root, _, _, _ = _fixture(tmp_path)
    final_path = root / "final" / "fixture-topic.md"
    _ = final_path.write_text(
        "![thumbnail](../assets/fixture-topic/thumbnail.png)\n", encoding="utf-8"
    )

    with pytest.raises(ContractError, match="thumbnail"):
        _ = build_manifest(
            root,
            "fixture-topic",
            "RUN-fixture",
            "TOPIC-fixture",
            "beta",
            "2026-08-27T00:00:00+00:00",
        )

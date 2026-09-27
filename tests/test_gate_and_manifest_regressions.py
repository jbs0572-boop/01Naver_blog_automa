from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.contract_types import PIPELINE_VERSION, ContractError, JSONMap, JSONValue
from tools.gate import GateRequest, verify_gate
from tools.log_contract import read_events
from tools.manifest import ManifestBuildInput, build_manifest


def _fixture(tmp_path: Path) -> tuple[Path, Path, JSONMap, str]:
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
    manifest = build_manifest(ManifestBuildInput(
        tmp_path, keyword, run_id, "TOPIC-fixture", "2026-08-27T00:00:00+00:00"
    ))
    manifest_path = tmp_path / "manifest.json"
    _ = manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path, manifest_path, manifest, run_id


def _stage(run_id: str, artifact_digest: str, status: str = "passed") -> JSONMap:
    return {
        "event_type": "stage",
        "pipeline_version": PIPELINE_VERSION,
        "telemetry_version": 2,
        "batch_id": "BATCH-fixture",
        "run_id": run_id,
        "topic_id": "TOPIC-fixture",
        "stage": "content-assembler",
        "started_at": "2026-08-27T00:00:00+00:00",
        "ended_at": "2026-08-27T00:01:00+00:00",
        "duration_ms": 60_000,
        "depends_on": ["image-maker"],
        "status": status,
        "attempt": 1,
        "quality": {"artifact_digest": artifact_digest},
    }


def _write_log(path: Path, events: list[JSONMap]) -> None:
    _ = path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )


def test_validated_stage_does_not_satisfy_q1(tmp_path: Path) -> None:
    root, manifest_path, manifest, run_id = _fixture(tmp_path)
    run_log = root / "run.jsonl"
    digest = manifest["artifact_digest"]
    assert isinstance(digest, str)
    _write_log(run_log, [_stage(run_id, digest, status="validated")])

    with pytest.raises(ContractError, match="Q1"):
        _ = verify_gate(GateRequest(
            root, manifest_path, run_log, "notion_write", run_id, "datasource-fixture"
        ))


def test_legacy_approval_event_does_not_control_notion_preflight(
    tmp_path: Path,
) -> None:
    root, manifest_path, manifest, run_id = _fixture(tmp_path)
    run_log = root / "run.jsonl"
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
    digest = manifest["artifact_digest"]
    assert isinstance(digest, str)
    _write_log(run_log, [_stage(run_id, digest), approval])

    assert len(read_events(run_log)) == 2
    result = verify_gate(GateRequest(
        root, manifest_path, run_log, "notion_write", run_id, "datasource-fixture"
    ))
    assert result["decision"] == "not_required"


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
        _ = build_manifest(ManifestBuildInput(
            root,
            "fixture-topic",
            "RUN-fixture",
            "TOPIC-fixture",
            "2026-08-27T00:00:00+00:00",
        ))

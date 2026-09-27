from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.contract_types import PIPELINE_VERSION, ContractError, JSONMap
from tools.external_adapter import (
    ExternalSystem,
    ExternalWriteRequest,
    notion_content_digest,
    plan_external_write,
    verify_notion_round_trip,
)
from tools.manifest import ManifestBuildInput, build_manifest


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, str, str]:
    keyword = "adapter-topic"
    run_id = "RUN-adapter"
    final_dir = tmp_path / "final"
    asset_dir = tmp_path / "assets" / keyword
    final_dir.mkdir(parents=True)
    asset_dir.mkdir(parents=True)
    _ = (asset_dir / "body.png").write_bytes(b"body")
    _ = (asset_dir / "thumbnail.png").write_bytes(b"thumbnail")
    _ = (asset_dir / "image-map.md").write_text("# map\n", encoding="utf-8")
    _ = (final_dir / f"{keyword}.md").write_text(
        "![body](../assets/adapter-topic/body.png)\n", encoding="utf-8"
    )
    _ = (final_dir / f"{keyword}-naver-layout.md").write_text(
        "# layout\n", encoding="utf-8"
    )
    _ = (final_dir / f"{keyword}-naver-copy.md").write_text(
        "# copy\n", encoding="utf-8"
    )
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-adapter`\n", encoding="utf-8"
    )
    manifest = build_manifest(ManifestBuildInput(
        tmp_path, keyword, run_id, "TOPIC-adapter", "2026-08-27T00:00:00+00:00"
    ))
    manifest_path = tmp_path / "manifest.json"
    _ = manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    run_log = tmp_path / "run.jsonl"
    events = [
        {
            "event_type": "stage",
            "pipeline_version": PIPELINE_VERSION,
            "telemetry_version": 2,
            "batch_id": "BATCH-adapter",
            "run_id": run_id,
            "topic_id": "TOPIC-adapter",
            "stage": "content-assembler",
            "started_at": "2026-08-27T00:00:00+00:00",
            "ended_at": "2026-08-27T00:01:00+00:00",
            "duration_ms": 60_000,
            "depends_on": ["image-maker"],
            "status": "passed",
            "attempt": 1,
            "quality": {"artifact_digest": manifest["artifact_digest"]},
        }
    ]
    _ = run_log.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    return tmp_path, manifest_path, run_log, run_id, str(manifest["artifact_digest"])


def test_notion_adapter_returns_a_non_executing_plan(tmp_path: Path) -> None:
    root, manifest_path, run_log, run_id, digest = _fixture(tmp_path)

    plan = plan_external_write(
        ExternalWriteRequest(
            root=root,
            manifest_path=manifest_path,
            run_log=run_log,
            system=ExternalSystem.NOTION,
            gate="notion_write",
            run_id=run_id,
            target_id="datasource-adapter",
            dry_run=True,
        )
    )

    assert plan.artifact_digest == digest
    assert plan.dry_run is True
    assert plan.would_execute is False


def test_adapter_rejects_non_dry_run_before_external_execution(tmp_path: Path) -> None:
    root, manifest_path, run_log, run_id, _ = _fixture(tmp_path)

    with pytest.raises(ContractError, match="dry-run"):
        _ = plan_external_write(
            ExternalWriteRequest(
                root=root,
                manifest_path=manifest_path,
                run_log=run_log,
                system=ExternalSystem.NOTION,
                gate="notion_write",
                run_id=run_id,
                target_id="datasource-adapter",
                dry_run=False,
            )
        )


def test_naver_adapter_requires_gate_b_contract(tmp_path: Path) -> None:
    root, manifest_path, run_log, run_id, _ = _fixture(tmp_path)

    with pytest.raises(ContractError, match="gate does not match"):
        _ = plan_external_write(
            ExternalWriteRequest(
                root=root,
                manifest_path=manifest_path,
                run_log=run_log,
                system=ExternalSystem.NAVER,
                gate="notion_write",
                run_id=run_id,
                target_id="blog-adapter",
                dry_run=True,
            )
        )


def test_round_trip_digest_is_the_normalized_stored_content_digest() -> None:
    # Given
    expected: JSONMap = {
        "title": "title",
        "properties": {},
        "blocks": [
            {"type": "image", "image": {"artifact_role": "thumbnail"}},
            {"type": "paragraph", "plain_text": "body"},
        ],
    }
    artifact_digest = "sha256:" + "a" * 64

    # When
    result = verify_notion_round_trip(
        expected,
        expected,
        "page",
        "2026-09-01T00:00:00+09:00",
        artifact_digest,
    )

    # Then
    content_digest = notion_content_digest(expected)
    assert result["expected_notion_content_digest"] == content_digest
    assert result["notion_content_digest"] == content_digest
    assert result["notion_roundtrip_digest"] == content_digest
    assert result["artifact_digest"] == artifact_digest


def test_round_trip_rejects_mutated_stored_content() -> None:
    # Given
    expected: JSONMap = {
        "title": "title",
        "properties": {},
        "blocks": [
            {"type": "image", "image": {"artifact_role": "thumbnail"}},
            {"type": "paragraph", "plain_text": "expected"},
        ],
    }
    actual: JSONMap = {
        "title": "title",
        "properties": {},
        "blocks": [
            {"type": "image", "image": {"artifact_role": "thumbnail"}},
            {"type": "paragraph", "plain_text": "mutated"},
        ],
    }

    # When / Then
    with pytest.raises(ContractError, match="content digest"):
        _ = verify_notion_round_trip(
            expected,
            actual,
            "page",
            "2026-09-01T00:00:00+09:00",
            "sha256:" + "a" * 64,
        )

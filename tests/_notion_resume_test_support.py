from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tests._notion_resume_transport import MemoryTransport, _page
from tools.contract_types import PIPELINE_VERSION, JSONMap
from tools.external_adapter import ExternalSystem, ExternalWriteRequest
from tools.manifest import ManifestBuildInput, build_manifest
from tools.notion_resume import ResumableNotionAdapter

__all__ = ("_adapter", "_request")


def _request(tmp_path: Path) -> tuple[ExternalWriteRequest, JSONMap]:
    keyword = "topic"
    run_id = "RUN-notion-resume"
    asset_dir = tmp_path / "assets" / keyword
    final_dir = tmp_path / "final"
    asset_dir.mkdir(parents=True)
    final_dir.mkdir()
    _ = (asset_dir / "body.png").write_bytes(b"body")
    _ = (asset_dir / "thumbnail.png").write_bytes(b"thumbnail")
    _ = (asset_dir / "image-map.md").write_text("# map\n", encoding="utf-8")
    _ = (final_dir / f"{keyword}.md").write_text(
        "![body](../assets/topic/body.png)\n", encoding="utf-8"
    )
    for suffix in ("-naver-layout.md", "-naver-copy.md", "-naver-input.md"):
        _ = (final_dir / f"{keyword}{suffix}").write_text("# file\n", encoding="utf-8")
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-resume`\n", encoding="utf-8"
    )
    manifest = build_manifest(
        ManifestBuildInput(
            tmp_path, keyword, run_id, "TOPIC-resume", "2026-08-31T09:00:00+09:00"
        )
    )
    manifest_path = tmp_path / "manifest.json"
    _ = manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    run_log = tmp_path / "run.jsonl"
    event = {
        "event_type": "stage",
        "pipeline_version": PIPELINE_VERSION,
        "batch_id": "BATCH-resume",
        "run_id": run_id,
        "topic_id": "TOPIC-resume",
        "stage": "content-assembler",
        "started_at": "2026-08-31T09:00:00+09:00",
        "ended_at": "2026-08-31T09:01:00+09:00",
        "status": "passed",
        "attempt": 1,
        "telemetry_version": 2,
        "duration_ms": 60_000,
        "depends_on": ["image-maker"],
        "quality": {"artifact_digest": manifest["artifact_digest"]},
    }
    _ = run_log.write_text(json.dumps(event) + "\n", encoding="utf-8")
    return (
        ExternalWriteRequest(
            root=tmp_path,
            manifest_path=manifest_path,
            run_log=run_log,
            system=ExternalSystem.NOTION,
            gate="notion_write",
            run_id=run_id,
            target_id="datasource-resume",
            dry_run=False,
            checkpoint_path=tmp_path / "state" / "notion.json",
        ),
        manifest,
    )


def _adapter(transport: MemoryTransport) -> ResumableNotionAdapter:
    return ResumableNotionAdapter(
        transport,
        _page("topic"),
        lambda: datetime(2026, 8, 31, 9, 5, tzinfo=UTC),
    )

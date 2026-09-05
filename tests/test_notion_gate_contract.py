from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import final, override

from tools.manifest import ManifestBuildInput
from tools.workflow_contract import (
    PIPELINE_VERSION,
    ContractError,
    GateRequest,
    JSONMap,
    build_manifest,
    verify_gate,
)


@final
class NotionGateContractTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.keyword = ""
        self.run_id = ""
        self.topic_id = ""
        self.manifest: JSONMap = {}
        self.manifest_path = self.root / "manifest.json"

    @override
    def setUp(self) -> None:
        self.temp_dir.cleanup()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.keyword = "fixture-topic"
        self.run_id = "RUN-20260826-120000"
        self.topic_id = "TOPIC-fixture"
        final_dir = self.root / "final"
        asset_dir = self.root / "assets" / self.keyword
        final_dir.mkdir(parents=True)
        asset_dir.mkdir(parents=True)
        _ = (asset_dir / "body.png").write_bytes(b"body-image")
        _ = (asset_dir / "thumbnail.png").write_bytes(b"thumbnail")
        _ = (asset_dir / "image-map.md").write_text("# image map\n", encoding="utf-8")
        _ = (self.root / "notion-config.md").write_text(
            "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
        )
        _ = (final_dir / f"{self.keyword}.md").write_text(
            "# Fixture\n\n![body](../assets/fixture-topic/body.png)\n",
            encoding="utf-8",
        )
        _ = (final_dir / f"{self.keyword}-naver-layout.md").write_text(
            "# Layout\n", encoding="utf-8"
        )
        _ = (final_dir / f"{self.keyword}-naver-copy.md").write_text(
            "# Copy\n", encoding="utf-8"
        )
        self.manifest = build_manifest(self._manifest_input())
        self.manifest_path = self.root / "manifest.json"
        _ = self.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @override
    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _manifest_input(self) -> ManifestBuildInput:
        return ManifestBuildInput(
            self.root, self.keyword, self.run_id, self.topic_id,
            "2026-08-26T12:00:00+09:00",
        )

    def test_notion_preflight_does_not_require_approval(self) -> None:
        self.manifest = build_manifest(self._manifest_input())
        _ = self.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        run_log = self.root / "run.jsonl"
        stage = {
            "event_type": "stage",
            "pipeline_version": PIPELINE_VERSION,
            "telemetry_version": 2,
            "run_id": self.run_id,
            "batch_id": "BATCH-fixture",
            "topic_id": self.topic_id,
            "stage": "content-assembler",
            "started_at": "2026-08-26T11:59:00+09:00",
            "ended_at": "2026-08-26T12:00:00+09:00",
            "duration_ms": 60_000,
            "depends_on": ["image-maker"],
            "status": "passed",
            "attempt": 1,
            "quality": {
                "artifact_digest": self.manifest["artifact_digest"],
            },
        }
        _ = run_log.write_text(json.dumps(stage) + "\n", encoding="utf-8")

        with self.assertRaises(ContractError):
            _ = verify_gate(GateRequest(
                root=self.root,
                manifest_path=self.manifest_path,
                run_log=run_log,
                gate="notion_write",
                run_id=self.run_id,
                target_id="wrong-data-source",
            ))

        result = verify_gate(GateRequest(
            root=self.root,
            manifest_path=self.manifest_path,
            run_log=run_log,
            gate="notion_write",
            run_id=self.run_id,
            target_id="datasource-fixture",
        ))
        self.assertEqual(result["decision"], "not_required")
        self.assertEqual(result["scope"], "production")

    def test_notion_preflight_requires_q1(self) -> None:
        run_log = self.root / "notion-run.jsonl"
        _ = run_log.write_text("", encoding="utf-8")

        with self.assertRaisesRegex(ContractError, "Q1"):
            _ = verify_gate(GateRequest(
                root=self.root,
                manifest_path=self.manifest_path,
                run_log=run_log,
                gate="notion_write",
                run_id=self.run_id,
                target_id="datasource-fixture",
            ))

    def test_naver_preflight_requires_notion_round_trip_fields(self) -> None:
        self.manifest = build_manifest(self._manifest_input())
        _ = self.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        run_log = self.root / "run.jsonl"
        notion_page_id = "page-fixture"
        verified_at = "2026-08-26T12:03:00+09:00"
        content_digest = "sha256:" + "1" * 64
        artifact_digest = self.manifest["artifact_digest"]
        assert isinstance(artifact_digest, str)
        events = [
            {
                "event_type": "stage",
                "pipeline_version": PIPELINE_VERSION,
                "telemetry_version": 2,
                "run_id": self.run_id,
                "batch_id": "BATCH-fixture",
                "topic_id": self.topic_id,
                "stage": "content-assembler",
                "started_at": "2026-08-26T11:59:00+09:00",
                "ended_at": "2026-08-26T12:00:00+09:00",
                "duration_ms": 60_000,
                "depends_on": ["image-maker"],
                "status": "passed",
                "attempt": 1,
                "quality": {
                    "artifact_digest": artifact_digest,
                },
            },
            {
                "event_type": "stage",
                "pipeline_version": PIPELINE_VERSION,
                "telemetry_version": 2,
                "run_id": self.run_id,
                "batch_id": "BATCH-fixture",
                "topic_id": self.topic_id,
                "stage": "notion-rider",
                "started_at": "2026-08-26T12:02:00+09:00",
                "ended_at": verified_at,
                "duration_ms": 60_000,
                "depends_on": ["content-assembler"],
                "status": "passed",
                "attempt": 1,
                "page_id": notion_page_id,
                "verified_at": verified_at,
                "quality": {
                    "storage_integrity": "passed",
                    "notion_page_id": notion_page_id,
                    "notion_last_verified_at": verified_at,
                    "expected_notion_content_digest": content_digest,
                    "notion_content_digest": content_digest,
                    "notion_roundtrip_digest": content_digest,
                    "artifact_digest": self.manifest["artifact_digest"],
                    "notion_target_id": "datasource-fixture",
                },
            },
        ]
        _ = run_log.write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )

        result = verify_gate(GateRequest(
            root=self.root,
            manifest_path=self.manifest_path,
            run_log=run_log,
            gate="naver_draft_save",
            run_id=self.run_id,
            target_id="blog-fixture",
            notion_page_id=notion_page_id,
            notion_verified_at=verified_at,
            expected_notion_content_digest=content_digest,
            notion_content_digest=content_digest,
            notion_roundtrip_digest=content_digest,
            q2_artifact_digest=artifact_digest,
            blog_id="blog-fixture",
        ))
        self.assertEqual(result["notion_page_id"], notion_page_id)

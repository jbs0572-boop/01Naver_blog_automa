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
    validate_log,
    verify_gate,
)


@final
class NaverGateContractTests(unittest.TestCase):
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

    def test_naver_preflight_does_not_require_approval(self) -> None:
        self.manifest = build_manifest(self._manifest_input())
        _ = self.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        notion_page_id = "page-fixture"
        verified_at = "2026-08-26T12:03:00+09:00"
        run_log = self.root / "run.jsonl"
        events = [
            {
                "event_type": "stage",
                "pipeline_version": PIPELINE_VERSION,
                "run_id": self.run_id,
                "batch_id": "BATCH-fixture",
                "topic_id": self.topic_id,
                "stage": "content-assembler",
                "started_at": "2026-08-26T11:59:00+09:00",
                "ended_at": "2026-08-26T12:00:00+09:00",
                "status": "passed",
                "attempt": 1,
                "quality": {
                    "notion_page_id": "page-fixture",
                    "notion_last_verified_at": verified_at,
                    "notion_roundtrip_digest": self.manifest["artifact_digest"],
                },
            },
            {
                "event_type": "stage",
                "pipeline_version": PIPELINE_VERSION,
                "run_id": self.run_id,
                "batch_id": "BATCH-fixture",
                "topic_id": self.topic_id,
                "stage": "notion-rider",
                "started_at": "2026-08-26T12:02:00+09:00",
                "ended_at": verified_at,
                "status": "passed",
                "attempt": 1,
                "quality": {
                    "notion_page_id": notion_page_id,
                    "notion_last_verified_at": verified_at,
                    "notion_roundtrip_digest": self.manifest["artifact_digest"],
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
            blog_id="blog-fixture",
        ))

        self.assertEqual(result["decision"], "not_required")
        self.assertEqual(
            result["verified_artifact_digest"], self.manifest["artifact_digest"]
        )

    def test_naver_preflight_rejects_round_trip_identity_mismatch(self) -> None:
        self.manifest = build_manifest(self._manifest_input())
        _ = self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        run_log = self.root / "run.jsonl"
        verified_at = "2026-08-26T12:03:00+09:00"
        events = [
            {
                "event_type": "stage",
                "pipeline_version": PIPELINE_VERSION,
                "run_id": self.run_id,
                "batch_id": "BATCH-fixture",
                "topic_id": self.topic_id,
                "stage": "content-assembler",
                "started_at": "2026-08-26T11:59:00+09:00",
                "ended_at": "2026-08-26T12:00:00+09:00",
                "status": "passed",
                "attempt": 1,
            },
            {
                "event_type": "stage",
                "pipeline_version": PIPELINE_VERSION,
                "run_id": self.run_id,
                "batch_id": "BATCH-fixture",
                "topic_id": self.topic_id,
                "stage": "notion-rider",
                "started_at": "2026-08-26T12:02:00+09:00",
                "ended_at": verified_at,
                "status": "passed",
                "attempt": 1,
            },
        ]
        _ = run_log.write_text(
            "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
        )
        with self.assertRaises(ContractError):
            _ = verify_gate(GateRequest(
                root=self.root,
                manifest_path=self.manifest_path,
                run_log=run_log,
                gate="naver_draft_save",
                run_id=self.run_id,
                target_id="blog-fixture",
                notion_page_id="wrong-page",
                notion_verified_at=verified_at,
                blog_id="blog-fixture",
            ))

    def test_naver_write_is_blocked_without_q2(self) -> None:
        run_log = self.root / "empty.jsonl"
        _ = run_log.write_text("", encoding="utf-8")
        with self.assertRaises(ContractError):
            _ = verify_gate(GateRequest(
                root=self.root,
                manifest_path=self.manifest_path,
                run_log=run_log,
                gate="naver_draft_save",
                run_id=self.run_id,
                target_id="blog-fixture",
            ))

    def test_optimized_event_schema_rejects_invalid_stage(self) -> None:
        run_log = self.root / "invalid-stage.jsonl"
        event = {
            "event_type": "stage",
            "pipeline_version": PIPELINE_VERSION,
            "batch_id": "BATCH-fixture",
            "run_id": self.run_id,
            "topic_id": self.topic_id,
            "stage": "not-a-stage",
            "started_at": "2026-08-26T11:59:00+09:00",
            "ended_at": "2026-08-26T12:00:00+09:00",
            "status": "passed",
            "attempt": 1,
        }
        _ = run_log.write_text(json.dumps(event) + "\n", encoding="utf-8")
        with self.assertRaises(ContractError):
            _ = validate_log(run_log)


if __name__ == "__main__":
    _ = unittest.main()

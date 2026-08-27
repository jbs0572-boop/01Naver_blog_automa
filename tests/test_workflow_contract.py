from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import final, override

from tools.workflow_contract import (
    PIPELINE_VERSION,
    ContractError,
    JSONMap,
    build_manifest,
    validate_log,
    verify_gate,
    verify_manifest,
)


@final
class WorkflowContractTests(unittest.TestCase):
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
        self.manifest = build_manifest(
            root=self.root,
            keyword=self.keyword,
            run_id=self.run_id,
            topic_id=self.topic_id,
            mode="beta",
            created_at="2026-08-26T12:00:00+09:00",
        )
        self.manifest_path = self.root / "manifest.json"
        _ = self.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @override
    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_manifest_rejects_naver_layout_byte_change(self) -> None:
        _ = verify_manifest(self.root, self.manifest_path)

        layout_path = self.root / "final" / f"{self.keyword}-naver-layout.md"
        _ = layout_path.write_text("# Mutated layout\n", encoding="utf-8")

        with self.assertRaises(ContractError):
            _ = verify_manifest(self.root, self.manifest_path)

    def test_manifest_requires_all_naver_outputs(self) -> None:
        copy_path = self.root / "final" / f"{self.keyword}-naver-copy.md"
        copy_path.unlink()
        with self.assertRaises(ContractError):
            _ = build_manifest(
                root=self.root,
                keyword=self.keyword,
                run_id=self.run_id,
                topic_id=self.topic_id,
                mode="beta",
                created_at="2026-08-26T12:00:00+09:00",
            )

    def test_manifest_requires_final_markdown(self) -> None:
        final_path = self.root / "final" / f"{self.keyword}.md"
        final_path.unlink()
        with self.assertRaises(ContractError):
            _ = build_manifest(
                root=self.root,
                keyword=self.keyword,
                run_id=self.run_id,
                topic_id=self.topic_id,
                mode="beta",
                created_at="2026-08-26T12:00:00+09:00",
            )

    def test_manifest_rejects_body_image_byte_change(self) -> None:
        _ = verify_manifest(self.root, self.manifest_path)
        _ = (self.root / "assets" / self.keyword / "body.png").write_bytes(
            b"changed-body-image"
        )
        with self.assertRaises(ContractError):
            _ = verify_manifest(self.root, self.manifest_path)

    def test_gate_a_requires_matching_approval(self) -> None:
        run_log = self.root / "run.jsonl"
        stage = {
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
        }
        _ = run_log.write_text(json.dumps(stage) + "\n", encoding="utf-8")

        with self.assertRaises(ContractError):
            _ = verify_gate(
                root=self.root,
                manifest_path=self.manifest_path,
                run_log=run_log,
                gate="notion_write",
                run_id=self.run_id,
                target_id="datasource-fixture",
            )

        approval = {
            "event_type": "approval",
            "pipeline_version": PIPELINE_VERSION,
            "run_id": self.run_id,
            "gate": "notion_write",
            "decision": "approved",
            "scope": "per-run",
            "target_id": "datasource-fixture",
            "artifact_digest": self.manifest["artifact_digest"],
            "requested_at": "2026-08-26T12:00:01+09:00",
            "decided_at": "2026-08-26T12:00:02+09:00",
        }
        bad_approval = dict(approval)
        bad_approval["artifact_digest"] = "sha256:" + "0" * 64
        with run_log.open("a", encoding="utf-8") as handle:
            _ = handle.write(json.dumps(bad_approval) + "\n")
        with self.assertRaises(ContractError):
            _ = verify_gate(
                root=self.root,
                manifest_path=self.manifest_path,
                run_log=run_log,
                gate="notion_write",
                run_id=self.run_id,
                target_id="datasource-fixture",
            )

        with run_log.open("a", encoding="utf-8") as handle:
            _ = handle.write(json.dumps(approval) + "\n")

        with self.assertRaises(ContractError):
            _ = verify_gate(
                root=self.root,
                manifest_path=self.manifest_path,
                run_log=run_log,
                gate="notion_write",
                run_id=self.run_id,
                target_id="wrong-data-source",
            )

        result = verify_gate(
            root=self.root,
            manifest_path=self.manifest_path,
            run_log=run_log,
            gate="notion_write",
            run_id=self.run_id,
            target_id="datasource-fixture",
        )
        self.assertEqual(result["decision"], "approved")

    def test_gate_b_requires_notion_round_trip_fields(self) -> None:
        self.manifest = build_manifest(
            root=self.root,
            keyword=self.keyword,
            run_id=self.run_id,
            topic_id=self.topic_id,
            mode="formal",
            created_at="2026-08-26T12:00:00+09:00",
        )
        _ = self.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        run_log = self.root / "run.jsonl"
        notion_page_id = "page-fixture"
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
                "page_id": notion_page_id,
                "verified_at": verified_at,
            },
            {
                "event_type": "approval",
                "pipeline_version": PIPELINE_VERSION,
                "run_id": self.run_id,
                "gate": "naver_draft_save",
                "decision": "approved",
                "scope": "per-run",
                "target_id": "blog-fixture",
                "artifact_digest": self.manifest["artifact_digest"],
                "requested_at": "2026-08-26T12:03:01+09:00",
                "decided_at": "2026-08-26T12:03:02+09:00",
                "notion_page_id": notion_page_id,
                "notion_last_verified_at": verified_at,
                "blog_id": "blog-fixture",
                "notion_roundtrip_digest": self.manifest["artifact_digest"],
            },
        ]
        _ = run_log.write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )

        result = verify_gate(
            root=self.root,
            manifest_path=self.manifest_path,
            run_log=run_log,
            gate="naver_draft_save",
            run_id=self.run_id,
            target_id="blog-fixture",
            notion_page_id=notion_page_id,
            notion_verified_at=verified_at,
            blog_id="blog-fixture",
        )
        self.assertEqual(result["notion_page_id"], notion_page_id)

    def test_gate_b_rejects_round_trip_identity_mismatch(self) -> None:
        self.manifest = build_manifest(
            root=self.root,
            keyword=self.keyword,
            run_id=self.run_id,
            topic_id=self.topic_id,
            mode="formal",
            created_at="2026-08-26T12:00:00+09:00",
        )
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
            {
                "event_type": "approval",
                "pipeline_version": PIPELINE_VERSION,
                "run_id": self.run_id,
                "gate": "naver_draft_save",
                "decision": "approved",
                "scope": "per-run",
                "target_id": "blog-fixture",
                "artifact_digest": self.manifest["artifact_digest"],
                "requested_at": "2026-08-26T12:03:01+09:00",
                "decided_at": "2026-08-26T12:03:02+09:00",
                "notion_page_id": "page-fixture",
                "notion_last_verified_at": verified_at,
                "blog_id": "blog-fixture",
                "notion_roundtrip_digest": self.manifest["artifact_digest"],
            },
        ]
        _ = run_log.write_text(
            "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
        )
        with self.assertRaises(ContractError):
            _ = verify_gate(
                root=self.root,
                manifest_path=self.manifest_path,
                run_log=run_log,
                gate="naver_draft_save",
                run_id=self.run_id,
                target_id="blog-fixture",
                notion_page_id="wrong-page",
                notion_verified_at=verified_at,
                blog_id="blog-fixture",
            )

    def test_beta_gate_b_is_blocked_before_external_write(self) -> None:
        run_log = self.root / "empty.jsonl"
        _ = run_log.write_text("", encoding="utf-8")
        with self.assertRaises(ContractError):
            _ = verify_gate(
                root=self.root,
                manifest_path=self.manifest_path,
                run_log=run_log,
                gate="naver_draft_save",
                run_id=self.run_id,
                target_id="blog-fixture",
            )

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

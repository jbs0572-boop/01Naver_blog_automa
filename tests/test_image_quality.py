from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import final, override

from tools.image_quality import (
    validate_image_metadata,
    validate_image_quality,
    validate_image_stage_assets,
)
from tools.workflow_contract import ContractError, JSONMap


@final
class ImageQualityTests(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str]
    root: Path

    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    @override
    def setUp(self) -> None:
        self.temp_dir.cleanup()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    @override
    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_jsonl(self, name: str, value: JSONMap) -> Path:
        path = self.root / name
        _ = path.write_text(json.dumps(value) + "\n", encoding="utf-8")
        return path

    def _image_bytes(self) -> bytes:
        return base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )

    def test_locked_generation_metadata_matches_output_hash(self) -> None:
        output = self.root / "image.png"
        _ = output.write_bytes(self._image_bytes())
        output_digest = f"sha256:{hashlib.sha256(output.read_bytes()).hexdigest()}"
        metadata = self._write_jsonl(
            "image-generation.jsonl",
            {
                "generation_provider": "openai",
                "generation_model": "gpt-image-2.5-flare",
                "generation_snapshot": "gpt-image-2.5-flare-2026-09-08",
                "generation_control": "locked",
                "quality": "high",
                "size": "1600x900",
                "prompt_template_version": "image-prompt-v1",
                "prompt_sha256": "sha256:" + "1" * 64,
                "reference_sha256": [],
                "output_sha256": output_digest,
                "output_path": "image.png",
                "generated_at": "2026-08-27T10:00:00+09:00",
                "provenance_status": "generated",
            },
        )
        result = validate_image_metadata(metadata)
        self.assertEqual(result["production_ready"], True)

    def test_unlocked_metadata_is_blocked_in_production_workflow(self) -> None:
        metadata = self._write_jsonl(
            "unlocked.jsonl",
            {
                "generation_provider": "openai",
                "generation_model": "gpt-image-2.5-flare",
                "generation_snapshot": "gpt-image-2.5-flare-2026-09-08",
                "generation_control": "unlocked",
                "quality": "high",
                "size": "1600x900",
                "prompt_template_version": "image-prompt-v1",
                "prompt_sha256": "sha256:" + "1" * 64,
                "reference_sha256": [],
                "output_sha256": "sha256:" + "2" * 64,
                "generated_at": "2026-08-27T10:00:00+09:00",
                "provenance_status": "generated",
            },
        )
        with self.assertRaises(ContractError):
            _ = validate_image_metadata(metadata)

    def test_metadata_missing_required_field_is_blocked(self) -> None:
        metadata = self._write_jsonl(
            "missing-field.jsonl",
            {
                "generation_provider": "openai",
                "generation_model": "gpt-image-2.5-flare",
                "generation_control": "locked",
                "quality": "high",
                "size": "1600x900",
                "prompt_template_version": "image-prompt-v1",
                "prompt_sha256": "sha256:" + "1" * 64,
                "reference_sha256": [],
                "output_sha256": "sha256:" + "2" * 64,
                "generated_at": "2026-08-27T10:00:00+09:00",
                "provenance_status": "generated",
            },
        )
        with self.assertRaises(ContractError):
            _ = validate_image_metadata(metadata)

    def test_quality_requires_mobile_human_pass(self) -> None:
        _ = (self.root / "mobile.png").write_bytes(self._image_bytes())
        mobile_digest = f"sha256:{hashlib.sha256((self.root / 'mobile.png').read_bytes()).hexdigest()}"
        quality = self._write_jsonl(
            "image-quality.jsonl",
            {
                "image_sha256": "sha256:" + "f" * 64,
                "run_id": "RUN-image-quality",
                "article_quality_report_digest": "sha256:" + "a" * 64,
                "reviewed_at": "2026-08-27T10:01:00+00:00",
                "automated_checks": {
                    "decode_check": "passed",
                    "duplicate_check": "passed",
                    "ocr_check": "passed",
                    "visual_contract_check": "passed",
                    "mobile_render_check": "passed",
                },
                "scores": {
                    "subject_relevance": 4,
                    "composition_legibility": 4,
                    "rendering_completion": 3,
                    "information_contribution": 4,
                    "style_consistency": 3,
                },
                "immediate_failure": False,
                "mobile_rendered": True,
                "mobile_viewport": "390x844",
                "mobile_render_path": "mobile.png",
                "mobile_render_sha256": mobile_digest,
                "human_verdict": "passed",
            },
        )
        result = validate_image_quality(quality)
        self.assertEqual(result["passed"], True)
        record = json.loads(quality.read_text(encoding="utf-8"))
        assert isinstance(record, dict)
        del record["run_id"]
        del record["article_quality_report_digest"]
        del record["reviewed_at"]
        _ = quality.write_text(json.dumps(record) + "\n", encoding="utf-8")
        self.assertEqual(validate_image_quality(quality)["passed"], True)

    def test_quality_score_below_threshold_is_blocked(self) -> None:
        _ = (self.root / "mobile.png").write_bytes(self._image_bytes())
        mobile_digest = f"sha256:{hashlib.sha256((self.root / 'mobile.png').read_bytes()).hexdigest()}"
        quality = self._write_jsonl(
            "low-quality.jsonl",
            {
                "image_sha256": "sha256:" + "f" * 64,
                "run_id": "RUN-image-quality",
                "article_quality_report_digest": "sha256:" + "a" * 64,
                "reviewed_at": "2026-08-27T10:01:00+00:00",
                "automated_checks": {
                    key: "passed"
                    for key in (
                        "decode_check",
                        "duplicate_check",
                        "ocr_check",
                        "visual_contract_check",
                        "mobile_render_check",
                    )
                },
                "scores": {
                    "subject_relevance": 4,
                    "composition_legibility": 2,
                    "rendering_completion": 4,
                    "information_contribution": 4,
                    "style_consistency": 4,
                },
                "immediate_failure": False,
                "mobile_rendered": True,
                "mobile_viewport": "390x844",
                "mobile_render_path": "mobile.png",
                "mobile_render_sha256": mobile_digest,
                "human_verdict": "passed",
            },
        )
        with self.assertRaises(ContractError):
            _ = validate_image_quality(quality)

    def test_image_stage_bundle_covers_markers_thumbnail_and_quality_records(self) -> None:
        asset_dir = self.root / "assets" / "topic"
        asset_dir.mkdir(parents=True)
        draft = self.root / "drafts" / "topic.md"
        draft.parent.mkdir(parents=True)
        _ = draft.write_text("[IMAGE: body image]", encoding="utf-8")
        outputs = {
            "body.png": self._image_bytes() + b"body",
            "thumbnail.png": self._image_bytes() + b"thumbnail",
        }
        metadata_records: list[JSONMap] = []
        quality_records: list[JSONMap] = []
        for name, image in outputs.items():
            output = asset_dir / name
            _ = output.write_bytes(image)
            digest = f"sha256:{hashlib.sha256(image).hexdigest()}"
            metadata_records.append(
                {
                    "production_method": "ai_generation",
                    "generation_provider": "openai",
                    "generation_model": "gpt-image-2.5-flare",
                    "generation_snapshot": "gpt-image-2.5-flare-2026-09-08",
                    "generation_control": "locked",
                    "quality": "high",
                    "size": "1600x900",
                    "prompt_template_version": "image-prompt-v1",
                    "prompt_sha256": "sha256:" + "1" * 64,
                    "reference_sha256": [],
                    "output_sha256": digest,
                    "output_path": name,
                    "generated_at": "2026-09-20T10:00:00+09:00",
                    "provenance_status": "generated",
                }
            )
            mobile_name = f"mobile-{name}"
            mobile_image = self._image_bytes() + b"mobile-" + name.encode()
            mobile_path = asset_dir / mobile_name
            _ = mobile_path.write_bytes(mobile_image)
            mobile_digest = f"sha256:{hashlib.sha256(mobile_image).hexdigest()}"
            quality_records.append(
                {
                    "image_sha256": digest,
                    "automated_checks": {
                        key: "passed"
                        for key in (
                            "decode_check",
                            "duplicate_check",
                            "ocr_check",
                            "visual_contract_check",
                            "mobile_render_check",
                        )
                    },
                    "scores": {
                        "subject_relevance": 4,
                        "composition_legibility": 4,
                        "rendering_completion": 4,
                        "information_contribution": 4,
                        "style_consistency": 4,
                    },
                    "immediate_failure": False,
                    "mobile_rendered": True,
                    "mobile_viewport": "390x844",
                    "mobile_render_path": mobile_name,
                    "mobile_render_sha256": mobile_digest,
                    "human_verdict": "passed",
                }
            )
        _ = (asset_dir / "image-generation.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in metadata_records),
            encoding="utf-8",
        )
        _ = (asset_dir / "image-quality.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in quality_records),
            encoding="utf-8",
        )
        _ = (asset_dir / "image-map.md").write_text(
            "| [IMAGE] | `body.png` |\n| [THUMBNAIL] | `thumbnail.png` |\n",
            encoding="utf-8",
        )

        result = validate_image_stage_assets(asset_dir, draft)
        self.assertEqual(result["body_markers"], 1)
        self.assertEqual(result["outputs"], 2)

        (asset_dir / "body.png").unlink()
        with self.assertRaises(ContractError):
            _ = validate_image_stage_assets(asset_dir, draft)


if __name__ == "__main__":
    _ = unittest.main()

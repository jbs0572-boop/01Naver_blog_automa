from __future__ import annotations

import base64
import hashlib
import json
import struct
import tempfile
import zlib
import unittest
from pathlib import Path
from typing import final, override

from tools.image_quality import validate_image_metadata, validate_image_quality
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

    def _mobile_image_bytes(self) -> bytes:
        def chunk(kind: bytes, payload: bytes) -> bytes:
            return (
                len(payload).to_bytes(4, "big") + kind + payload
                + zlib.crc32(kind + payload).to_bytes(4, "big")
            )

        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 390, 844, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x00" * 390) * 844))
            + chunk(b"IEND", b"")
        )

    def test_locked_generation_metadata_matches_output_hash(self) -> None:
        output = self.root / "image.png"
        _ = output.write_bytes(self._image_bytes())
        output_digest = f"sha256:{hashlib.sha256(output.read_bytes()).hexdigest()}"
        metadata = self._write_jsonl(
            "image-generation.jsonl",
            {
                "generation_provider": "openai",
                "generation_model": "gpt-image-2",
                "generation_snapshot": "gpt-image-2-2026-04-21",
                "generation_control": "locked",
                "quality": "high",
                "size": "1x1",
                "prompt_template_version": "image-prompt-v1",
                "prompt_sha256": "sha256:" + "1" * 64,
                "reference_sha256": [],
                "output_sha256": output_digest,
                "output_path": "image.png",
                "generated_at": "2026-08-27T10:00:00+09:00",
                "provenance_status": "generated",
            },
        )
        result = validate_image_metadata(metadata, "formal")
        self.assertEqual(result["formal_ready"], True)

    def test_unlocked_metadata_is_blocked_in_formal_mode(self) -> None:
        metadata = self._write_jsonl(
            "unlocked.jsonl",
            {
                "generation_provider": "openai",
                "generation_model": "gpt-image-2",
                "generation_snapshot": "gpt-image-2-2026-04-21",
                "generation_control": "unlocked",
                "quality": "high",
                "size": "1x1",
                "prompt_template_version": "image-prompt-v1",
                "prompt_sha256": "sha256:" + "1" * 64,
                "reference_sha256": [],
                "output_sha256": "sha256:" + "2" * 64,
                "generated_at": "2026-08-27T10:00:00+09:00",
                "provenance_status": "generated",
            },
        )
        with self.assertRaises(ContractError):
            _ = validate_image_metadata(metadata, "formal")

    def test_metadata_missing_required_field_is_blocked(self) -> None:
        metadata = self._write_jsonl(
            "missing-field.jsonl",
            {
                "generation_provider": "openai",
                "generation_model": "gpt-image-2",
                "generation_control": "locked",
                "quality": "high",
                "size": "1x1",
                "prompt_template_version": "image-prompt-v1",
                "prompt_sha256": "sha256:" + "1" * 64,
                "reference_sha256": [],
                "output_sha256": "sha256:" + "2" * 64,
                "generated_at": "2026-08-27T10:00:00+09:00",
                "provenance_status": "generated",
            },
        )
        with self.assertRaises(ContractError):
            _ = validate_image_metadata(metadata, "beta")

    def test_quality_requires_mobile_human_pass(self) -> None:
        _ = (self.root / "mobile.png").write_bytes(self._mobile_image_bytes())
        mobile_digest = f"sha256:{hashlib.sha256((self.root / 'mobile.png').read_bytes()).hexdigest()}"
        quality = self._write_jsonl(
            "image-quality.jsonl",
            {
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

    def test_quality_score_below_threshold_is_blocked(self) -> None:
        _ = (self.root / "mobile.png").write_bytes(self._mobile_image_bytes())
        mobile_digest = f"sha256:{hashlib.sha256((self.root / 'mobile.png').read_bytes()).hexdigest()}"
        quality = self._write_jsonl(
            "low-quality.jsonl",
            {
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


    def test_image_metadata_rejects_symlink_outside_evidence_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="outside-image-proof-") as external:
            external_image = Path(external) / "original.png"
            _ = external_image.write_bytes(self._image_bytes())
            (self.root / "image.png").symlink_to(external_image)
            digest = f"sha256:{hashlib.sha256(external_image.read_bytes()).hexdigest()}"
            metadata = self._write_jsonl(
                "symlink-image-metadata.jsonl",
                {
                    "generation_provider": "openai",
                    "generation_model": "gpt-image-2",
                    "generation_snapshot": "gpt-image-2-2026-04-21",
                    "generation_control": "locked",
                    "quality": "high",
                    "size": "1x1",
                    "prompt_template_version": "image-prompt-v1",
                    "prompt_sha256": "sha256:" + "1" * 64,
                    "reference_sha256": [],
                    "output_sha256": digest,
                    "output_path": "image.png",
                    "generated_at": "2026-08-27T10:00:00+09:00",
                    "provenance_status": "generated",
                },
            )
            with self.assertRaisesRegex(ContractError, "unsafe output_path"):
                _ = validate_image_metadata(metadata, "formal")

    def test_mobile_quality_rejects_symlinked_directory_outside_proof_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="outside-mobile-proof-") as external:
            outside = Path(external)
            mobile = outside / "mobile.png"
            _ = mobile.write_bytes(self._image_bytes())
            (self.root / "offsite").symlink_to(outside, target_is_directory=True)
            quality = self._write_jsonl(
                "symlink-mobile-quality.jsonl",
                {
                    "automated_checks": {
                        key: "passed"
                        for key in (
                            "decode_check", "duplicate_check", "ocr_check",
                            "visual_contract_check", "mobile_render_check",
                        )
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
                    "mobile_render_path": "offsite/mobile.png",
                    "mobile_render_sha256": "sha256:" + hashlib.sha256(
                        mobile.read_bytes()
                    ).hexdigest(),
                    "human_verdict": "passed",
                },
            )
            with self.assertRaisesRegex(ContractError, "unsafe mobile_render_path"):
                _ = validate_image_quality(quality)


if __name__ == "__main__":
    _ = unittest.main()

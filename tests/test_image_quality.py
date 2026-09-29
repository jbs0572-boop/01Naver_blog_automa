from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import final, override

from tools.image_contract import AUTOMATED_CHECKS, has_image_signature
from tools.image_quality import (
    validate_image_map,
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

    def test_image_map_requires_exact_ordered_asset_references(self) -> None:
        image_map = self.root / "image-map.md"
        _ = image_map.write_text(
            "| 1 | VIS-01 | [IMAGE: slot 1] | `copy-image-01.png` | identify | original_photo | title_promise | generated_allowed | scope | section | fallback | info | `origin=generated; method=local_render` | 통과 |\n"
            + "| [THUMBNAIL] | `[THUMBNAIL]` | `thumbnail.png` |\n",
            encoding="utf-8",
        )

        with self.assertRaises(ContractError):
            _ = validate_image_map(
                image_map,
                ["image-01.png", "copy-image-01.png"],
                "thumbnail.png",
            )

        _ = image_map.write_text(
            "| 1 | VIS-01 | [IMAGE: slot 1] | `image-01.png` | identify | original_photo | title_promise | generated_allowed | scope | section | fallback | info | `origin=generated; method=local_render` | 통과 |\n"
            + "| 2 | VIS-02 | [IMAGE: slot 2] | `copy-image-01.png` | identify | original_photo | title_promise | generated_allowed | scope | section | fallback | info | `origin=generated; method=local_render` | 통과 |\n"
            + "| [THUMBNAIL] | `[THUMBNAIL]` | `thumbnail.png` |\n",
            encoding="utf-8",
        )
        result = validate_image_map(
            image_map,
            ["image-01.png", "copy-image-01.png"],
            "thumbnail.png",
        )
        self.assertEqual(result["body_images"], 2)

        invalid_order_rows = (
            (
                "| 2 | VIS-01 | [IMAGE: slot 1] | `image-01.png` | identify | original_photo | title_promise | generated_allowed | scope | section | fallback | info | `origin=generated; method=local_render` | 통과 |\n"
                + "| 2 | VIS-02 | [IMAGE: slot 2] | `copy-image-01.png` | identify | original_photo | title_promise | generated_allowed | scope | section | fallback | info | `origin=generated; method=local_render` | 통과 |\n"
            ),
            (
                "| second | VIS-01 | [IMAGE: slot 1] | `image-01.png` | identify | original_photo | title_promise | generated_allowed | scope | section | fallback | info | `origin=generated; method=local_render` | 통과 |\n"
                + "| 2 | VIS-02 | [IMAGE: slot 2] | `copy-image-01.png` | identify | original_photo | title_promise | generated_allowed | scope | section | fallback | info | `origin=generated; method=local_render` | 통과 |\n"
            ),
            (
                "| 2 | VIS-01 | [IMAGE: slot 1] | `image-01.png` | identify | original_photo | title_promise | generated_allowed | scope | section | fallback | info | `origin=generated; method=local_render` | 통과 |\n"
                + "| 1 | VIS-02 | [IMAGE: slot 2] | `copy-image-01.png` | identify | original_photo | title_promise | generated_allowed | scope | section | fallback | info | `origin=generated; method=local_render` | 통과 |\n"
            ),
        )
        for rows in invalid_order_rows:
            _ = image_map.write_text(
                rows + "| [THUMBNAIL] | `[THUMBNAIL]` | `thumbnail.png` |\n",
                encoding="utf-8",
            )
            with self.subTest(rows=rows), self.assertRaises(ContractError):
                _ = validate_image_map(
                    image_map,
                    ["image-01.png", "copy-image-01.png"],
                    "thumbnail.png",
                )

        _ = image_map.write_text(
            "| 1 | VIS-01 | [IMAGE: slot 1] | `missing.png` | identify | original_photo | title_promise | generated_allowed | scope | section | fallback | info | `origin=generated; method=local_render` | 통과 |\n"
            + "| 2 | VIS-02 | [IMAGE: slot 2] | `copy-image-01.png` | identify | original_photo | title_promise | generated_allowed | scope | section | fallback | info | `origin=generated; method=local_render` | 통과 | `image-01.png` |\n"
            + "| [THUMBNAIL] | `[THUMBNAIL]` | `thumbnail.png` |\n",
            encoding="utf-8",
        )
        with self.assertRaises(ContractError):
            _ = validate_image_map(
                image_map,
                ["image-01.png", "copy-image-01.png"],
                "thumbnail.png",
            )

    def test_generated_route_diagram_cannot_claim_official_map_slot(self) -> None:
        image_map = self.root / "image-map.md"
        _ = image_map.write_text(
            "| 1 | VIS-01 | `[IMAGE: asset_type=map; source_policy=official_or_licensed]` | `image-01.png` | identify | map | title_promise | official_or_licensed | scope | section | fallback | info | `origin=generated; Pillow local_render` | 통과 |\n"
            + "| [THUMBNAIL] | `[THUMBNAIL]` | `thumbnail.png` |\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ContractError, "official or licensed source provenance"):
            _ = validate_image_map(image_map, ["image-01.png"], "thumbnail.png")

    def test_official_map_requires_source_url_and_original_origin(self) -> None:
        image_map = self.root / "image-map.md"
        marker = "`[IMAGE: asset_type=map; source_policy=official_or_licensed]`"
        tail = "| [THUMBNAIL] | `[THUMBNAIL]` | `thumbnail.png` |\n"
        for provenance in (
            "`origin=official`",
            "`origin=licensed; source_url=https://example.com/license`",
            "`origin=official; source_url=http://example.com/map.png`",
            "`origin=generated; source_url=https://example.com/source`",
        ):
            _ = image_map.write_text(
                f"| 1 | VIS-01 | {marker} | `image-01.png` | identify | map | title_promise | official_or_licensed | scope | section | fallback | info | {provenance} | 통과 |\n"
                + tail,
                encoding="utf-8",
            )
            with self.subTest(provenance=provenance), self.assertRaises(ContractError):
                _ = validate_image_map(image_map, ["image-01.png"], "thumbnail.png")

        _ = image_map.write_text(
            f"| 1 | VIS-01 | {marker} | `image-01.png` | identify | map | title_promise | official_or_licensed | scope | section | fallback | info | `origin=official; source_url=https://example.com/official-map.png` | 통과 |\n"
            + tail,
            encoding="utf-8",
        )
        result = validate_image_map(image_map, ["image-01.png"], "thumbnail.png")
        self.assertEqual(result["body_images"], 1)

    def test_official_or_licensed_asset_requires_source_metadata(self) -> None:
        image_map = self.root / "image-map.md"
        marker = "`[IMAGE: asset_type=original_photo; source_policy=official_or_licensed]`"
        _ = image_map.write_text(
            f"| 1 | VIS-01 | {marker} | `image-01.png` | identify | original_photo | title_promise | official_or_licensed | scope | section | fallback | info | `origin=generated; Pillow local_render` | 통과 |\n"
            + "| [THUMBNAIL] | `[THUMBNAIL]` | `thumbnail.png` |\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ContractError, "official or licensed source provenance"):
            _ = validate_image_map(image_map, ["image-01.png"], "thumbnail.png")

    def test_declared_columns_enforce_official_map_provenance(self) -> None:
        image_map = self.root / "image-map.md"
        row = (
            "| 1 | VIS-01 | `[IMAGE: slot 1]` | `image-01.png` | identify | map | title_promise | official_or_licensed | scope | section | fallback | info | `origin=generated; Pillow local_render` | 통과 |\n"
            + "| [THUMBNAIL] | `[THUMBNAIL]` | `thumbnail.png` |\n"
        )
        _ = image_map.write_text(row, encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "official or licensed source provenance"):
            _ = validate_image_map(image_map, ["image-01.png"], "thumbnail.png")

    def test_image_map_rejects_conflicting_marker_and_columns(self) -> None:
        image_map = self.root / "image-map.md"
        _ = image_map.write_text(
            "| 1 | VIS-01 | `[IMAGE: asset_type=map]` | `image-01.png` | identify | photograph | title_promise | generated_allowed | scope | section | fallback | info | `origin=official; source_url=https://example.com/map.png` | 통과 |\n"
            + "| [THUMBNAIL] | `[THUMBNAIL]` | `thumbnail.png` |\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ContractError, "conflicts with its marker metadata"):
            _ = validate_image_map(image_map, ["image-01.png"], "thumbnail.png")

    def test_official_source_rejects_malformed_https_authorities(self) -> None:
        image_map = self.root / "image-map.md"
        marker = "`[IMAGE: slot 1]`"
        tail = "| [THUMBNAIL] | `[THUMBNAIL]` | `thumbnail.png` |\n"
        for source_url in (
            "https://not a url/map.png",
            "https://./map.png",
            "https://https://x/map.png",
            "https://example.com:invalid/map.png",
        ):
            _ = image_map.write_text(
                f"| 1 | VIS-01 | {marker} | `image-01.png` | identify | photograph | title_promise | official_or_licensed | scope | section | fallback | info | `origin=official; source_url={source_url}` | 통과 |\n"
                + tail,
                encoding="utf-8",
            )
            with self.subTest(source_url=source_url), self.assertRaises(ContractError):
                _ = validate_image_map(image_map, ["image-01.png"], "thumbnail.png")

    def test_image_map_rejects_unknown_provenance_enums(self) -> None:
        image_map = self.root / "image-map.md"
        tail = "| [THUMBNAIL] | `[THUMBNAIL]` | `thumbnail.png` |\n"
        for asset_type, source_policy in (
            ("MAP", "official_or_licensed"),
            ("map", "OFFICIAL_OR_LICENSED"),
            ("map", "official-or-licensed"),
            ("x", "x"),
        ):
            _ = image_map.write_text(
                f"| 1 | VIS-01 | `[IMAGE: asset_type={asset_type}; source_policy={source_policy}]` | `image-01.png` | identify | {asset_type} | title_promise | {source_policy} | scope | section | fallback | info | `origin=generated; Pillow local_render` | 통과 |\n"
                + tail,
                encoding="utf-8",
            )
            with self.subTest(asset_type=asset_type, source_policy=source_policy), self.assertRaises(ContractError):
                _ = validate_image_map(image_map, ["image-01.png"], "thumbnail.png")

    def test_official_source_rejects_malformed_uri_path_escapes(self) -> None:
        image_map = self.root / "image-map.md"
        tail = "| [THUMBNAIL] | `[THUMBNAIL]` | `thumbnail.png` |\n"
        for source_url in (
            "https://example.com/%ZZ",
            "https://example.com/%",
            "https://example.com/image<1>.png",
            "https://example.com/image[1].png",
            "https://example.com/#a#b",
            "https://example.com/image.png\x7f",
            "https://[2001:db8::1]/image[1].png",
            "https://example.com/image.png\x90",
        ):
            _ = image_map.write_text(
                f"| 1 | VIS-01 | `[IMAGE: slot 1]` | `image-01.png` | identify | original_photo | title_promise | official_or_licensed | scope | section | fallback | info | `origin=official; source_url={source_url}` | 통과 |\n"
                + tail,
                encoding="utf-8",
            )
            with self.subTest(source_url=source_url), self.assertRaises(ContractError):
                _ = validate_image_map(image_map, ["image-01.png"], "thumbnail.png")
        for source_url in (
            "https://example.com/image.png",
            "https://[2001:db8::1]/image.png",
        ):
            _ = image_map.write_text(
                f"| 1 | VIS-01 | `[IMAGE: slot 1]` | `image-01.png` | identify | original_photo | title_promise | official_or_licensed | scope | section | fallback | info | `origin=official; source_url={source_url}` | 통과 |\n"
                + tail,
                encoding="utf-8",
            )
            with self.subTest(valid_source_url=source_url):
                _ = validate_image_map(image_map, ["image-01.png"], "thumbnail.png")

    def test_image_map_rejects_raw_pipe_in_official_source_url(self) -> None:
        image_map = self.root / "image-map.md"
        _ = image_map.write_text(
            "| 1 | VIS-01 | `[IMAGE: slot 1]` | `image-01.png` | identify | original_photo | title_promise | official_or_licensed | scope | section | fallback | info | `origin=official; source_url=https://example.com/image|bad.png` | 통과 |\n"
            + "| [THUMBNAIL] | `[THUMBNAIL]` | `thumbnail.png` |\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ContractError, "exactly 14 columns"):
            _ = validate_image_map(image_map, ["image-01.png"], "thumbnail.png")

    def test_image_map_rejects_unquoted_provenance_suffix(self) -> None:
        image_map = self.root / "image-map.md"
        _ = image_map.write_text(
            "| 1 | VIS-01 | `[IMAGE: slot 1]` | `image-01.png` | identify | original_photo | title_promise | official_or_licensed | scope | section | fallback | info | `origin=official; source_url=https://example.com/image;` unquoted-note ` | 통과 |\n"
            + "| [THUMBNAIL] | `[THUMBNAIL]` | `thumbnail.png` |\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ContractError, "exactly 14 columns"):
            _ = validate_image_map(image_map, ["image-01.png"], "thumbnail.png")

        _ = image_map.write_text(
            "| 1 | VIS-01 | `[IMAGE: slot 1]` | `image-01.png` | identify | original_photo | title_promise | official_or_licensed | scope | section | fallback | info | `origin=official; source_url=https://example.com/image|bad.png` |\n"
            + "| [THUMBNAIL] | `[THUMBNAIL]` | `thumbnail.png` |\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ContractError, "exactly 14 columns"):
            _ = validate_image_map(image_map, ["image-01.png"], "thumbnail.png")

        _ = image_map.write_text(
            "| 1 | VIS-01 | `[IMAGE: slot 1]` | `image-01.png` | identify | original_photo | title_promise | official_or_licensed | scope | section | fallback | info | `origin=official; source_url=https://example.com/image|통과` |\n"
            + "| [THUMBNAIL] | `[THUMBNAIL]` | `thumbnail.png` |\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ContractError, "exactly 14 columns"):
            _ = validate_image_map(image_map, ["image-01.png"], "thumbnail.png")

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

    def test_truncated_png_signature_does_not_pass_image_validation(self) -> None:
        truncated = self.root / "truncated.png"
        _ = truncated.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

        self.assertFalse(has_image_signature(truncated))

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
        _ = draft.write_text(
            "[IMAGE: body image; fallback: [IMAGE:example]]\n"
            + "The literal [IMAGE: example] is explanatory text, not another slot.",
            encoding="utf-8",
        )
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
            "| 1 | VIS-01 | [IMAGE: body image; fallback: [IMAGE:example]] | `body.png` | identify | original_photo | title_promise | generated_allowed | scope | section | fallback | info | `origin=generated; method=local_render` | 통과 |\n"
            + "| [THUMBNAIL] | `[THUMBNAIL]` | `thumbnail.png` |\n",
            encoding="utf-8",
        )

        result = validate_image_stage_assets(asset_dir, draft)
        self.assertEqual(result["body_markers"], 1)
        self.assertEqual(result["outputs"], 2)

        records = [
            json.loads(line)
            for line in (asset_dir / "image-generation.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        records[0]["generation_control"] = "unavailable"
        _ = (asset_dir / "image-generation.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ContractError, "not production-ready"):
            _ = validate_image_stage_assets(asset_dir, draft)

        records[0]["generation_control"] = "locked"
        _ = (asset_dir / "image-generation.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        (asset_dir / "body.png").unlink()
        with self.assertRaises(ContractError):
            _ = validate_image_stage_assets(asset_dir, draft)

    def test_image_stage_rejects_swapped_or_truncated_markers(self) -> None:
        asset_dir = self.root / "assets" / "topic"
        asset_dir.mkdir(parents=True)
        draft = self.root / "drafts" / "topic.md"
        draft.parent.mkdir(parents=True)
        _ = draft.write_text("[IMAGE: first]\n[IMAGE: second]\n", encoding="utf-8")
        outputs = {"body-01.png": self._image_bytes(), "body-02.png": self._image_bytes() + b"2", "thumbnail.png": self._image_bytes() + b"thumb"}
        metadata_records: list[JSONMap] = []
        quality_records: list[JSONMap] = []
        for name, image in outputs.items():
            output = asset_dir / name
            _ = output.write_bytes(image)
            digest = f"sha256:{hashlib.sha256(image).hexdigest()}"
            metadata_records.append(
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
            quality_records.append(
                {
                    "image_sha256": digest,
                    "automated_checks": {key: "passed" for key in AUTOMATED_CHECKS},
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
                    "mobile_render_sha256": f"sha256:{hashlib.sha256(mobile_image).hexdigest()}",
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
            "| 1 | VIS-01 | [IMAGE: second] | `body-01.png` | identify | original_photo | title_promise | generated_allowed | scope | section | fallback | info | `origin=generated; method=local_render` | 통과 |\n"
            + "| 2 | VIS-02 | [IMAGE: first] | `body-02.png` | identify | original_photo | title_promise | generated_allowed | scope | section | fallback | info | `origin=generated; method=local_render` | 통과 |\n"
            + "| [THUMBNAIL] | `[THUMBNAIL]` | `thumbnail.png` |\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ContractError, "marker order"):
            _ = validate_image_stage_assets(asset_dir, draft)


if __name__ == "__main__":
    _ = unittest.main()

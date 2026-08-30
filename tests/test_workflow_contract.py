from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import final, override

from tools.manifest import ManifestBuildInput
from tools.workflow_contract import (
    ContractError,
    JSONMap,
    build_manifest,
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
            _ = build_manifest(self._manifest_input())

    def test_manifest_includes_naver_input_when_present(self) -> None:
        input_path = self.root / "final" / f"{self.keyword}-naver-input.md"
        _ = input_path.write_text("# Naver input\n", encoding="utf-8")

        manifest = build_manifest(self._manifest_input())

        files = manifest["files"]
        if not isinstance(files, list):
            self.fail("manifest files must be a list")
        paths: list[str] = []
        for value in files:
            if not isinstance(value, dict):
                self.fail("manifest file entry must be an object")
            path = value.get("path")
            if not isinstance(path, str):
                self.fail("manifest file path must be a string")
            paths.append(path)
        self.assertIn(f"final/{self.keyword}-naver-input.md", paths)

    def test_manifest_requires_final_markdown(self) -> None:
        final_path = self.root / "final" / f"{self.keyword}.md"
        final_path.unlink()
        with self.assertRaises(ContractError):
            _ = build_manifest(self._manifest_input())

    def test_manifest_rejects_body_image_byte_change(self) -> None:
        _ = verify_manifest(self.root, self.manifest_path)
        _ = (self.root / "assets" / self.keyword / "body.png").write_bytes(
            b"changed-body-image"
        )
        with self.assertRaises(ContractError):
            _ = verify_manifest(self.root, self.manifest_path)

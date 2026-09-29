from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.contract_types import ContractError
from tools.dashboard_preview import preview_asset, run_preview
from tools.manifest import ManifestBuildInput, build_manifest


def _preview_run(root: Path) -> tuple[str, Path]:
    keyword = "미리보기"
    run_id = "RUN-preview"
    final = root / "final"
    assets = root / "assets" / keyword
    manifests = root / "manifests"
    state_dir = root / ".automation" / "state"
    for directory in (final, assets, manifests, state_dir):
        directory.mkdir(parents=True, exist_ok=True)
    _ = (assets / "thumbnail.png").write_bytes(b"png-thumbnail")
    _ = (assets / "body.png").write_bytes(b"png-body")
    _ = (assets / "image-map.md").write_text("# map\n", encoding="utf-8")
    _ = (final / f"{keyword}.md").write_text(
        f"![본문](../assets/{keyword}/body.png)\n", encoding="utf-8"
    )
    copy = """[TITLE]검증된 제목[/TITLE]
[TEXT]긴 한글 본문을 원문 순서대로 표시합니다.[/TEXT]
[IMAGE file="thumbnail.png" alt="대표 이미지" representative=true]
[HEADING level=2]소제목[/HEADING]
[TABLE title="표"]
[ROW]항목=값; 상태=확정[/ROW]
[/TABLE]
[IMAGE file="body.png" alt="본문 이미지" representative=false]
"""
    for suffix in ("-naver-layout.md", "-naver-copy.md", "-naver-input.md"):
        _ = (final / f"{keyword}{suffix}").write_text(copy, encoding="utf-8")
    manifest_path = manifests / f"{run_id}-workflow-manifest.json"
    manifest = build_manifest(
        ManifestBuildInput(
            root,
            keyword,
            run_id,
            "TOPIC-preview",
            "2026-09-11T12:00:00+09:00",
        )
    )
    _ = manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    state = {
        "run_id": run_id,
        "manifest_path": manifest_path.relative_to(root).as_posix(),
        "artifact_digest": manifest["artifact_digest"],
        "storage_integrity": "passed",
        "notion_page_id": "page-one",
        "notion_page_url": "https://www.notion.so/page-one",
        "stages": {"content-assembler": "passed"},
    }
    _ = (state_dir / f"{run_id}.json").write_text(json.dumps(state), encoding="utf-8")
    return run_id, final / f"{keyword}-naver-input.md"


def test_preview_preserves_canonical_blocks_and_verified_notion_link(tmp_path: Path) -> None:
    # Given
    run_id, _input_path = _preview_run(tmp_path)

    # When
    result = run_preview(tmp_path, run_id)

    # Then
    assert result["title"] == "검증된 제목"
    blocks = result["blocks"]
    assert isinstance(blocks, list)
    assert [block["type"] for block in blocks if isinstance(block, dict)] == [
        "text",
        "image",
        "heading",
        "table",
        "image",
    ]
    assert result["notion_link"] == {
        "status": "ready",
        "url": "https://www.notion.so/page-one",
    }
    image = blocks[-1]
    assert isinstance(image, dict)
    body, content_type = preview_asset(tmp_path, run_id, str(image["asset_id"]))
    assert body == b"png-body"
    assert content_type == "image/png"


def test_preview_rejects_artifact_changed_after_manifest(tmp_path: Path) -> None:
    # Given
    run_id, input_path = _preview_run(tmp_path)
    _ = input_path.write_text("changed", encoding="utf-8")

    # When / Then
    with pytest.raises(ContractError, match="manifest artifact changed"):
        _ = run_preview(tmp_path, run_id)

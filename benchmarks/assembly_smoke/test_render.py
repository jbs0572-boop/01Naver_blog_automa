from pathlib import Path

import pytest

from benchmarks.assembly_smoke.check import (
    ASSETS,
    compare_golden,
    validate_contract,
)
from benchmarks.assembly_smoke.render import render, render_files
from benchmarks.assembly_smoke.run import prepare

KEYWORD = "fixture-dessert"


def _fixture_project(root: Path) -> tuple[Path, str]:
    project = root / "golden"
    draft_dir = project / "drafts"
    asset_dir = project / "assets" / KEYWORD
    draft_dir.mkdir(parents=True)
    asset_dir.mkdir(parents=True)
    markers = [
        f"[IMAGE: visual_slot_id=VS-{index:02}; alt=사진 {index}]"
        for index in range(1, 4)
    ]
    draft = (
        "# 테스트 디저트 후기\n\n"
        "직접 확인한 정보만 정리합니다.\n\n"
        + "\n\n".join(markers[:2])
        + "\n\n## 메뉴와 가격\n\n"
        "| 메뉴 | 가격 |\n| --- | --- |\n| 찹쌀떡 | 3,000원 |\n\n"
        + "\n\n".join(markers[2:])
        + "\n\n## 정보 출처\n\n"
        "- [공식 안내](https://example.com) — 페이지 확인\n"
    )
    image_map = "\n".join(
        [
            "| 역할 | 슬롯 | 설명 | 파일 |",
            *(
                f"| 본문 | VS-{index:02} | 음식 사진 | image-{index:02}.png |"
                for index in range(1, 4)
            ),
            "| [THUMBNAIL] | 대표 | thumbnail.png | 디저트 대표 사진 |",
        ]
    )
    (draft_dir / f"{KEYWORD}.md").write_text(draft, encoding="utf-8")
    (asset_dir / "image-map.md").write_text(image_map + "\n", encoding="utf-8")
    for filename in ASSETS[1:]:
        (asset_dir / filename).write_bytes(f"fixture:{filename}".encode())
    (project / "final").mkdir()
    for name, content in render(draft, image_map, KEYWORD).items():
        (project / "final" / name).write_text(content, encoding="utf-8")
    return project, draft


def test_fixture_draft_preserves_content_and_asset_bytes(
    tmp_path: Path,
) -> None:
    project, draft = _fixture_project(tmp_path)
    root = tmp_path / "case"
    prepare(root, project, KEYWORD)
    render_files(root, KEYWORD)
    validate_contract(root, KEYWORD)
    compare_golden(root, project, KEYWORD)
    expected_final = draft
    for index in range(1, 4):
        marker = f"[IMAGE: visual_slot_id=VS-{index:02}; alt=사진 {index}]"
        expected_final = expected_final.replace(
            marker,
            f"![사진 {index}](../assets/{KEYWORD}/image-{index:02}.png)",
        )
    assert (root / "final" / f"{KEYWORD}.md").read_text(encoding="utf-8") == expected_final


def test_missing_slot_fails_instead_of_dropping_image() -> None:
    draft = "# topic\n\n[IMAGE: visual_slot_id=VS-99; alt=unknown]\n"
    mapping = "| role | VS-01 | use | image-01.png |\n| [THUMBNAIL] | 대표 | thumbnail.png | 대표 이미지 |\n"
    with pytest.raises(ValueError, match="unmapped"):
        _ = render(
            draft.replace("visual_slot_id=VS-02", "visual_slot_id=VS-99"),
            mapping,
            KEYWORD,
        )


def test_comparator_detects_mutated_text_and_assets(tmp_path: Path) -> None:
    project, _draft = _fixture_project(tmp_path)
    root = tmp_path / "case"
    prepare(root, project, KEYWORD)
    render_files(root, KEYWORD)
    path = root / "final" / f"{KEYWORD}-naver-copy.md"
    original = path.read_text()
    _ = path.write_text(original.replace("찹쌀떡", "무료 메뉴"))
    with pytest.raises(ValueError, match="mismatch"):
        compare_golden(root, project, KEYWORD)
    _ = path.write_text(original)
    _ = (root / "assets" / KEYWORD / "image-01.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="asset changed"):
        compare_golden(root, project, KEYWORD)

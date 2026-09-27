from pathlib import Path

import pytest

from benchmarks.assembly_smoke.check import KEYWORD, compare_golden, validate_contract
from benchmarks.assembly_smoke.render import render, render_files
from benchmarks.assembly_smoke.run import PROJECT, prepare


def test_real_draft_preserves_historical_content_and_asset_bytes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "case"
    prepare(root)
    render_files(root, KEYWORD)
    validate_contract(root)
    compare_golden(root, PROJECT)


def test_missing_slot_fails_instead_of_dropping_image() -> None:
    draft = (PROJECT / "drafts" / f"{KEYWORD}.md").read_text()
    mapping = (PROJECT / "assets" / KEYWORD / "image-map.md").read_text()
    with pytest.raises(ValueError, match="unmapped"):
        _ = render(
            draft.replace("visual_slot_id=VS-02", "visual_slot_id=VS-99"),
            mapping,
            KEYWORD,
        )


def test_comparator_detects_mutated_text_and_assets(tmp_path: Path) -> None:
    root = tmp_path / "case"
    prepare(root)
    render_files(root, KEYWORD)
    path = root / "final" / f"{KEYWORD}-naver-copy.md"
    original = path.read_text()
    _ = path.write_text(original.replace("가격은 얼마인가요", "가격은 무료인가요"))
    with pytest.raises(ValueError, match="mismatch"):
        compare_golden(root, PROJECT)
    _ = path.write_text(original)
    _ = (root / "assets" / KEYWORD / "image-01.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="asset changed"):
        compare_golden(root, PROJECT)

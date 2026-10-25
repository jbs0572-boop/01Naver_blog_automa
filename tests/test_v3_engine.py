"""Actual Chromium regression tests for V3 (all photos in CI are synthetic fixtures).

This verifies image composition and document integrity, NOT actual-photo rights,
editorial truth, aesthetic judgments, account access or traffic outcomes.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import zipfile

from PIL import Image, ImageDraw
import pytest

from editorial_studio import engine as source_engine, studio
from editorial_studio.gallery import curated_article
from editorial_studio.v3 import engine

ROOT = Path(__file__).resolve().parents[1]


def build_fixture_archive(tmp_path: Path) -> Path:
    """Package five real frozen *fact strings* plus five synthetic photo test images."""
    raw = json.loads((ROOT / "editorial_studio/benchmarks/cases.json").read_text(encoding="utf-8"))
    text = json.loads((ROOT / "editorial_studio/examples/gallery5_copy.json").read_text(encoding="utf-8"))
    cases = {x["case_id"]: x for x in raw["cases"]}
    root = tmp_path / "fixture-data"
    original = root / "approved-originals"
    original.mkdir(parents=True)
    for index, category in enumerate(engine.CATEGORIES):
        case = source_engine.load_case(cases[category + "-2"])
        profile = studio.profiles()[category]
        article = curated_article(case, text["entries"][category + "-2"], profile)
        folder = root / "articles" / (category + "-fixture")
        folder.mkdir(parents=True)
        (folder / "sourcepack.json").write_text(
            json.dumps(case, ensure_ascii=False), encoding="utf-8"
        )
        (folder / "article-structure.json").write_text(
            json.dumps(article, ensure_ascii=False), encoding="utf-8"
        )
        picture = Image.new("RGB", (850, 590), (95 + 19 * index, 120, 170))
        draw = ImageDraw.Draw(picture)
        draw.rectangle((65, 85, 570, 450), fill=(185, 95 + 21 * index, 62))
        draw.ellipse((260, 105, 770, 510), fill=(35, 55 + 16 * index, 115))
        path = original / (category + ".jpg")
        picture.save(path, quality=91)
        (original / (category + ".source.json")).write_text(
            json.dumps({"credit": "SYNTHETIC FIXTURE — NOT AN AUTHENTIC PHOTO",
                        "downloaded_sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
                       ensure_ascii=False), encoding="utf-8"
        )
    archive = tmp_path / "synthetic-source.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for item in sorted(root.rglob("*")):
            if item.is_file():
                out.write(item, item.relative_to(root).as_posix())
    return archive


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    folder = tmp_path_factory.mktemp("v3-actual-chromium")
    return engine.generate(build_fixture_archive(folder), folder / "output")


def test_five_distinct_articles_each_have_three_real_files_and_role_ids(rendered):
    assert rendered["status"] == "PASS"
    assert rendered["threshold"] == 95
    assert rendered["images_total"] == 15
    assert rendered["screenshots_total"] == 10
    assert len(rendered["results"]) == 5
    assert len({row["title"] for row in rendered["results"]}) == 5
    assert len({row["category"] for row in rendered["results"]}) == 5
    for row in rendered["results"]:
        assert row["image_count"] == 3
        assert row["original_same_sha256"] is True
        assert row["evaluation"]["score"] >= engine.MIN_SCORE
        assert row["evaluation"]["status"] == "REVIEW_CANDIDATE"
        assert row["evaluation"]["human_review"] == "PENDING"
        assert row["evaluation"]["published"] is False
        assert row["mobile_qa"]["roles"] == [
            "context-photo", "information-graphic", "action-checklist"
        ]
        assert row["mobile_qa"]["issues"] == []
        assert row["desktop_qa"]["issues"] == []
        assert all(item["status"] == "PASS" for item in row["diagram_qa"])
        assert all(1 <= item["attempts"] <= 3 for item in row["diagram_qa"])
        assert len(row["screenshot_sha256"]) == 64


def test_refuse_path_escape_inside_source_archive(tmp_path):
    filename = tmp_path / "bad.zip"
    with zipfile.ZipFile(filename, "w") as out:
        out.writestr("../do-not-write.txt", "malicious")
    with pytest.raises(ValueError, match="unsafe path"):
        engine._extract_source(filename, tmp_path / "target")


def test_refuse_tampered_original_without_claiming_success(tmp_path):
    archive = build_fixture_archive(tmp_path)
    unpack = tmp_path / "unpack"
    unpack.mkdir()
    engine._extract_source(archive, unpack)
    picture = unpack / "approved-originals" / "economy.jpg"
    picture.write_bytes(picture.read_bytes() + b"tampered bytes")
    with pytest.raises(ValueError, match="original source hash changed"):
        engine._source_category(unpack, "economy")


def test_long_card_text_is_detected_by_dom_not_just_pixel_statistics():
    from playwright.sync_api import sync_playwright
    from shutil import which

    with sync_playwright() as pw:
        executable = os.environ.get("CHROMIUM_PATH") or which("chromium")
        browser = pw.chromium.launch(
            headless=True, executable_path=executable,
            args=["--no-sandbox"] if executable else []
        )
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            markup = engine.card_html("news_policy", "facts", "정보",
                                       ["가" * 500, "나" * 500, "다" * 500],
                                       "출처", 2)
            page.set_content(markup)
            problems = page.evaluate(engine.QA_JS)["issues"]
            assert problems
            assert any(("clipped" in p or "out-of-bounds" in p
                        or "overlaps-footer" in p) for p in problems)
        finally:
            browser.close()


def test_workflow_markdown_rules_remain_human_editable():
    path = Path(engine.__file__).resolve().parent
    intent = (path / "INTEND.md").read_text(encoding="utf-8")
    roles = (path / "IMAGE_ROLES.md").read_text(encoding="utf-8")
    rubric = (path / "QUALITY_GATE.md").read_text(encoding="utf-8")
    assert "자동 수행 영역" in intent
    assert "사람이 갱신할 Markdown 규칙" in intent
    assert "최소 3개" in roles
    assert "95점 이상" in rubric


def test_missing_visual_role_or_unknown_category_never_passes():
    assert engine.MIN_SCORE == 95
    with pytest.raises(ValueError, match="three"):
        engine.card_html("news_policy", "facts", "제목", ["두 항목", "뿐"], "출처", 0)
    with pytest.raises(KeyError):
        engine.card_html("not_a_style", "facts", "제목", ["1", "2", "3"], "출처", 0)

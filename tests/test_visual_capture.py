"""Actual Chromium capture and original-asset regression tests (no paid APIs)."""
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from editorial_studio.visual_capture import VisualError, html_slide, prepare_spec, render


def spec(category="life_health"):
    return {
        "category": category,
        "headline": "창문을 열어도 환기가 잘되지 않는 이유",
        "eyebrow": "LIFE · ARTICLE GUIDE",
        "deck": "외부 공기와 실내 오염원, 그리고 공간 구조를 함께 확인하면 방법을 이해할 수 있습니다.",
        "bullets": [
            "외부 공기 상태를 먼저 확인합니다.",
            "공기가 들어오는 위치와 나가는 위치를 구분합니다.",
            "오염을 일으키는 원인을 우선 줄입니다.",
        ],
        "source_caption": "검수용 시각 자료 · 실제 공기 흐름을 측정한 결과는 아닙니다",
    }


def test_invalid_categories_and_inputs_rejected():
    for field, value in [("category", "other"), ("headline", ""), ("deck", ""),
                         ("bullets", []), ("source_caption", "\n")]:
        data = spec()
        data[field] = value
        with pytest.raises(VisualError):
            prepare_spec(data)


def test_markup_has_no_remote_media_or_hidden_approval():
    data = spec()
    data["headline"] = '<script>alert(1)</script>'
    result = html_slide(prepare_spec(data), photo_data=None, attempt=1)
    assert "&lt;script&gt;" in result
    assert '<script>' not in result
    assert "Content-Security-Policy" in result
    assert "img-src data:" in result
    assert "KEY POINTS" in result
    with pytest.raises(VisualError):
        html_slide(prepare_spec(spec()), photo_data=None, attempt=4)


@pytest.mark.parametrize("category", [
    "life_health", "news_policy", "culture_people", "real_estate", "economy"
])
def test_all_five_categories_render_desktop_and_mobile_without_clips(tmp_path, category):
    out = tmp_path / category
    result = render(spec(category), out)
    assert result.status == "PASS", result.issues
    assert result.provenance["photo_used"] is False
    assert (out / "slide.html").is_file()
    assert (out / "cover.png").is_file()
    assert (out / "mobile.png").is_file()
    assert json.loads((out / "result.json").read_text())["status"] == "PASS"
    with Image.open(out / "cover.png") as image:
        assert image.size == (1536, 1024)
    assert not list(out.glob("original.*"))


def test_user_approved_original_keeps_exact_source_bytes(tmp_path):
    image_file = tmp_path / "supplied.jpg"
    image = Image.new("RGB", (1100, 740), (187, 200, 205))
    ImageDraw.Draw(image).ellipse((200, 110, 800, 620), fill=(51, 96, 119))
    image.save(image_file, format="JPEG", quality=94)
    data = spec("culture_people")
    data["source_photo"] = str(image_file)
    out = tmp_path / "original_cover"
    result = render(data, out)
    assert result.status == "PASS", result.issues
    assert result.provenance["photo_used"]
    assert result.provenance["byte_identical"]
    assert (out / "original.jpg").read_bytes() == image_file.read_bytes()
    assert result.provenance["kind"] == "user_verified_original"
    assert "object-fit:contain" in (out / "slide.html").read_text()
    with pytest.raises(VisualError, match="overwrite"):
        render(data, out)


def test_no_real_original_means_no_photo_fabrication(tmp_path):
    data = spec()
    data["source_photo"] = str(tmp_path / "missing.png")
    with pytest.raises(VisualError):
        render(data, tmp_path / "missing_output")
    assert not (tmp_path / "missing_output" / "cover.png").exists()


def test_overloaded_text_is_retried_and_fail_closed(tmp_path):
    data = spec()
    data["headline"] = ("여백과 글자 잘림을 확실히 확인해야 하는 이유와 조건 " * 4)[:100]
    data["deck"] = ("원본 사진을 보존하면서 불필요한 사실을 추가하지 않도록 합니다. " * 6)[:240]
    data["bullets"] = [("매 단계에서 실제 DOM의 글자 범위와 이미지 로딩 여부를 검사합니다. " * 7)[:155]] * 4
    out = tmp_path / "overload"
    result = render(data, out)
    assert result.status == "QA_FAIL"
    assert result.attempts == 3
    assert not (out / "cover.png").exists()
    assert all((out / f"attempt-{n}-qa.json").exists() for n in (1, 2, 3))
    assert any("outside-slide" in value or "overlaps-footer" in value for value in result.issues)


def test_engine_original_mode_integrates_into_article_pipeline(tmp_path):
    from editorial_studio import engine
    case = json.loads((Path(__file__).resolve().parents[1] /
                       "editorial_studio/benchmarks/cases.json").read_text())["cases"][0]
    case_file = tmp_path / "case.json"
    case_file.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    source = tmp_path / "approved.jpg"
    image = Image.new("RGB", (1280, 850), "#b7cad0")
    ImageDraw.Draw(image).rectangle((100, 150, 600, 620), fill="#245b70")
    image.save(source, quality=95)
    folder = engine.run(case_file, tmp_path / "outputs", text_backend="fixture",
                        image_backend="auto", imported_image=source)
    meta = json.loads((folder / "result.json").read_text())
    assert meta["image_backend"] == "original"
    assert meta["state"] == "IMAGE_READY_FOR_REVIEW"
    assert meta["visual_qa"]["status"] == "PASS"
    assert meta["visual_qa"]["provenance"]["photo_used"]
    assert not meta["images_created_by_this_run"]
    assert not meta["score"]["ready_for_publication"]
    assert (folder / "visual-assets" / "original.jpg").read_bytes() == source.read_bytes()


def test_hidden_parent_clipping_is_detected_in_browser():
    from playwright.sync_api import sync_playwright
    from editorial_studio.visual_capture import DOM_INSPECTION
    page_html = html_slide(prepare_spec(spec()), None, 1)
    # Force only the visual panel to clip its inner text without moving the slide.
    page_html = page_html.replace("height:520px;background:", "height:70px;background:")
    with sync_playwright() as pw:
        import os
        import shutil
        exe = os.environ.get("CHROMIUM_PATH") or shutil.which("chromium") or shutil.which("google-chrome")
        browser = pw.chromium.launch(headless=True, executable_path=exe, args=["--no-sandbox"] if exe else [])
        page = browser.new_page(viewport={"width":1536, "height":1024})
        page.set_content(page_html, wait_until="load")
        result = page.evaluate(DOM_INSPECTION)
        browser.close()
    assert any("hidden-parent-y" in issue for issue in result["issues"]), result


def test_original_photo_is_not_labeled_as_fake_in_article():
    from editorial_studio import engine, studio
    case = json.loads((Path(__file__).resolve().parents[1] /
                       "editorial_studio/benchmarks/cases.json").read_text())["cases"][0]
    parsed = engine.load_case(case)
    profile = studio.profiles()[parsed["category"]]
    article = engine.fixture_article(parsed, profile)
    markdown = engine.render_markdown(parsed, article, True, original_photo=True)
    page = engine.render_html(parsed, article, True, original_photo=True)
    assert "사용자가 승인한 원본 사진" in markdown
    assert "원본 사진 사용" in page
    assert "실제 자료 사진이 아님" not in page
    assert "개념을 설명하는 보조 자료" not in markdown
    concept = engine.render_html(parsed, article, True, original_photo=False)
    assert "실제 자료 사진이 아님" in concept


def test_category_specific_css_layout_contract():
    code = html_slide(prepare_spec(spec("life_health")), None, 1)
    assert ".culture_people .stage" in code
    assert ".real_estate .stage" in code
    assert ".economy .stage" in code
    assert "KEY POINTS" in code


def test_four_short_diagram_steps_fit_mobile(tmp_path):
    data = spec("real_estate")
    data["bullets"] = ["대상 확인", "서류 확인", "조건 비교", "최종 점검"]
    result = render(data, tmp_path / "four")
    assert result.status == "PASS", result.issues
    first = json.loads((tmp_path / "four" / "attempt-1-qa.json").read_text())
    assert not first["mobile"]["issues"]


def test_multiline_fixture_intro_is_valid_for_html_visual(tmp_path):
    from unittest.mock import patch
    from editorial_studio import engine
    source = json.loads((Path(__file__).resolve().parents[1] /
                         "editorial_studio/benchmarks/cases.json").read_text())["cases"][0]
    spec_file = tmp_path / "case.json"
    spec_file.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    base_writer = engine.fixture_article

    def multiline_writer(case, profile):
        article = base_writer(case, profile)
        article["intro"] = "첫 번째 설명입니다.\n두 번째 설명입니다.\t세 번째 설명입니다."
        return article

    with patch.object(engine, "fixture_article", side_effect=multiline_writer):
        bundle = engine.run(spec_file, tmp_path / "results",
                            text_backend="fixture", image_backend="html")
    report = json.loads((bundle / "result.json").read_text(encoding="utf-8"))
    assert report["visual_qa"]["status"] == "PASS"
    assert (bundle / "cover.png").exists()
    assert not report["images_created_by_this_run"]


def test_multiline_headings_and_title_are_normalized_for_image_only(tmp_path):
    from unittest.mock import patch
    from editorial_studio import engine
    source = json.loads((Path(__file__).resolve().parents[1] /
                         "editorial_studio/benchmarks/cases.json").read_text())["cases"][0]
    case_file = tmp_path / "case.json"
    case_file.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    original = engine.fixture_article

    def validated_multiline_article(case, profile):
        article = original(case, profile)
        article["title"] = article["title"].replace(" ", "\n", 1)
        article["sections"][0]["heading"] = "먼저 확인할\n답"
        return article

    with patch.object(engine, "fixture_article", side_effect=validated_multiline_article):
        bundle = engine.run(case_file, tmp_path / "runs", text_backend="fixture",
                            image_backend="html")
    metadata = json.loads((bundle / "result.json").read_text())
    assert metadata["visual_qa"]["status"] == "PASS"
    assert "먼저 확인할\n답" in (bundle / "draft.md").read_text()
    assert (bundle / "cover.png").is_file()

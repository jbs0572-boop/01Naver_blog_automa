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

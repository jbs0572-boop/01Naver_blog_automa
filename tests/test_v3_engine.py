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
        (folder / "style-lock.json").write_text(
            json.dumps({"category": category, "profile_sha256": profile["sha256"]}),
            encoding="utf-8"
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
        assert len(set(row["image_sha256"].values())) == 3
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



def test_refuse_changed_frozen_profile_hash(tmp_path):
    archive = build_fixture_archive(tmp_path)
    folder = tmp_path / "read"
    folder.mkdir()
    engine._extract_source(archive, folder)
    path = next((folder / "articles").glob("news_policy-*/style-lock.json"))
    record = json.loads(path.read_text(encoding="utf-8"))
    record["profile_sha256"] = "0" * 64
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="frozen style profile SHA-256 mismatch"):
        engine._source_category(folder, "news_policy")


def test_section_cites_only_its_own_fact_sources_and_preserves_photo_credit():
    editor = json.loads((ROOT / "editorial_studio/v3/cases.json").read_text(encoding="utf-8"))["articles"][0]
    pack = {
        "topic": "실내 습도", "as_of": "2026-10-22",
        "sources": [
            {"id": "S1", "title": "첫 번째 기관", "url": "https://example.com/first"},
            {"id": "S2", "title": "두 번째 기관", "url": "https://example.org/second"},
        ],
        "facts": [
            {"id": f"F{i}", "role": i, "source_id": ("S2" if i == 0 else "S1"),
             "text": f"검증된 사실 {i}"} for i in range(5)
        ],
    }
    article = {
        "sections": [
            {"role": i, "heading": f"내용 {i}", "body": f"검증된 본문 {i}",
             "fact_ids": [f"F{i}"]} for i in range(5)
        ]
    }
    rendered = engine.create_article(
        "life_health", pack, article, "original.jpg", "facts.png", "check.png",
        "맥락 사진", editor,
        {"credit": "권리 확인한 사진작가",
         "page_url": "https://example.net/attribution",
         "license": "CC BY 2.0"}
    )
    sections = rendered.split("<section>")
    assert len(sections) == 6
    assert "https://example.org/second" in sections[1]
    assert "https://example.com/first" not in sections[1]
    assert "https://example.com/first" in sections[2]
    assert "https://example.org/second" not in sections[2]
    assert "권리 확인한 사진작가" in rendered
    assert "https://example.net/attribution" in rendered
    assert "CC BY 2.0" in rendered


def test_missing_reference_fails_closed_before_publishing():
    editor = json.loads((ROOT / "editorial_studio/v3/cases.json").read_text(encoding="utf-8"))["articles"][0]
    pack = {"topic": "실내", "as_of": "2026-10-22",
            "sources": [{"id": "S1", "title": "공식", "url": "https://example.com"}],
            "facts": [{"id": "F1", "source_id": "S2", "role": 0, "text": "내용"}]}
    article = {"sections": [{"role": i, "body": "근거 있는 내용", "fact_ids": ["F1"]}
                            for i in range(5)]}
    with pytest.raises(ValueError, match="unknown source/fact ID"):
        engine.create_article("life_health", pack, article, "original.jpg",
                              "facts.png", "check.png", "사진", editor)



def test_missing_section_evidence_is_never_accepted():
    editor = json.loads((ROOT / "editorial_studio/v3/cases.json").read_text(encoding="utf-8"))["articles"][0]
    pack = {
        "topic": "실내 습도", "as_of": "2026-10-22",
        "sources": [{"id": "S1", "title": "공식 원문", "url": "https://example.org"}],
        "facts": [
            {"id": f"F{i}", "role": i, "source_id": "S1", "text": f"검증 근거 {i}"}
            for i in range(5)
        ],
    }
    article = {
        "sections": [
            {"role": i, "heading": f"제목 {i}", "body": f"구체적인 설명 {i}",
             "fact_ids": ([] if i == 2 else [f"F{i}"])}
            for i in range(5)
        ]
    }
    with pytest.raises(ValueError, match="at least one source-backed fact"):
        engine.create_article("life_health", pack, article, "original.jpg",
                              "facts.png", "check.png", "사진", editor)
    article["sections"][2]["fact_ids"] = ["F1"]
    with pytest.raises(ValueError, match="fact role"):
        engine.create_article("life_health", pack, article, "original.jpg",
                              "facts.png", "check.png", "사진", editor)



def test_mobile_canvas_horizontal_overflow_is_rejected_with_real_viewport():
    """A 1,000-px mobile slide must not pass an outdated 1,282-px check."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 800, "height": 1200})
            markup = engine.card_html(
                "news_policy", "facts", "근거 비교",
                ["첫째 근거", "둘째 근거", "셋째 근거"], "공식 출처", 0
            ).replace("</head>", "<style>#canvas{width:1000px!important}</style></head>")
            page.set_content(markup, wait_until="load")
            assert "horizontal-overflow" in page.evaluate(engine.QA_JS)["issues"]
        finally:
            browser.close()


def test_untrusted_source_url_cannot_create_javascript_evidence_link():
    editor = json.loads((ROOT / "editorial_studio/v3/cases.json").read_text(
        encoding="utf-8"
    ))["articles"][0]
    pack = {
        "topic": "실내", "as_of": "2026-10-22",
        "sources": [{"id": "S1", "title": "가짜 출처",
                     "url": "javascript:alert(document.domain)"}],
        "facts": [{"id": f"F{i}", "role": i, "source_id": "S1",
                   "text": f"문장 {i}"} for i in range(5)],
    }
    article = {
        "sections": [{"role": i, "heading": f"설명 {i}",
                      "body": f"근거를 확인하는 문장 {i}",
                      "fact_ids": [f"F{i}"]} for i in range(5)]
    }
    with pytest.raises(ValueError, match="source URL must be HTTPS"):
        engine.create_article(
            "life_health", pack, article, "original.jpg",
            "facts.png", "check.png", "원본 사진", editor
        )


def test_zip_archive_aggregate_expansion_limit_is_checked_before_writing(tmp_path, monkeypatch):
    """Thirty entries below the individual limit can still exceed a safe total."""
    class SyntheticMember:
        filename = "articles/source.json"
        file_size = 20_000_000
        compress_size = 12_000_000
        external_attr = 0
        def is_dir(self):
            return False

    class SyntheticZip:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def infolist(self):
            return [SyntheticMember() for _ in range(30)]

    monkeypatch.setattr(engine.zipfile, "ZipFile", lambda _: SyntheticZip())
    with pytest.raises(ValueError, match="total uncompressed size"):
        engine._extract_source(tmp_path / "not-a-real-zip.zip", tmp_path / "output")
    assert not (tmp_path / "output" / "articles").exists()



def test_v3_rejects_wrong_film_or_reference_date_even_with_matching_category():
    """Exact source-to-editorial identity is required, not just '영화...'."""
    editor_cases = json.loads(
        (ROOT / "editorial_studio/v3/cases.json").read_text(encoding="utf-8")
    )["articles"]
    archived = json.loads(
        (ROOT / "editorial_studio/benchmarks/cases.json").read_text(encoding="utf-8")
    )["cases"]
    expected = {case["category"]: case for case in archived
                if case["case_id"].endswith("-2")}
    for editorial in editor_cases:
        category = editorial["category"]
        pack = expected[category]
        engine.verify_source_case(category, pack, editorial)
        different = dict(pack, topic=pack["topic"] + " 다른 작품")
        with pytest.raises(ValueError, match="source topic mismatch"):
            engine.verify_source_case(category, different, editorial)
        expired = dict(pack, as_of="2020-01-01")
        with pytest.raises(ValueError, match="reference date mismatch"):
            engine.verify_source_case(category, expired, editorial)
    cinema = next(x for x in editor_cases if x["category"] == "culture_people")
    parasite = dict(expected["culture_people"], topic="영화 기생충")
    with pytest.raises(ValueError, match="source topic mismatch"):
        engine.verify_source_case("culture_people", parasite, cinema)




@pytest.mark.parametrize(
    ("malformation", "diagnostic"),
    [
        ("duplicate_source", "source IDs must be unique"),
        ("duplicate_fact", "fact IDs must be unique"),
        ("unsafe_source_url", "public HTTPS host"),
    ],
)
def test_archived_sources_cannot_bypass_original_evidence_validation(
    tmp_path, malformation, diagnostic
):
    """A valid lock does not legitimize corrupted IDs or unsafe evidence URLs."""
    archive = build_fixture_archive(tmp_path)
    extracted = tmp_path / "corrupt-evidence"
    extracted.mkdir()
    engine._extract_source(archive, extracted)
    path = next((extracted / "articles").glob("economy-*/sourcepack.json"))
    pack = json.loads(path.read_text(encoding="utf-8"))
    if malformation == "duplicate_source":
        pack["sources"].append(
            dict(pack["sources"][0], title="Conflicting citation",
                 url="https://example.org/conflicting")
        )
    elif malformation == "duplicate_fact":
        pack["facts"].append(
            dict(pack["facts"][0], text="Contradictory fact with same ID")
        )
    else:
        pack["sources"][0]["url"] = "https://127.0.0.1/private"
    path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match=diagnostic):
        engine._source_category(extracted, "economy")


def test_archived_draft_with_fabricated_firsthand_experience_fails(tmp_path):
    """An altered reviewed draft must still satisfy the primary article gate."""
    archive = build_fixture_archive(tmp_path)
    extracted = tmp_path / "corrupt-article"
    extracted.mkdir()
    engine._extract_source(archive, extracted)
    path = next((extracted / "articles").glob("life_health-*/article-structure.json"))
    article = json.loads(path.read_text(encoding="utf-8"))
    article["sections"][0]["body"] += " 제가 직접 다녀왔습니다."
    path.write_text(json.dumps(article, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="excluded phrase or fabricated experience"):
        engine._source_category(extracted, "life_health")


def test_matching_hash_does_not_make_a_fake_jpeg_an_original_photo(tmp_path):
    """A forged JPEG with a matching hash must fail before render and scoring."""
    archive = build_fixture_archive(tmp_path)
    extracted = tmp_path / "photo-check"
    extracted.mkdir()
    engine._extract_source(archive, extracted)
    original = extracted / "approved-originals" / "economy.jpg"
    original.write_bytes(b"THIS IS NOT A REAL IMAGE")
    provenance = extracted / "approved-originals" / "economy.source.json"
    info = json.loads(provenance.read_text(encoding="utf-8"))
    info["downloaded_sha256"] = hashlib.sha256(original.read_bytes()).hexdigest()
    provenance.write_text(json.dumps(info), encoding="utf-8")
    with pytest.raises(ValueError, match="not a decodable photo"):
        engine._source_category(extracted, "economy")


def test_matching_hash_does_not_allow_tiny_original_photo(tmp_path):
    from PIL import Image
    archive = build_fixture_archive(tmp_path)
    extracted = tmp_path / "small-photo-check"
    extracted.mkdir()
    engine._extract_source(archive, extracted)
    original = extracted / "approved-originals" / "economy.jpg"
    Image.new("RGB", (64, 64), "#717e8d").save(original, format="JPEG")
    provenance = extracted / "approved-originals" / "economy.source.json"
    info = json.loads(provenance.read_text(encoding="utf-8"))
    info["downloaded_sha256"] = hashlib.sha256(original.read_bytes()).hexdigest()
    provenance.write_text(json.dumps(info), encoding="utf-8")
    with pytest.raises(ValueError, match="dimensions/format invalid"):
        engine._source_category(extracted, "economy")

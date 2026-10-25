"""Regression checks for five genuinely distinct category article presentations."""
import json
import hashlib
import html as html_std
from pathlib import Path
import pytest
from editorial_studio import engine, studio
from editorial_studio.gallery import CASE_IDS, curated_article
from editorial_studio.visual_page import THEMES, SPECIFIC, themed_html

ROOT=Path(__file__).resolve().parents[1]
DATA=json.loads((ROOT/"editorial_studio/benchmarks/cases.json").read_text(encoding="utf-8"))
COPY=json.loads((ROOT/"editorial_studio/examples/gallery5_copy.json").read_text(encoding="utf-8"))["entries"]


@pytest.mark.parametrize("cid",CASE_IDS)
def test_five_authored_articles_use_only_source_bound_roles(cid):
    original=next(x for x in DATA["cases"] if x["case_id"]==cid)
    normalized=engine.load_case(original)
    profile,_=studio.choose(original["topic"],explicit=original["category"])
    article=curated_article(normalized,COPY[cid],profile)
    assert len(article["sections"])==5
    assert all("\n" in item["body"] for item in article["sections"])
    for section in article["sections"]:
        assert section["fact_ids"]
        assert any(f["text"] in section["body"] for f in normalized["facts"] if f["id"] in section["fact_ids"])
    rendered=engine.render_html(normalized,article,True)
    assert f'data-editorial="{normalized["category"]}"' in rendered
    assert THEMES[normalized["category"]][0] in rendered
    assert "cover.png" in rendered
    assert "네이버 미게시" in rendered


def test_five_theme_values_and_styles_are_independent():
    assert len(THEMES)==5
    assert len({x[3] for x in THEMES.values()})==5
    assert len({x[5] for x in THEMES.values()})==5
    assert len(SPECIFIC)==5
    assert all(SPECIFIC[key] for key in THEMES)


def test_theming_keeps_factual_text_and_evidence_links_unmodified():
    raw="<html><head><style></style></head><body><main><h1>실내 습도</h1>" \
        "<p class='meta'>근거: <a href='https://example.org'>원문</a></p></main></body></html>"
    output=themed_html(raw,"life_health")
    assert "<h1>실내 습도</h1>" in output
    assert "href='https://example.org'" in output
    assert "원문" in output


def test_unknown_category_does_not_fallback_to_wrong_style():
    with pytest.raises(ValueError):
        themed_html("<body><main></main></body>","missing")


def test_sources_are_not_replaced_with_image_facts():
    for cid in CASE_IDS:
        original=next(x for x in DATA["cases"] if x["case_id"]==cid)
        article=curated_article(engine.load_case(original),COPY[cid],studio.profiles()[original["category"]])
        html=engine.render_html(engine.load_case(original),article,True)
        assert all(html_std.escape(src["url"], quote=True) in html for src in original["sources"])
        assert "실제 자료 사진이 아님" in html


def test_original_first_remote_asset_import_accepts_only_real_bytes(tmp_path):
    from io import BytesIO
    from PIL import Image, ImageDraw
    from editorial_studio.gallery import import_approved_original
    im=Image.new("RGB",(700,480),"#d7e4ed")
    ImageDraw.Draw(im).rectangle((80,80,620,420),fill="#123953")
    output=BytesIO()
    im.save(output,format="JPEG",quality=89)
    payload=output.getvalue()
    class FakeResponse:
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def geturl(self):return "https://upload.wikimedia.org/test.jpg"
        def read(self,size):return payload[:size]
    record={
      "category":"economy","asset_url":"https://upload.wikimedia.org/test.jpg",
      "page_url":"https://commons.wikimedia.org/wiki/Example","credit":"author",
      "kind":"original_photograph","original_file_url":"https://upload.wikimedia.org/test.jpg"}
    path,provenance=import_approved_original(record,tmp_path,urlopen=lambda req,timeout:FakeResponse())
    assert path.read_bytes()==payload
    assert provenance["downloaded_sha256"]==hashlib.sha256(payload).hexdigest()
    assert provenance["user_confirmed_rights"] is True


def test_original_first_rejects_nonimage_or_unapproved_domain(tmp_path):
    from editorial_studio.gallery import import_approved_original
    record={
      "category":"economy","asset_url":"https://evil.example/photo.jpg",
      "page_url":"https://evil.example","credit":"author",
      "kind":"original_photograph","original_file_url":"https://evil.example/a.jpg"}
    with pytest.raises(ValueError,match="not approved"):
        import_approved_original(record,tmp_path,urlopen=lambda req,timeout:None)

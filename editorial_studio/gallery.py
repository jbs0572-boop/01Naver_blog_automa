"""Produce five *actual engine outputs* and mobile Chromium screenshot evidence.

Uses the second topic from each frozen category pair, with separately authored
narrative text and real HTML/CSS cover assets. No paid API or external publishing.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import urllib.request
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

from . import engine, quality

ROOT = Path(__file__).resolve().parent
CASE_IDS = ("life_health-2", "news_policy-2", "culture_people-2", "real_estate-2", "economy-2")
DISPLAY_NAMES = ("생활·건강", "뉴스·정책", "영화·인물", "부동산", "경제")


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))



ALLOWED_ASSET_HOSTS = frozenset((
    "thumb.wikimedia.org", "upload.wikimedia.org", "cdn.imweb.me",
    "img.festival-cannes.com", "newsimg.koreatimes.co.kr",
))
MAX_ORIGINAL_BYTES = 18_000_000


def import_approved_original(record: dict, root: Path, *, urlopen=urllib.request.urlopen) -> tuple[Path, dict]:
    """Import a REAL approved remote original. Do not generate any substitute."""
    category = record.get("category")
    if category not in engine.studio.PROFILE_IDS:
        raise ValueError("source manifest contains an unknown category")
    source_url = record.get("asset_url")
    if not isinstance(source_url, str):
        raise ValueError("source manifest is missing an image URL")
    parsed = urlsplit(source_url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_ASSET_HOSTS:
        raise ValueError("original image source host is not approved")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("original image URL contains unsafe authentication or port")
    request = urllib.request.Request(
        source_url, method="GET",
        headers={"User-Agent": "Mozilla/5.0 (compatible; EditorialStudioVisualQA/1.0)",
                 "Accept": "image/jpeg,image/png,image/webp"},
    )
    try:
        with urlopen(request, timeout=35) as response:
            final = urlsplit(response.geturl())
            if final.scheme != "https" or final.hostname not in ALLOWED_ASSET_HOSTS:
                raise ValueError("original image redirected outside the approved hosts")
            payload = response.read(MAX_ORIGINAL_BYTES+1)
    except (OSError, TimeoutError) as error:
        raise ValueError("approved original image could not be downloaded: " + category) from error
    if len(payload) > MAX_ORIGINAL_BYTES:
        raise ValueError("original image exceeds the approved size budget")
    try:
        with Image.open(BytesIO(payload)) as image:
            image.verify()
        with Image.open(BytesIO(payload)) as image:
            fmt = image.format
            width, height = image.size
    except OSError as error:
        raise ValueError("original image failed real pixel decoding") from error
    if fmt not in ("JPEG", "PNG", "WEBP") or width < 640 or height < 400:
        raise ValueError("original image is too small or not a real supported photograph")
    root.mkdir(parents=True, exist_ok=True)
    ext = ".jpg" if fmt == "JPEG" else ".png" if fmt == "PNG" else ".webp"
    destination = root / (category + ext)
    with destination.open("xb") as handle:
        handle.write(payload)
    digest = hashlib.sha256(payload).hexdigest()
    audit = dict(record)
    audit.update({
        "downloaded_asset": destination.name, "downloaded_sha256": digest,
        "actual_image_format": fmt, "actual_width": width, "actual_height": height,
        "bytes_preserved": len(payload), "user_confirmed_rights": True,
    })
    (root / (category + ".source.json")).write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destination, audit


def curated_article(case: dict, text: dict, profile: dict) -> dict:
    """Start from the frozen, role-linked engine article and add edited prose."""
    article = engine.fixture_article(case, profile)
    article["intro"] = text["intro"]
    assert len(text["more"]) == len(article["sections"]) == 5
    facts = {item["id"]: item for item in case["facts"]}
    for section, extra in zip(article["sections"], text["more"]):
        # Every new section preserves its original source-bound claim verbatim.
        cited = " ".join(facts[fid]["text"] for fid in section["fact_ids"])
        section["body"] = cited + "\n" + extra
    return engine.validate_article(article, case, profile)


def screenshot_qa(page, category: str) -> dict:
    return page.evaluate("""(expected) => {
       const sections=[...document.querySelectorAll('main section')];
       const title=document.querySelector('h1');
       const img=[...document.querySelectorAll('figure img')];
       const links=[...document.querySelectorAll('a[href]')];
       const badLinks=links.filter(a => a.protocol!=='https:');
       const issues=[];
       if (document.documentElement.scrollWidth > innerWidth+2) issues.push('horizontal-overflow');
       if (!title||!title.innerText.trim()) issues.push('missing-title');
       if (sections.length!==5) issues.push('not-five-sections');
       if (img.length<1||img.length>2||!img[0]?.complete||img[0].naturalWidth<1280) issues.push('cover-not-loaded');
       if (img.length===2 && (!document.querySelector('.source-photo img')||
           !img[1].complete||img[1].naturalWidth<640)) issues.push('original-photo-not-loaded');
       if (links.length<5||badLinks.length) issues.push('evidence-links-invalid');
       if (document.body.dataset.editorial!==expected) issues.push('wrong-category-theme');
       for (const [index, node] of [title,...document.querySelectorAll('h2')].entries()) {
         if (!node) continue;
         const rect=node.getBoundingClientRect();
         if (rect.left < -2 || rect.right > innerWidth+2) issues.push('heading-overflow-'+index);
       }
       return {issues, actualWidth:innerWidth, scrollWidth:document.documentElement.scrollWidth,
               fullHeight:document.documentElement.scrollHeight, sections:sections.length,
               loadedCover:img.length>=1&&img[0].naturalWidth>=1280,
               originalImageCount:img.length-1,
               loadedOriginal:img.length===2&&img[1].naturalWidth>=640,
               theme:document.body.dataset.editorial, evidenceLinks:links.length};
    }""", category)


def _contact_sheet(shots: list[Path], out: Path):
    from PIL import ImageOps
    width = 380
    sample_height = 1110
    pad = 18
    header = 65
    canvas = Image.new("RGB", ((width+pad)*5+pad, sample_height+header+2*pad), "#e9eff1")
    draw = ImageDraw.Draw(canvas)
    font = None
    for name in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                 "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"):
        try:
            font = ImageFont.truetype(name, 21)
            break
        except OSError:
            pass
    for i, path in enumerate(shots):
        with Image.open(path) as img:
            image = img.convert("RGB")
            image = ImageOps.fit(image, (width, sample_height), method=Image.Resampling.LANCZOS,
                                 centering=(0.5, 0.0))
        left = pad + i * (width+pad)
        canvas.paste(image, (left, pad+header))
        draw.text((left+8, pad+12), f"{i+1}. {DISPLAY_NAMES[i]}", fill="#1d3c45", font=font)
    canvas.save(out, optimize=True)


def generate(outdir: Path) -> dict:
    if outdir.exists():
        raise ValueError("gallery output exists; refusing overwrite")
    outdir.mkdir(parents=True)
    data = _json(ROOT / "benchmarks" / "cases.json")
    articles = {case["case_id"]: case for case in data["cases"]}
    editorial = _json(ROOT / "examples" / "gallery5_copy.json")["entries"]
    original_manifest = _json(ROOT / "examples" / "gallery5_original_sources.json")
    original_sources = {item["category"]: item for item in original_manifest["sources"]}
    if set(original_sources) != set(engine.studio.PROFILE_IDS):
        raise ValueError("every category requires one real original asset")
    if set(CASE_IDS) - (articles.keys() & editorial.keys()):
        raise ValueError("missing frozen topic or human-edited copy")
    artroot = outdir / "articles"
    artroot.mkdir()
    shotroot = outdir / "screenshots"
    shotroot.mkdir()
    originalroot = outdir / "approved-originals"
    article_shots: list[Path] = []
    results = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for cid in CASE_IDS:
                raw = articles[cid]
                infile = outdir / f"{cid}-input.json"
                infile.write_text(json.dumps(raw, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
                # Fetch and preserve the user-approved actual photo or original
                # source screenshot BEFORE choosing an image backend. Fail if missing.
                photo, asset_info = import_approved_original(
                    original_sources[raw["category"]], originalroot)
                # Real production engine uses original photo inside a CSS cover.
                # The cover renderer uses sync Playwright. Invoke the real engine
                # in a distinct process so its browser is not nested inside this
                # gallery screenshot browser's async event loop.
                process = subprocess.run(
                    [sys.executable, "-m", "editorial_studio.engine",
                     "--case", str(infile), "--output-root", str(artroot),
                     "--text-backend", "fixture", "--image-backend", "original",
                     "--image-file", str(photo), "--rubric", "v2"],
                    capture_output=True, text=True, timeout=180, check=False,
                )
                if process.returncode:
                    raise RuntimeError("article engine failed: " + process.stderr.strip())
                bundle = Path(json.loads(process.stdout)["path"])
                normalized = engine.load_case(raw)
                profile, _ = engine.studio.choose(raw["topic"], explicit=raw["category"])
                written = curated_article(normalized, editorial[cid], profile)
                (bundle / "article-structure.json").write_bytes(engine.canonical(written) + b"\n")
                original_copy = bundle / "visual-assets" / ("original" + photo.suffix)
                if original_copy.read_bytes() != photo.read_bytes():
                    raise AssertionError("real original photo byte integrity failed")
                # The original is the primary visual, not merely a tiny element
                # inside the CSS cover. It appears as a separate full-width image.
                src_label = asset_info["credit"]
                src_page = asset_info["page_url"]
                display_label = (
                    "원본 공식 화면" if asset_info["kind"] == "original_site_screenshot"
                    else "원본 실사진"
                )
                original_note = (
                    "\n\n![{}]({})\n\n원본 이미지 출처: [{}]({})\n"
                    .format(display_label, "visual-assets/" + original_copy.name,
                            src_label, src_page)
                )
                draft_text = engine.render_markdown(
                    normalized, written, True, original_photo=True)
                draft_text = draft_text.replace(
                    "\n## " + written["sections"][0]["heading"], original_note +
                    "\n## " + written["sections"][0]["heading"], 1)
                (bundle / "draft.md").write_text(draft_text, encoding="utf-8")
                document = engine.render_html(
                    normalized, written, True, original_photo=True)
                original_tag = (
                    '<figure class="source-photo">'
                    '<img src="visual-assets/{}" alt="{}" loading="eager">'
                    '<figcaption class="meta">실제 원본 자료 · {}'
                    ' / <a href="{}" rel="noopener noreferrer">원본 출처</a></figcaption>'
                    '</figure>'
                ).format(
                    original_copy.name, display_label,
                    src_label.replace("&","&amp;").replace("<","&lt;"),
                    src_page.replace("&","&amp;").replace('"',"&quot;"))
                document = document.replace(
                    '<figure><img src=\'cover.png\'', 
                    '<figure><img src=\'cover.png\'', 1)
                # Show the genuine image immediately after the composed header.
                figure_end = document.find("</figure>")
                if figure_end < 0:
                    raise AssertionError("original-first article has no CSS cover")
                document = (document[:figure_end + len("</figure>")] +
                            original_tag + document[figure_end + len("</figure>"):])
                (bundle / "preview.html").write_text(document, encoding="utf-8")
                (bundle / "asset-provenance.json").write_text(
                    json.dumps(asset_info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                meta = _json(bundle / "result.json")
                meta["score"] = quality.assess(bundle, "v2")
                meta["gallery_curated_text"] = True
                meta["gallery_source_case_id"] = cid
                meta["original_source"] = asset_info
                meta["image_backend"] = "original"
                (bundle / "result.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2)+"\n", encoding="utf-8"
                )
                if meta["visual_qa"]["status"] != "PASS" or meta["state"] != "IMAGE_READY_FOR_REVIEW":
                    raise AssertionError("production cover failed QA for " + cid)
                # Screenshot the actual engine preview; data URI avoids file://
                # restrictions and prevents internet/external resource requests.
                cover_b64 = base64.b64encode((bundle/"cover.png").read_bytes()).decode("ascii")
                original_b64 = base64.b64encode(original_copy.read_bytes()).decode("ascii")
                mime = ("image/jpeg" if photo.suffix == ".jpg" else
                        "image/png" if photo.suffix == ".png" else "image/webp")
                page_html = (bundle/"preview.html").read_text(encoding="utf-8").replace(
                    "src='cover.png'", "src='data:image/png;base64,"+cover_b64+"'"
                ).replace('src="visual-assets/' + original_copy.name + '"',
                          'src="data:' + mime + ';base64,' + original_b64 + '"')
                page = browser.new_page(viewport={"width":430,"height":920}, device_scale_factor=2)
                requests = []
                console_errors = []
                page.on("request", lambda req: requests.append(req.url) if req.url.startswith(("http:","https:")) else None)
                page.on("pageerror", lambda exc: console_errors.append(str(exc)))
                page.set_content(page_html, wait_until="load")
                page.evaluate("document.fonts.ready")
                qa = screenshot_qa(page, normalized["category"])
                if requests:
                    qa["issues"].append("external-network-requests")
                if console_errors:
                    qa["issues"].append("browser-javascript-errors")
                if qa["issues"]:
                    raise AssertionError(cid + ": " + ", ".join(qa["issues"]))
                mobile_shot = shotroot / (normalized["category"]+".png")
                page.screenshot(path=str(mobile_shot), full_page=True, animations="disabled")
                page.set_viewport_size({"width":1280,"height":900})
                desktop_qa = screenshot_qa(page, normalized["category"])
                if desktop_qa["issues"]:
                    raise AssertionError(cid + " desktop: " + str(desktop_qa["issues"]))
                wide_shot = shotroot / (normalized["category"]+"_desktop.png")
                page.screenshot(path=str(wide_shot), full_page=True, animations="disabled")
                page.close()
                article_shots.append(mobile_shot)
                results.append({
                    "id":cid, "topic":normalized["topic"], "category":normalized["category"],
                    "title":written["title"], "article_folder":str(bundle.relative_to(outdir)),
                    "mobile_png":str(mobile_shot.relative_to(outdir)),
                    "desktop_png":str(wide_shot.relative_to(outdir)),
                    "mobile_sha256":hashlib.sha256(mobile_shot.read_bytes()).hexdigest(),
                    "sections":len(written["sections"]), "source_count":len(normalized["sources"]),
                    "draft_chars":len((bundle/"draft.md").read_text(encoding="utf-8")),
                    "cover_created_by":"HTML/CSS composite of actual approved original image",
                    "original_photo_included":True,
                    "original_source_kind":asset_info["kind"],
                    "original_source_sha256":asset_info["downloaded_sha256"],
                    "original_file_url":asset_info["original_file_url"],
                    "original_page_url":asset_info["page_url"],
                    "style_profile_sha256":meta["profile_sha256"],
                    "visual_qa":meta["visual_qa"]["status"],
                    "browser_qa":qa, "review":"PENDING",
                    "published":False,
                })
        finally:
            browser.close()
    _contact_sheet(article_shots, outdir/"five_articles_contact_sheet.png")
    report = {"status":"PASS","items":results,"browser":"Chromium/Playwright",
              "styles":5,"original_assets_included":5,
              "original_photos_supplied":4,
              "historical_official_screenshots_included":1,
              "output_contains_real_engine_cover":True,
              "paid_calls":0,"naver_published":False,
              "human_source_review":"PENDING"}
    (outdir/"proof.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir",type=Path,required=True)
    args = parser.parse_args()
    proof=generate(args.out_dir)
    print(json.dumps({"status":proof["status"],"article_count":len(proof["items"]),
                      "output":str(args.out_dir),"published":False},ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

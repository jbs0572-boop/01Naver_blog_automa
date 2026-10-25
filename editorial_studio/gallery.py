"""Produce five *actual engine outputs* and mobile Chromium screenshot evidence.

Uses the second topic from each frozen category pair, with separately authored
narrative text and real HTML/CSS cover assets. No paid API or external publishing.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

from . import engine, quality

ROOT = Path(__file__).resolve().parent
CASE_IDS = ("life_health-2", "news_policy-2", "culture_people-2", "real_estate-2", "economy-2")
DISPLAY_NAMES = ("생활·건강", "뉴스·정책", "영화·인물", "부동산", "경제")


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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
       if (img.length!==1||img.some(i=>!i.complete||i.naturalWidth<1280)) issues.push('cover-not-loaded');
       if (links.length<5||badLinks.length) issues.push('evidence-links-invalid');
       if (document.body.dataset.editorial!==expected) issues.push('wrong-category-theme');
       for (const [index, node] of [title,...document.querySelectorAll('h2')].entries()) {
         if (!node) continue;
         const rect=node.getBoundingClientRect();
         if (rect.left < -2 || rect.right > innerWidth+2) issues.push('heading-overflow-'+index);
       }
       return {issues, actualWidth:innerWidth, scrollWidth:document.documentElement.scrollWidth,
               fullHeight:document.documentElement.scrollHeight, sections:sections.length,
               loadedCover:img.length===1&&img[0].naturalWidth>0,
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
    if set(CASE_IDS) - (articles.keys() & editorial.keys()):
        raise ValueError("missing frozen topic or human-edited copy")
    artroot = outdir / "articles"
    artroot.mkdir()
    shotroot = outdir / "screenshots"
    shotroot.mkdir()
    article_shots: list[Path] = []
    results = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for cid in CASE_IDS:
                raw = articles[cid]
                infile = outdir / f"{cid}-input.json"
                infile.write_text(json.dumps(raw, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
                # Real production engine creates the cover through HTML/CSS/Chromium.
                bundle = engine.run(infile, artroot, text_backend="fixture",
                                    image_backend="html", rubric="v2")
                normalized = engine.load_case(raw)
                profile, _ = engine.studio.choose(raw["topic"], explicit=raw["category"])
                written = curated_article(normalized, editorial[cid], profile)
                (bundle / "article-structure.json").write_bytes(engine.canonical(written) + b"\n")
                (bundle / "draft.md").write_text(engine.render_markdown(normalized, written, True),
                                                  encoding="utf-8")
                (bundle / "preview.html").write_text(engine.render_html(normalized, written, True),
                                                    encoding="utf-8")
                meta = _json(bundle / "result.json")
                meta["score"] = quality.assess(bundle, "v2")
                meta["gallery_curated_text"] = True
                meta["gallery_source_case_id"] = cid
                (bundle / "result.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2)+"\n", encoding="utf-8"
                )
                if meta["visual_qa"]["status"] != "PASS" or meta["state"] != "IMAGE_READY_FOR_REVIEW":
                    raise AssertionError("production cover failed QA for " + cid)
                # Screenshot the actual engine preview; data URI avoids file://
                # restrictions and prevents internet/external resource requests.
                cover_b64 = base64.b64encode((bundle/"cover.png").read_bytes()).decode("ascii")
                page_html = (bundle/"preview.html").read_text(encoding="utf-8").replace(
                    "src='cover.png'", "src='data:image/png;base64,"+cover_b64+"'"
                )
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
                    "cover_created_by":"actual HTML/CSS production capture engine",
                    "original_photo_included":False,
                    "style_profile_sha256":meta["profile_sha256"],
                    "visual_qa":meta["visual_qa"]["status"],
                    "browser_qa":qa, "review":"PENDING",
                    "published":False,
                })
        finally:
            browser.close()
    _contact_sheet(article_shots, outdir/"five_articles_contact_sheet.png")
    report = {"status":"PASS","items":results,"browser":"Chromium/Playwright",
              "styles":5,"original_photos_supplied":0,
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

"""Original-photo-first covers, or deterministic HTML/CSS infographics.

Visual QA uses Chromium screenshots, DOM bounds, image decoding and three
bounded typography corrections. This never checks whether source facts are true.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageStat
from playwright.sync_api import sync_playwright

PALETTE = {
    "life_health": ("#f8f6ed", "#173e36", "#087e76", "#e7f1e9"),
    "news_policy": ("#f0f3f8", "#182e4d", "#45648f", "#dfe7f3"),
    "culture_people": ("#141f2b", "#f1e8db", "#d6b17b", "#283949"),
    "real_estate": ("#f7f5ef", "#273e35", "#42806c", "#e1ece4"),
    "economy": ("#0b2638", "#f3f8f9", "#e4ba73", "#163d50"),
}


class VisualError(ValueError):
    pass


def _checked_text(value: object, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise VisualError(f"{field}: require 1..{limit} characters")
    if any(ord(c) < 32 for c in value):
        raise VisualError(f"{field}: control character")
    return value.strip()


def prepare_spec(raw: object) -> dict:
    if not isinstance(raw, dict) or raw.get("category") not in PALETTE:
        raise VisualError("one of five category IDs required")
    bullets = raw.get("bullets")
    if not isinstance(bullets, list) or not 2 <= len(bullets) <= 4:
        raise VisualError("2..4 factual/step bullets required")
    result = {
        "category": raw["category"],
        "headline": _checked_text(raw.get("headline"), "headline", 100),
        "deck": _checked_text(raw.get("deck"), "deck", 240),
        "eyebrow": _checked_text(raw.get("eyebrow"), "eyebrow", 60),
        "source_caption": _checked_text(raw.get("source_caption"), "source_caption", 160),
        "bullets": [_checked_text(v, "bullet", 155) for v in bullets],
    }
    if raw.get("source_photo") is not None:
        # Original rights were confirmed by the user for supplied/approved assets.
        # No per-image reapproval loop; retain byte-identical provenance instead.
        result["source_photo"] = _checked_text(raw["source_photo"], "source_photo", 1200)
    return result


def _original(spec: dict, folder: Path) -> tuple[str | None, dict]:
    source = spec.get("source_photo")
    if not source:
        return None, {"kind": "html_css_infographic", "photo_used": False}
    source_path = Path(source).expanduser()
    if not source_path.is_file() or source_path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        raise VisualError("supplied original must exist as PNG/JPEG/WEBP")
    if source_path.stat().st_size > 18_000_000:
        raise VisualError("original file exceeds 18MB; do not degrade automatically")
    try:
        with Image.open(source_path) as image:
            image.verify()
        with Image.open(source_path) as image:
            width, height, image_format = *image.size, image.format
    except OSError as error:
        raise VisualError("original photo is not decodable") from error
    if image_format not in ("PNG", "JPEG", "WEBP") or width < 640 or height < 400 or width * height > 26_000_000:
        raise VisualError("original photo size/format unsupported")
    target = folder / ("original" + source_path.suffix.lower())
    shutil.copyfile(source_path, target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != hashlib.sha256(source_path.read_bytes()).hexdigest():
        raise VisualError("original copied bytes changed")
    suffix = source_path.suffix.lower()
    mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/" + suffix.lstrip(".")
    encoded = base64.b64encode(target.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}", {
        "kind": "user_verified_original", "photo_used": True,
        "source_sha256": digest, "source_file": target.name,
        "width": width, "height": height, "byte_identical": True,
    }


def html_slide(spec: dict, photo_data: str | None, attempt: int) -> str:
    if attempt not in (1, 2, 3):
        raise VisualError("maximum three versions per slide")
    bg, ink, accent, panel = PALETTE[spec["category"]]
    esc = lambda s: html.escape(s, quote=True)
    bullets = "".join(
        f'<li data-check><span class="idx">{n:02}</span><span>{esc(t)}</span></li>'
        for n, t in enumerate(spec["bullets"], 1)
    )
    if photo_data:
        media = f'<div class="photo"><img src="{photo_data}" alt="사용자가 사용권을 확인한 원본 사진"></div>'
    else:
        stages = "".join(
            f'<div class="stage"><span class="num">{i:02}</span><p data-check>{esc(t)}</p></div>'
            for i, t in enumerate(spec["bullets"], 1)
        )
        media = '<div class="diagram"><span class="diagram-top">KEY POINTS · FLOW</span>' + stages + '</div>'
    pt = (84, 70, 56)[attempt - 1]
    title = (66, 59, 52)[attempt - 1]
    body = (26, 23, 21)[attempt - 1]
    gap = (25, 17, 11)[attempt - 1]
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; base-uri 'none'">
<style>
:root{{--bg:{bg};--ink:{ink};--accent:{accent};--panel:{panel};--pad:{pt}px;--title:{title}px;--body:{body}px;--gap:{gap}px}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:'Noto Sans CJK KR','NanumGothic',sans-serif}}
#poster{{width:1536px;height:1024px;position:relative;overflow:hidden;padding:var(--pad);background:var(--bg)}}
header{{display:flex;justify-content:space-between;gap:15px;align-items:center;border-bottom:1px solid var(--accent);padding-bottom:20px}}
header span:first-child{{color:var(--accent);font-size:17px;font-weight:800;letter-spacing:.14em}}
header span:last-child{{opacity:.7;font-size:16px;white-space:nowrap}}
main{{display:grid;grid-template-columns:1.15fr .85fr;align-items:stretch;gap:38px;padding-top:48px}}
.copy{{min-width:0;display:flex;flex-direction:column;gap:var(--gap)}}
h1{{margin:0;font-size:var(--title);line-height:1.25;letter-spacing:-.055em;word-break:keep-all;overflow-wrap:anywhere}}
p.deck{{margin:0;font-size:var(--body);line-height:1.55;opacity:.83;word-break:keep-all}}
ul{{margin:8px 0 0;padding:0;list-style:none;display:grid;gap:var(--gap)}}
li{{display:flex;gap:18px;font-size:var(--body);line-height:1.48;word-break:keep-all;overflow-wrap:anywhere}}
.idx{{flex:0 0 auto;color:var(--accent);font-size:.8em;font-weight:800}}
.media{{height:520px;background:var(--panel);border-radius:20px;overflow:hidden}}
.photo{{height:100%;display:flex;align-items:center;justify-content:center;padding:14px}}
.photo img{{width:100%;height:100%;object-fit:contain;display:block}}
.diagram{{height:100%;display:flex;flex-direction:column;justify-content:center;gap:16px;padding:70px 24px 26px;position:relative}}
.diagram-top{{position:absolute;top:27px;left:24px;color:var(--accent);font-weight:800;letter-spacing:.12em;font-size:15px}}
.stage{{min-height:100px;display:flex;align-items:center;gap:14px;padding:12px 16px;border:1px solid var(--accent);border-radius:12px;background:var(--bg)}}
.num{{font-size:32px;font-weight:900;color:var(--accent)}}
.stage p{{margin:0;font-size:22px;line-height:1.4;font-weight:650;word-break:keep-all;overflow-wrap:anywhere}}
footer{{position:absolute;bottom:var(--pad);left:var(--pad);right:var(--pad);font-size:17px;opacity:.7;border-top:1px solid var(--accent);padding-top:14px}}
.life_health .stage{{border-radius:22px;background:color-mix(in srgb,var(--bg) 65%,var(--panel))}}
.news_policy .stage{{border-radius:4px;border-left:7px solid var(--accent);background:var(--bg)}}
.news_policy h1{{letter-spacing:-.035em}}
.culture_people h1{{font-family:'Noto Serif CJK KR',serif;font-weight:700;letter-spacing:-.045em}}
.culture_people .media{{border-radius:0}}
.culture_people .stage{{border:0;border-bottom:1px solid var(--accent);border-radius:0;background:transparent}}
.real_estate .stage{{border-left:8px solid var(--accent);border-radius:5px}}
.real_estate .media{{border-radius:8px}}
.economy .stage{{border-radius:0;border:0;border-bottom:2px solid var(--accent);background:transparent}}
.economy h1{{font-weight:850}}
@media(max-width:600px){{#poster{{width:100%;height:auto;min-height:0;overflow:visible;padding:22px}}header{{padding-bottom:11px}}header span:first-child,header span:last-child{{font-size:10px}}main{{display:flex;flex-direction:column;gap:17px;padding-top:23px}}.copy{{gap:13px}}h1{{font-size:30px}}p.deck{{font-size:16px}}ul{{gap:11px}}li{{font-size:15px;gap:10px}}.media{{height:auto;min-height:250px}}.photo{{height:250px}}.diagram{{height:auto;min-height:250px;padding:43px 13px 14px;gap:7px}}.diagram-top{{top:11px;left:13px;font-size:9px}}.stage{{min-height:60px;padding:7px;gap:8px}}.stage p{{font-size:12px}}.num{{font-size:19px}}footer{{position:static;margin-top:22px;font-size:12px}}}}
</style></head><body><article id="poster" class="{spec["category"]}">
<header><span data-check>{esc(spec["eyebrow"])}</span><span data-check>EDITORIAL · SOURCE FIRST</span></header>
<main><div class="copy"><h1 data-check>{esc(spec["headline"])}</h1><p class="deck" data-check>{esc(spec["deck"])}</p><ul>{bullets}</ul></div><div class="media">{media}</div></main>
<footer data-check>{esc(spec["source_caption"])}</footer></article></body></html>'''


DOM_INSPECTION = r'''() => {
  const canvas=document.querySelector("#poster"),outer=canvas.getBoundingClientRect();
  const issues=[], mobile=innerWidth<600;
  if(document.documentElement.scrollWidth>innerWidth+2)issues.push("horizontal-scroll");
  const els=[...document.querySelectorAll("[data-check]")];
  for(let i=0;i<els.length;i++){
    const el=els[i],r=el.getBoundingClientRect(),style=getComputedStyle(el);
    if(!r.width||!r.height)issues.push("text-"+i+":empty");
    if(r.left<outer.left-2||r.top<outer.top-2||r.right>outer.right+2||r.bottom>outer.bottom+2)issues.push("text-"+i+":outside-slide");
    if((style.overflowX==="hidden"&&el.scrollWidth>el.clientWidth+3)||
       (style.overflowY==="hidden"&&el.scrollHeight>el.clientHeight+3))issues.push("text-"+i+":clipped");
    // A glyph inside a hidden parent can be clipped without leaving the slide.
    let ancestor=el.parentElement;
    while(ancestor&&ancestor!==canvas.parentElement){
      const computed=getComputedStyle(ancestor),ar=ancestor.getBoundingClientRect();
      if((computed.overflowX==="hidden"||computed.overflowX==="clip")&&r.right>ar.right+2)
        issues.push("text-"+i+":hidden-parent-x");
      if((computed.overflowY==="hidden"||computed.overflowY==="clip")&&r.bottom>ar.bottom+2)
        issues.push("text-"+i+":hidden-parent-y");
      ancestor=ancestor.parentElement;
    }
    const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);let text;
    while((text=walker.nextNode())){
      if(!text.textContent.trim())continue;
      const range=document.createRange();range.selectNodeContents(text);
      for(const t of range.getClientRects())
        if(t.left<outer.left-2||t.top<outer.top-2||t.right>outer.right+2||t.bottom>outer.bottom+2)
          issues.push("text-"+i+":glyph-outside");
    }
  }
  for(const img of document.images)if(!img.complete||!img.naturalWidth)issues.push("image-load-failed");
  if(!mobile&&document.querySelector(".copy").getBoundingClientRect().bottom>
     document.querySelector("footer").getBoundingClientRect().top-18)issues.push("body-overlaps-footer");
  return {issues:[...new Set(issues)],width:outer.width,height:outer.height,mobile};
}'''


def _pixels(path: Path) -> list[str]:
    try:
        with Image.open(path) as image:
            if image.width < 390 or image.height < 300:
                return ["insufficient-resolution"]
            sd = ImageStat.Stat(image.convert("RGB").resize((100, 100))).stddev
            return [] if sum(sd) / 3 >= 10 else ["blank-or-low-contrast"]
    except OSError:
        return ["invalid-screenshot-image"]


@dataclass
class RenderResult:
    status: str
    category: str
    attempts: int
    cover: str | None
    mobile: str | None
    issues: list[str]
    provenance: dict


def render(raw: dict, folder: Path, attempts: int = 3) -> RenderResult:
    spec = prepare_spec(raw)
    if attempts not in (1, 2, 3):
        raise VisualError("retry budget is 1..3")
    if folder.exists():
        raise VisualError("refuse to overwrite existing visual output")
    folder.mkdir(parents=True)
    photo_data, provenance = _original(spec, folder)
    with sync_playwright() as playwright:
        executable = os.environ.get("CHROMIUM_PATH") or shutil.which("chromium") or shutil.which("google-chrome")
        browser = playwright.chromium.launch(headless=True, executable_path=executable,
                                              args=["--no-sandbox"] if executable else [])
        try:
            page = browser.new_page(viewport={"width": 1536, "height": 1024}, device_scale_factor=1)
            page.route("**/*", lambda route: route.abort()
                       if route.request.url.startswith(("https:", "http:")) else route.continue_())
            all_issues: list[str] = []
            for count in range(1, attempts + 1):
                markup = html_slide(spec, photo_data, count)
                (folder / f"attempt-{count}.html").write_text(markup, encoding="utf-8")
                page.set_viewport_size({"width": 1536, "height": 1024})
                page.set_content(markup, wait_until="load")
                page.evaluate("document.fonts.ready")
                wide = page.evaluate(DOM_INSPECTION)
                wide_png = folder / f"attempt-{count}.png"
                page.locator("#poster").screenshot(path=str(wide_png), animations="disabled", scale="css")
                page.set_viewport_size({"width": 390, "height": 844})
                page.set_content(markup, wait_until="load")
                page.evaluate("document.fonts.ready")
                narrow = page.evaluate(DOM_INSPECTION)
                mobile_png = folder / f"attempt-{count}-mobile.png"
                page.screenshot(path=str(mobile_png), full_page=True, animations="disabled", scale="css")
                issues = (["desktop:" + s for s in wide["issues"] + _pixels(wide_png)] +
                          ["mobile:" + s for s in narrow["issues"] + _pixels(mobile_png)])
                (folder / f"attempt-{count}-qa.json").write_text(
                    json.dumps({"desktop": wide, "mobile": narrow, "issues": issues},
                               ensure_ascii=False, indent=2), encoding="utf-8")
                all_issues.extend(issues)
                if not issues:
                    shutil.copyfile(wide_png, folder / "cover.png")
                    shutil.copyfile(mobile_png, folder / "mobile.png")
                    (folder / "slide.html").write_text(markup, encoding="utf-8")
                    result = RenderResult("PASS", spec["category"], count, "cover.png", "mobile.png", [], provenance)
                    break
            else:
                result = RenderResult("QA_FAIL", spec["category"], attempts, None, None, all_issues, provenance)
        finally:
            browser.close()
    (folder / "result.json").write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
    return result


__all__ = ["VisualError", "prepare_spec", "html_slide", "render", "RenderResult"]

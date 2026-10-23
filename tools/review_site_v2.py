"""Accessible, static and network-free HTML preview for human review.

Never publishes, authenticates, fetches evidence URLs, or grants approval.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from tools.blog_v2 import ContentError, _read_json, validate_pack, verify_bundle


_CSS = """
:root { color-scheme: light; --ink: #182b38; --muted: #566979;
  --line: #dce6eb; --paper: #fff; --background: #f1f6f8; --accent: #0d6f77 }
* { box-sizing: border-box }
body { background: var(--background); color: var(--ink);
  font: 16px/1.75 system-ui, -apple-system, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
  margin: 0; padding: 0 }
header { background: #173743; color: #fff; padding: 38px max(22px, calc((100vw - 980px)/2)); }
header p { color: #cae1e6; margin: 5px 0 0 }
h1 { max-width: 980px; margin: 0; line-height: 1.35; font-size: clamp(1.65rem, 4vw, 2.4rem) }
h2 { margin: 0 0 16px; line-height: 1.35 }
main { max-width: 980px; margin: 0 auto; padding: 24px 20px 50px }
article { background: var(--paper); border: 1px solid var(--line);
  border-radius: 14px; padding: clamp(18px, 3vw, 30px); margin-bottom: 20px }
.callout { border-left: 5px solid var(--accent); background: #e5f3f3; padding: 15px 18px;
  border-radius: 0 8px 8px 0; }
.metadata { color: var(--muted); margin: 0 0 12px }
.claim { border-top: 1px solid var(--line); padding: 17px 0 8px }
.claim:first-child { border-top: 0 }
.claim p { margin: 8px 0 }
.tags { font-size: .82rem; color: var(--muted) }
a { color: #065d75; overflow-wrap: anywhere }
a:focus-visible { outline: 3px solid #eea33d; outline-offset: 3px }
header a { color: #e8ffff }
ul { padding-left: 20px }
footer { text-align:center; font-size:.86rem; color: var(--muted); padding: 15px 20px 40px }
@media(max-width: 600px) {
  header { padding: 24px 18px }
  main { padding: 15px 12px 40px }
  article { border-radius: 9px }
}
@media(prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important } }
"""


def _e(value: str) -> str:
    return html.escape(value, quote=True)


def render_preview(pack: dict, run_id: str) -> str:
    """Render safely escaped evidence; output contains no scripts or external assets."""
    groups: dict[str, list[dict]] = {}
    for claim in pack["claims"]:
        groups.setdefault(claim["section"], []).append(claim)
    source_by_id = {source["id"]: source for source in pack["sources"]}
    parts = [
        "<!doctype html>", '<html lang="ko"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; img-src &#39;none&#39;; base-uri &#39;none&#39;; form-action &#39;none&#39;">',
        f"<title>{_e(pack['title'])} — 검수용 미리보기</title>", f"<style>{_CSS}</style></head><body>",
        f'<header><h1>{_e(pack["title"])}</h1><p>검수 전용 · 기준일 {_e(pack["as_of"])}</p></header>',
        "<main>", '<article aria-label="검수 상태">',
        '<p class="callout"><strong>미검수 초안입니다.</strong> 문서에 적힌 주장은 입력 자료를 구조화한 것이며 '
        '원문 대조, 최신성 확인, 저작권 점검과 별도 사람 승인이 필요합니다. 자동으로 게시되지 않습니다.</p>',
        f'<p class="metadata">독자: {_e(pack["audience"])} · 실행: {_e(run_id)}</p>',
        f"<p>{_e(pack['angle'])}</p></article>",
    ]
    for section, claims in groups.items():
        parts.append(f'<article aria-label="{_e(section)}"><h2>{_e(section)}</h2>')
        for claim in claims:
            src = source_by_id[claim["source_id"]]
            parts.append('<div class="claim">')
            parts.append(f"<p>{_e(claim['text'])}</p>")
            parts.append(f'<p class="tags">근거 등급: {_e(claim["evidence_level"])} · '
                         f'출처: <a href="{_e(src["url"])}" target="_blank" rel="noopener noreferrer">'
                         f'{_e(src["title"])}</a> ({_e(src["published_at"])})</p>')
            parts.append("</div>")
        parts.append("</article>")
    parts.append('<article><h2>편집자가 확인할 사항</h2><ul>')
    parts.extend(f"<li>{_e(item)}</li>" for item in (
        "모든 주장과 공식 원문을 직접 대조했는가?",
        "기준일 이후 변경된 정보가 있음을 명시했는가?",
        "이미지 및 링크의 사용 권리와 광고 표시를 확인했는가?",
        "독자에게 단순 요약 이상으로 유용한 정보를 제공하는가?",
    ))
    parts.extend(["</ul></article></main>",
                  "<footer>로컬 HTML 검수 미리보기 · 온라인 저장 또는 네이버 발행 기능 없음</footer>",
                  "</body></html>"])
    return "\n".join(parts) + "\n"


def write_preview(bundle: Path) -> Path:
    verified = verify_bundle(bundle)
    source = validate_pack(_read_json(bundle / "evidence.json"))
    payload = render_preview(source, verified["run_id"]).encode("utf-8")
    path = bundle / "preview.html"
    if path.is_symlink():
        raise ContentError("preview symlink is forbidden")
    if path.exists():
        if path.read_bytes() != payload:
            raise ContentError("preview differs from verified evidence; refusing overwrite")
        return path
    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError as error:
        raise ContentError("preview already exists") from error
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps({"preview": str(write_preview(args.folder)), "published": False},
                         ensure_ascii=False))
        return 0
    except (ContentError, OSError) as error:
        parser.exit(2, message=f"review-site-v2: {error}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

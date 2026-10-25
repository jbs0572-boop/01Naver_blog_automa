"""Five independently themed Korean article pages; keeps evidence text unchanged.

This is a presentation-only transform used by the actual editorial engine.
"""
from __future__ import annotations

from html import escape

THEMES = {
    "life_health": ("LIVING / HEALTH", "생활·건강", "실생활 질문을 원리와 실천 순서로",
                    "#f3f4eb", "#ffffff", "#203b32", "#347d67", "#eaf2e9", "#d7e7db"),
    "news_policy": ("PUBLIC BRIEF / FACTS", "뉴스·정책·이슈", "확정 사실과 적용 시점을 구분하는 브리핑",
                    "#e9eef4", "#ffffff", "#172e47", "#365d83", "#e9f0f6", "#cddce9"),
    "culture_people": ("THE CINEMA FILES", "영화·드라마·인물", "작품의 기록과 해석을 구분하는 영화 저널",
                       "#0e1721", "#172836", "#f0e9de", "#d6b17b", "#243c4b", "#425262"),
    "real_estate": ("PROPERTY / CHECKLIST", "부동산", "놓치기 쉬운 거래 조건부터 확인하는 점검표",
                    "#ecf0e8", "#ffffff", "#263b33", "#3f7760", "#ecf3ec", "#d9e4d9"),
    "economy": ("MACRO / DATA LITERACY", "경제", "지표·단위·기간을 분리해 읽는 분석",
                "#0d2435", "#153349", "#eaf1f2", "#dfb978", "#203e52", "#38556a"),
}

COMMON = """
:root { --bg: BG; --paper: PAPER; --ink: INK; --accent: ACCENT; --soft: SOFT; --border: BORDER; }
*{box-sizing:border-box}
body{background:var(--bg)!important;color:var(--ink)!important;font-family:'Noto Sans CJK KR','NanumGothic',sans-serif!important}
main{max-width:780px!important;background:var(--paper)!important;padding:clamp(22px,4vw,54px)!important;min-height:100vh!important}
.masthead{display:flex;gap:14px;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);padding-bottom:14px;margin-bottom:23px}
.masthead strong{font-size:.69rem;letter-spacing:.14em;color:var(--accent);font-weight:850}
.masthead span{font-size:.71rem;opacity:.75;white-space:nowrap}
.styletag{font-size:.74rem;letter-spacing:.04em;color:var(--accent);font-weight:700;margin:0 0 7px}
.subtitle{background:var(--soft);border-left:3px solid var(--accent);padding:13px 15px;line-height:1.65;font-size:.89rem;margin:23px 0 22px}
h1{color:var(--ink)!important;font-size:clamp(1.8rem,4.5vw,2.35rem)!important;line-height:1.42!important;margin:11px 0 15px!important;word-break:keep-all}
h2{color:var(--ink);line-height:1.48!important;word-break:keep-all}
section>p{margin:12px 0;line-height:1.85;word-break:keep-all}
section p.meta{color:var(--accent);opacity:.8;font-size:.77rem}
a{color:var(--accent);overflow-wrap:anywhere}
.state{background:var(--soft)!important;color:var(--ink)!important;border:1px solid var(--border);font-size:.72rem!important;line-height:1.5!important}
.reference{margin-top:30px;padding-top:19px!important;border-color:var(--border)!important}
.reference h2{font-size:1.03rem!important}
.reference li{font-size:.9rem;line-height:1.5;padding:4px 0;overflow-wrap:anywhere}
figure{margin:23px 0}
figure img{width:100%;max-width:100%;height:auto;object-fit:contain;margin:0!important}
figure figcaption{margin:12px 3px 0!important;line-height:1.5}
@media(max-width:600px){main{padding:24px 21px!important}.masthead{margin-bottom:20px}h1{font-size:1.87rem!important}h2{font-size:1.13rem}section p{font-size:.975rem}.masthead span{font-size:.65rem}}
"""

SPECIFIC = {
    "life_health": """
main{border-radius:26px;box-shadow:0 8px 40px #304d3012}
section{border-radius:18px;background:#f1f7ef;padding:20px 19px;margin:19px 0;border:1px solid #dbead9}
section:nth-of-type(even){background:#fcfaf3}
h2{border-left:0!important;font-size:1.17rem!important}h2::before{content:'●';font-size:10px;color:var(--accent);margin-right:10px}
figure img{border-radius:19px!important}
""",
    "news_policy": """
main{border-top:8px solid #1b3956;box-shadow:0 8px 30px #253f5011}
section{padding:18px 0 23px;border-bottom:1px solid #cad7e5}
h2{border-left:4px solid #426d95!important;margin-top:16px!important}
figure img{border-radius:4px!important}.meta{font-size:.78rem}
figure{background:#f0f4f7;padding:10px;border:1px solid #d8e2ea}
""",
    "culture_people": """
main{border:1px solid #354657;box-shadow:0 20px 50px #0004}
h1{font-family:'Noto Serif CJK KR','NanumMyeongjo',serif!important;letter-spacing:-.04em;font-weight:650}
section{padding:10px 0 19px;border-bottom:1px solid #3a4e5c}
h2{border-left:0!important;font-family:'Noto Serif CJK KR','NanumMyeongjo',serif;color:#d6b17b!important;font-size:1.22rem!important}
h2::before{content:'—';margin-right:9px}
figure img{border-radius:0!important}.reference{border-color:#475868!important}
.meta{color:#c2c9c5!important}a{color:#d8c199!important}
""",
    "real_estate": """
main{box-shadow:0 10px 36px #203d2916}
section{background:#f4f7f2;border:1px solid #d7e5d7;border-left:5px solid #4b806a;padding:18px 17px;margin:15px 0}
h2{border:0!important;margin-top:6px!important;font-size:1.11rem!important}
h2::before{content:'□';color:#3d795e;margin-right:9px}
figure{border:1px solid #dde7da;padding:8px}figure img{border-radius:4px!important}
""",
    "economy": """
main{box-shadow:0 20px 54px #0003}h1{font-weight:850;letter-spacing:-.05em}
section{padding:12px 0 20px;border-bottom:1px solid #436072}
h2{border-left:0!important;color:#dfb978!important;font-size:1.14rem!important}
h2::before{content:'◇';margin-right:8px}
figure img{border-radius:3px!important}
.meta{color:#a8c3cb!important}a{color:#a9deed!important}
.reference{border-color:#42657a!important}
""",
}


def themed_html(document: str, category: str) -> str:
    """Add presentation only: no factual body text or source URLs are changed."""
    if category not in THEMES:
        raise ValueError("unsupported article category")
    if "</style>" not in document or "<body><main>" not in document:
        raise ValueError("unexpected preview template")
    label, name, subtitle, bg, paper, ink, accent, soft, border = THEMES[category]
    css = COMMON
    for key, value in {
        "BG": bg, "PAPER": paper, "INK": ink, "ACCENT": accent,
        "SOFT": soft, "BORDER": border,
    }.items():
        css = css.replace(key, value)
    css += SPECIFIC[category]
    head = (
        '<body data-editorial="' + escape(category, quote=True) + '"><main>'
        '<div class="masthead"><strong>' + escape(label) + '</strong>'
        '<span>원문 확인 필요 · 검수 전</span></div>'
        '<div class="styletag">' + escape(name) + '</div>'
    )
    return (document.replace("</style>", css + "</style>", 1)
                    .replace("<body><main>", head, 1)
                    .replace("</h1><p class='meta'>",
                             '</h1><div class="subtitle">' + escape(subtitle)
                             + "</div><p class='meta'>", 1))

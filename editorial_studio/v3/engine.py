"""V3 offline editorial production: three genuinely different images per article.

Input is an authenticated prior review *artifact ZIP* with human-curated claims,
not real-time AI writing. No Naver/Notion writes and no paid models.
"""
from __future__ import annotations

import argparse
import base64
import mimetypes
import hashlib
import html
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
from PIL import Image, ImageOps, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

CATEGORIES = ('life_health','news_policy','culture_people','real_estate','economy')
LABELS = ('생활·건강','뉴스·정책','영화·인물','부동산','경제')
THEMES = {
'life_health': dict(bg='#F6F8F2',paper='#FFFFFF',ink='#213D33',accent='#237C66',wash='#E7F1E9',gold='#E7BE6B'),
'news_policy': dict(bg='#F1F5F8',paper='#FFFFFF',ink='#1C344E',accent='#AC513E',wash='#E9EDF3',gold='#E9C07D'),
'culture_people': dict(bg='#12212B',paper='#1C303A',ink='#F7EBDD',accent='#D2B17C',wash='#2C4350',gold='#E4C98D'),
'real_estate': dict(bg='#F2F6F2',paper='#FFFFFF',ink='#25433A',accent='#477F6B',wash='#E4EDE5',gold='#E8C27D'),
'economy': dict(bg='#102B3E',paper='#1B3B50',ink='#F2F7F7',accent='#E2B573',wash='#29566B',gold='#F6D4A0'),
}
MIN_SCORE = 95
SOURCE_FILE = 'sourcepack.json'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def esc(t: object) -> str:
    return html.escape(str(t), quote=True)


def _fonts(size: int):
    for p in ('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
              '/usr/share/fonts/truetype/nanum/NanumGothic.ttf'):
        try: return ImageFont.truetype(p, size)
        except OSError: pass
    return ImageFont.load_default()


def _extract_source(archive: Path, target: Path) -> None:
    """Only copy paths from an expected export; reject symlinks/path escapes."""
    with zipfile.ZipFile(archive) as zin:
        members = zin.infolist()
        if len(members) > 350: raise ValueError('archive too large')
        # Do not let many individually valid members expand to gigabytes.
        # Check central-directory sizes before extracting any files.
        total_expanded = sum(m.file_size for m in members if not m.is_dir())
        if total_expanded > 512_000_000:
            raise ValueError('archive total uncompressed size exceeds limit')
        for m in members:
            if (not m.is_dir() and m.file_size > 1_000_000
                    and m.file_size > max(m.compress_size, 1) * 300):
                raise ValueError('archive suspicious compression ratio')
        for member in members:
            relative = Path(member.filename)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('archive contains unsafe path')
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('archive contains symlink')
            if member.is_dir(): continue
            if not (str(relative).startswith('approved-originals/') or str(relative).startswith('articles/')):
                continue
            if member.file_size > 24_000_000:
                raise ValueError('archive member exceeds limit')
            dest = target / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zin.open(member) as origin, dest.open('xb') as out:
                shutil.copyfileobj(origin, out)


def _source_category(root: Path, category: str) -> tuple[Path, dict, dict, dict]:
    matches=list(root.glob(f'articles/{category}-*/{SOURCE_FILE}'))
    if len(matches) != 1:
        raise ValueError(f'{category}: missing unique frozen source pack')
    spec=matches[0]
    pack=json.loads(spec.read_text(encoding='utf8'))
    article=json.loads((spec.parent/'article-structure.json').read_text(encoding='utf8'))
    # A frozen article cannot silently switch to a changed or different style.
    from editorial_studio import studio
    lock=json.loads((spec.parent/'style-lock.json').read_text(encoding='utf8'))
    if lock.get('category')!=category or lock.get('profile_sha256')!=studio.profiles()[category]['sha256']:
        raise ValueError('frozen style profile SHA-256 mismatch')
    # A ZIP is untrusted even when it carries a matching style lock.
    # Reapply the source-pack and article contracts before citations, images,
    # or a quality score can be produced. In particular, duplicate fact/source
    # IDs must never be silently overwritten by dict comprehensions.
    from editorial_studio import engine as evidence_engine
    pack = evidence_engine.load_case(pack)
    article = evidence_engine.validate_article(article, pack, studio.profiles()[category])
    meta=list((root/'approved-originals').glob(category+'.*'))
    photos=[p for p in meta if p.suffix.lower() in ('.jpg','.jpeg','.png','.webp')]
    if len(photos)!=1:raise ValueError(f'{category}: actual original missing')
    provenance=json.loads((root/'approved-originals'/f'{category}.source.json').read_text(encoding='utf8'))
    if sha(photos[0])!=provenance['downloaded_sha256']:
        raise ValueError('original source hash changed')
    # A matching filename and SHA-256 alone do not establish that the bytes
    # are an actual usable photo. Reject forged, tiny or giant images early.
    try:
        with Image.open(photos[0]) as img:
            width, height = img.size
            if (img.format not in ('JPEG', 'PNG', 'WEBP')
                    or width < 640 or height < 400
                    or width * height > 26_000_000):
                raise ValueError('original image dimensions/format invalid')
            img.verify()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError('original image is not a decodable photo') from exc
    if pack['category']!=category or len(pack['facts'])<5 or len(article['sections'])!=5:
        raise ValueError('category/source structure invalid')
    return photos[0],pack,article,provenance


CARD_CSS = '''
:root{--bg:BG;--paper:PAPER;--ink:INK;--accent:ACCENT;--wash:WASH;--gold:GOLD}
*{box-sizing:border-box}
body{margin:0;color:var(--ink);background:var(--bg);font-family:'Noto Sans CJK KR','Apple SD Gothic Neo',sans-serif}
#canvas{width:1280px;height:720px;position:relative;overflow:hidden;background:var(--bg);padding:56px 66px}
.label{font-size:19px;color:var(--accent);font-weight:750;letter-spacing:.08em}
h1{font-size:48px;letter-spacing:-.045em;line-height:1.25;margin:19px 0 30px;word-break:keep-all}
.content{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;height:420px}
.card{min-width:0;padding:30px 27px;display:flex;flex-direction:column;justify-content:flex-start;gap:18px;position:relative;border:1px solid var(--accent);background:var(--paper);border-radius:16px}
.card .count{font-size:19px;font-weight:800;color:var(--accent)}
.card .text{font-size:27px;line-height:1.43;font-weight:690;letter-spacing:-.04em;overflow-wrap:anywhere;word-break:keep-all}
.source{font-size:16px;color:var(--accent);opacity:.9;position:absolute;left:66px;bottom:33px;right:66px;padding-top:9px;border-top:1px solid var(--accent)}
#canvas[data-role=check] .content{grid-template-columns:1fr;gap:14px}
#canvas[data-role=check] .card{padding:17px 22px;min-height:110px;flex-direction:row;align-items:center;border-radius:6px;border-left:8px solid var(--accent);gap:28px}
#canvas[data-role=check] .card .text{font-size:25px;line-height:1.3}
#canvas[data-category=life_health] .card{border-radius:23px;background:var(--wash)}
#canvas[data-category=news_policy] .card{border-radius:2px;border-left:7px solid var(--accent)}
#canvas[data-category=culture_people] h1{font-family:'Noto Serif CJK KR',serif}
#canvas[data-category=culture_people] .card{border-radius:0;background:var(--wash)}
#canvas[data-category=real_estate] .card{border-radius:4px;background:var(--wash)}
#canvas[data-category=economy] .card{border-radius:2px;border-top:5px solid var(--gold)}
@media(max-width:850px){
 #canvas{width:800px;height:1200px;padding:48px 45px}
 .label{font-size:22px}h1{font-size:39px;margin:18px 0 23px}
 .content{height:880px;grid-template-columns:1fr;gap:14px}
 .card{padding:19px 25px;flex-direction:row;align-items:center;gap:23px;border-radius:10px}
 .card .text{font-size:25px!important;line-height:1.39}
 .card .count{min-width:33px}
 #canvas[data-role=check] .content{gap:15px}
 #canvas[data-role=check] .card{min-height:0;padding:19px 24px}
 .source{left:45px;right:45px;bottom:22px;font-size:16px}
}
'''


def card_html(category: str, role: str, title: str, entries: list[str], caption: str, shrink: int) -> str:
    if len(entries)!=3:raise ValueError('card requires exactly three independently useful entries')
    theme=THEMES[category]
    css=CARD_CSS
    for k,v in theme.items():css=css.replace(k.upper(),v)
    css+=f'\n.card .text{{font-size:{27-2*shrink}px!important}} h1{{font-size:{48-3*shrink}px!important}}'
    cards=''.join(f'<div class="card"><div class="count">{i:02}</div><div class="text" data-card-text>{esc(text)}</div></div>' for i,text in enumerate(entries,1))
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>{css}</style></head>
<body><article id="canvas" data-role="{role}" data-category="{category}"><div class="label">{esc('한눈에 보는 핵심' if role=='facts' else '점검할 순서')}</div><h1>{esc(title)}</h1><div class="content">{cards}</div><div class="source">{esc(caption)}</div></article></body></html>'''


QA_JS = r'''() => {
 const issues=[];
 const canvas=document.querySelector('#canvas').getBoundingClientRect();
 const footer=document.querySelector('.source').getBoundingClientRect();
 const title=document.querySelector('h1').getBoundingClientRect();
 if(title.bottom>footer.top-20)issues.push('headline-overlap');
 if(title.left<canvas.left-2||title.right>canvas.right+2)issues.push('headline-out-of-bounds');
 if(footer.left<canvas.left-2||footer.right>canvas.right+2||footer.bottom>canvas.bottom+2)
   issues.push('footer-out-of-bounds');
 for(const [i,el] of [...document.querySelectorAll('[data-card-text]')].entries()){
   const card=el.closest('.card').getBoundingClientRect(),r=el.getBoundingClientRect();
   if(r.top<card.top+4 || r.bottom>card.bottom-5 || r.right>card.right-5)
     issues.push('card-'+i+'-out-of-bounds');
   const range=document.createRange();range.selectNodeContents(el);
   for(const rect of range.getClientRects()){
     if(rect.top<card.top+4||rect.bottom>card.bottom-5||rect.right>card.right-4)
        issues.push('card-'+i+'-glyph-clipped');
   }
   if(el.scrollHeight>el.clientHeight+2)issues.push('card-'+i+'-scroll-clipped');
 }
 const cards=[...document.querySelectorAll('.card')].map(x=>x.getBoundingClientRect());
 if(cards.some(r=>r.bottom>footer.top-8))issues.push('card-overlaps-footer');
 if(cards.some(r=>r.left<canvas.left-2||r.right>canvas.right+2))
   issues.push('card-out-of-canvas');
 if(document.documentElement.scrollWidth>innerWidth+2)issues.push('horizontal-overflow');
 return {issues:[...new Set(issues)], cards:cards.length};
}'''


def create_visual(page, folder: Path, category: str, role: str, title: str, items: list[str], source: str) -> tuple[Path,dict]:
    diagnostics=[]
    for attempt in range(3):
        markup=card_html(category,role,title,items,source,attempt)
        page.set_viewport_size({'width':1280,'height':720})
        page.set_content(markup,wait_until='load')
        page.evaluate('document.fonts.ready')
        errors=page.evaluate(QA_JS)
        path=folder/f'{role}-{attempt+1}.png'
        page.locator('#canvas').screenshot(path=str(path), animations='disabled')
        diagnostics.append({'attempt':attempt+1,'issues':errors['issues']})
        if not errors['issues']:
            with Image.open(path) as img:
                if img.size!=(1280,720): raise ValueError('incorrect screenshot size')
            final=folder/f'{role}.png'
            shutil.copyfile(path,final)
            (folder/f'{role}.html').write_text(markup,encoding='utf8')
            page.set_viewport_size({'width':800,'height':1200})
            page.set_content(markup,wait_until='load')
            page.evaluate('document.fonts.ready')
            mobile_err=page.evaluate(QA_JS)
            if mobile_err['issues']:
                diagnostics.append({'mobile_issues':mobile_err['issues']})
                final.unlink(missing_ok=True)
                continue
            mobile_png=folder/f'{role}-mobile.png'
            page.locator('#canvas').screenshot(path=str(mobile_png),animations='disabled')
            with Image.open(mobile_png) as image:
                if image.size!=(800,1200):raise ValueError('incorrect mobile infographic pixel size')
            return final,{'status':'PASS','attempts':attempt+1,'history':diagnostics,
                          'mobile_image':mobile_png.name}
    raise ValueError(f'{category}/{role}: unacceptable card layout {diagnostics}')


def verify_source_case(category: str, pack: dict, editorial: dict) -> None:
    """Do not join fixed headlines/checklists with a different reviewed topic.

    The archived source snapshot and the five human-edited V3 topics are a
    matched set, not interchangeable inputs just because the category matches.
    """
    def compact(value: object) -> str:
        return ' '.join(value.split()) if isinstance(value, str) else ''

    if (compact(pack.get('topic')) != compact(editorial.get('source_topic'))
            or not compact(editorial.get('source_topic'))):
        raise ValueError(f'{category}: source topic mismatch in reviewed archive')
    if pack.get('as_of') != editorial.get('source_as_of'):
        raise ValueError(f'{category}: source reference date mismatch in reviewed archive')


def paragraphs(article:dict) -> list[str]:
    output=[]
    for section in article['sections']:
        body=section['body'].strip()
        chunks=[v.strip() for v in body.split('\n') if v.strip()]
        if not chunks:raise ValueError('empty paragraph')
        if len(chunks)==1:chunks=[body]
        output.append(' '.join(chunks))
    return output


def create_article(category:str,pack:dict,article:dict,src_name:str,
                   facts_png:str,check_png:str,hero_label:str,editor:dict,
                   provenance:dict|None=None)->str:
    theme=THEMES[category]
    title=editor['title']
    if not pack['topic'].split()[0] in title:raise ValueError('topic omitted from title')
    # Evidence links in a reviewed archive are still untrusted URLs.
    # Every rendered citation must be an actual HTTPS origin, never javascript:.
    from urllib.parse import urlsplit
    for source in pack['sources']:
        candidate = source.get('url', '')
        if not isinstance(candidate, str):
            raise ValueError('source URL must be HTTPS')
        parsed = urlsplit(candidate)
        if (parsed.scheme != 'https' or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443)):
            raise ValueError('source URL must be HTTPS')
    sources={item['id']:item for item in pack['sources']}
    facts={item['id']:item for item in pack['facts']}
    referenced=set()
    for index, section in enumerate(article['sections']):
        ids=section.get('fact_ids')
        if not isinstance(ids,list) or not ids:
            raise ValueError('each section must have at least one source-backed fact ID')
        if any(fid not in facts or facts[fid]['source_id'] not in sources
               for fid in ids):
            raise ValueError('article section has unknown source/fact ID')
        if section.get('role')!=index or any(facts[fid]['role']!=index for fid in ids):
            raise ValueError('source fact role must match the article section')
        referenced.update(ids)
    if referenced!=set(facts):
        raise ValueError('not every curated fact was used by the article')
    paras=paragraphs(article)
    css=f'''
*{{box-sizing:border-box}}body{{margin:0;background:{theme['bg']};color:{theme['ink']};font-family:'Noto Sans CJK KR','Apple SD Gothic Neo',sans-serif;line-height:1.8}}
main{{max-width:790px;margin:0 auto;padding:clamp(20px,4vw,48px);background:{theme['paper']};min-height:100vh}}
.mast{{letter-spacing:.12em;font-weight:800;font-size:13px;color:{theme['accent']};border-bottom:1px solid {theme['accent']};padding-bottom:15px}}
h1{{font-size:clamp(28px,4vw,39px);line-height:1.34;letter-spacing:-.045em;word-break:keep-all;margin:30px 0 16px}}.lead{{font-size:18px;line-height:1.8}}h2{{margin:40px 0 15px;font-size:25px;line-height:1.4;color:{theme['ink']};word-break:keep-all}}
article p{{font-size:17px;line-height:1.85;margin:16px 0;word-break:keep-all}}a{{color:{theme['accent']};overflow-wrap:anywhere}}
.meta{{font-size:13px;opacity:.8}}figure{{margin:27px 0 34px}}figure img{{width:100%;height:auto;display:block;object-fit:contain;background:{theme['wash']}}}
figcaption{{font-size:12px;color:{theme['accent']};padding-top:9px;line-height:1.55}}
section{{border-top:1px solid {theme['accent']};padding-top:6px;margin-top:35px}}blockquote{{background:{theme['wash']};padding:13px 20px;margin:25px 0;border-left:4px solid {theme['accent']};font-size:15px}}
@media(max-width:450px){{main{{padding:23px 18px}}h1{{font-size:27px}}h2{{font-size:21px}}.lead{{font-size:16px}}article p{{font-size:15px}}}}
'''
    style={
        'life_health': 'section{background:'+theme['wash']+';padding:17px;border-radius:14px;border:0;margin-bottom:15px} figure img{border-radius:16px}',
        'news_policy': 'h2{border-left:5px solid '+theme['accent']+';padding-left:13px}figure img{border-radius:3px}main{border-top:8px solid '+theme['ink']+'}',
        'culture_people': "h1,h2{font-family:'Noto Serif CJK KR',serif}section{border-color:#354D5C}figure img{border-radius:0}",
        'real_estate': 'section{border:1px solid '+theme['accent']+';padding:20px;border-left-width:6px}h2{margin-top:10px}',
        'economy': 'h2{color:'+theme['accent']+';border-bottom:1px solid '+theme['accent']+';padding-bottom:10px}figure img{border-radius:0}',
    }[category]
    css+=style
    def figure(src,cap,role):
        mobile=src.replace('.png','-mobile.png') if role!='context-photo' else None
        source_tag=(f'<source media="(max-width:620px)" srcset="images/{esc(mobile)}">' if mobile else '')
        return (f'<figure data-image-role="{role}"><picture>{source_tag}'
                f'<img src="images/{esc(src)}" alt="{esc(cap)}"></picture>'
                f'<figcaption>{esc(cap)}</figcaption></figure>')
    def section_source_links(section):
        ids=list(dict.fromkeys(facts[fid]['source_id'] for fid in section['fact_ids']))
        return ' · '.join(f'<a href="{esc(sources[sid]["url"])}" target="_blank" rel="noopener noreferrer">{esc(sources[sid]["title"])}</a>' for sid in ids)
    output=[f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>{css}</style></head>',
            f'<body data-editorial="{category}"><main><div class="mast">{esc(editor["masthead"])} · 검수 전 원고</div>',
            f'<h1>{esc(title)}</h1><p class="meta">기준일 {esc(pack["as_of"])} · 원본 사진/그래픽 3개 · 네이버 미게시</p><p class="lead">{esc(editor["intro"])}</p>',
            figure(src_name,hero_label,'context-photo')]
    if provenance:
        credit=provenance.get('credit') or '원본 제공자'
        page_url=provenance.get('page_url') or provenance.get('original_file_url')
        license_text=provenance.get('license')
        if page_url:
            from urllib.parse import urlsplit
            if urlsplit(page_url).scheme!='https':
                raise ValueError('photo attribution URL must be HTTPS')
            line=f'원본 출처: <a href="{esc(page_url)}" target="_blank" rel="noopener noreferrer">{esc(credit)}</a>'
        else:
            line=f'원본 출처: {esc(credit)}'
        if license_text:
            line+=' · '+esc(license_text)
        output.append('<p class="meta photo-credit">'+line+'</p>')
    for n,sect in enumerate(article['sections']):
        output.extend([f'<section><h2>{esc(editor["headings"][n])}</h2><p>{esc(paras[n])}</p><p class="meta">근거: {section_source_links(sect)}</p></section>'])
        if n==0:output.append(figure(facts_png,'핵심 정보 비교','information-graphic'))
        if n==2:output.append(figure(check_png,'실행 전 점검','action-checklist'))
    all_source_titles=' · '.join(esc(item['title']) for item in pack['sources'])
    output.extend([f'<blockquote>자료 확인 범위: {all_source_titles} · 모든 판단은 기준일과 실제 조건을 다시 확인하세요.</blockquote>',
                   '<p class="meta">사용된 원본 사진은 별도 파일로 보존되어 있습니다. 출처 확인 및 발행 검수는 아직 완료되지 않았습니다.</p>',
                   '</main></body></html>'])
    return '\n'.join(output)


def inspect_article(page,category:str)->dict:
    return page.evaluate(r'''(category) => {
      const roles=[...document.querySelectorAll('figure[data-image-role]')];
      const ids=roles.map(x=>x.dataset.imageRole),img=roles.map(x=>x.querySelector('img'));
      const issues=[];
      if(roles.length<3||new Set(ids).size<3)issues.push('missing-image-roles');
      if(img.some(x=>!x.complete||!x.naturalWidth))issues.push('image-not-loaded');
      if(img.some(x=>!x.alt.trim()))issues.push('image-alt-missing');
      if(roles.some(x=>!x.querySelector('figcaption')?.textContent.trim()))issues.push('caption-missing');
      if(document.documentElement.scrollWidth>innerWidth+2)issues.push('horizontal-scroll');
      if(document.querySelectorAll('main section').length!==5)issues.push('section-count');
      if(document.body.dataset.editorial!==category)issues.push('wrong-theme');
      for(const [i,h] of [...document.querySelectorAll('h1,h2')].entries()){
        const r=h.getBoundingClientRect();
        if(r.left < -2||r.right>innerWidth+2)issues.push('heading-overflow-'+i);
      }
      return {issues,roles:ids,imageCount:roles.length,width:innerWidth,fullHeight:document.documentElement.scrollHeight};
    }''',category)


def score_result(category:str,pack:dict, article:dict, metadata:dict, mobile:dict, desktop:dict)->dict:
    # Constrained mechanical proxy, never a human/editorial/SEO success claim.
    checks={
     'source_links': bool(pack.get('sources')) and len(pack['facts'])>=5,
     'style_lock': category in THEMES and len(article['sections'])==5,
     'original_integrity': metadata['original_same_sha256'],
     'role_diversity': len(set(mobile['roles']))>=3,
     'browser_qa': not mobile['issues'] and not desktop['issues'],
     'repetition': len(set(s['body'] for s in article['sections']))==5,
    }
    weights={'source_links':22,'style_lock':15,'original_integrity':18,'role_diversity':18,'browser_qa':20,'repetition':7}
    points=sum(weights[k] for k,v in checks.items() if v)
    # Even perfect structural validation requires a separate human/source check.
    points=min(points,98)
    return {'score':points,'goal':MIN_SCORE,'status':'REVIEW_CANDIDATE' if points>=MIN_SCORE and all(checks.values()) else 'FAIL',
            'checks':checks,'weights':weights,'human_review':'PENDING','published':False,'organic_traffic_measured':False}


def contact_sheet(images:list[Path],output:Path):
    width=328;height=1150;pad=15
    frame=Image.new('RGB',((width+pad)*5+pad,height+90),'#E9F0F1')
    draw=ImageDraw.Draw(frame)
    for i,p in enumerate(images):
        with Image.open(p) as img:
            sample=ImageOps.fit(img.convert('RGB'),(width,height),centering=(0.5,0.0))
        left=pad+i*(width+pad)
        frame.paste(sample,(left,70))
        draw.text((left+6,19),f'{i+1}. {LABELS[i]} / V3',fill='#1B3943',font=_fonts(21))
    frame.save(output,optimize=True)


def generate(source_archive:Path, out:Path)->dict:
    if out.exists():raise ValueError('refuse overwrite of existing build output')
    out.mkdir(parents=True)
    extract=out/'_source'
    extract.mkdir()
    _extract_source(source_archive,extract)
    assets=out/'images'
    assets.mkdir()
    editorial=json.loads((Path(__file__).parent/'cases.json').read_text(encoding='utf8'))
    by_id={r['category']:r for r in editorial['articles']}
    if set(by_id)!=set(CATEGORIES):raise ValueError('one case per category required')
    outputs=[];screens=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None, args=["--no-sandbox"] if shutil.which("chromium") else [])
        try:
            for cat in CATEGORIES:
                original,pack,article,provenance=_source_category(extract,cat)
                spec=by_id[cat]
                verify_source_case(cat, pack, spec)
                sub=out/cat
                sub.mkdir()
                imagefolder=sub/'images';imagefolder.mkdir()
                dest=imagefolder/('original'+original.suffix)
                shutil.copyfile(original,dest)
                original_same=sha(dest)==sha(original)==provenance['downloaded_sha256']
                if not original_same:raise ValueError('original image lost integrity')
                # Facts remain verbatim; visual items are drawn from stored evidence and headings.
                fact_entries=[x['text'] for x in pack['facts'][:3]]
                check_entries=spec['checklist']
                page=browser.new_page(viewport={'width':1280,'height':720})
                source_map={item['id']:item for item in pack['sources']}
                facts_ids=list(dict.fromkeys(item['source_id'] for item in pack['facts'][:3]))
                all_ids=list(dict.fromkeys(item['source_id'] for item in pack['facts']))
                if any(sid not in source_map for sid in all_ids):
                    raise ValueError('infographic source references missing')
                facts_source=' · '.join(source_map[sid]['title'] for sid in facts_ids)
                check_source='점검 항목 · '+' · '.join(source_map[sid]['title'] for sid in all_ids)
                facts_png,qa1=create_visual(page,imagefolder,cat,'facts',spec['fact_title'],fact_entries,facts_source)
                check_png,qa2=create_visual(page,imagefolder,cat,'check',spec['check_title'],check_entries,check_source)
                image_hashes={'original':sha(dest),'facts':sha(facts_png),'check':sha(check_png)}
                if len(set(image_hashes.values()))!=3:
                    raise ValueError('image role files are identical; three distinct visuals required')
                html_content=create_article(cat,pack,article,dest.name,facts_png.name,check_png.name,spec['photo_caption'],spec,provenance)
                (sub/'article.html').write_text(html_content,encoding='utf8')
                (sub/'draft.md').write_text('# '+spec['title']+'\n\n'+spec['intro']+'\n\n'+'\n\n'.join('## '+spec['headings'][i]+'\n'+s['body'] for i,s in enumerate(article['sections']))+'\n',encoding='utf8')
                # Browser sandbox blocks file://. Embed identical local image
                # bytes as data URLs, without fetching anything externally.
                markup=html_content
                for asset in (dest, facts_png, check_png, imagefolder/'facts-mobile.png',imagefolder/'check-mobile.png'):
                    mime=mimetypes.guess_type(asset.name)[0] or 'image/png'
                    uri='data:'+mime+';base64,'+base64.b64encode(asset.read_bytes()).decode('ascii')
                    markup=markup.replace('src="images/'+asset.name+'"', 'src="'+uri+'"').replace('srcset="images/'+asset.name+'"', 'srcset="'+uri+'"')
                page.set_content(markup,wait_until='load')
                page.set_viewport_size({'width':430,'height':900})
                page.wait_for_load_state('load')
                mobile=inspect_article(page,cat)
                if mobile['issues']:raise ValueError(f'{cat}: mobile error: {mobile["issues"]}')
                mobile_png=sub/'mobile.png'
                page.screenshot(path=str(mobile_png),full_page=True,animations='disabled')
                page.set_viewport_size({'width':1280,'height':900})
                desktop=inspect_article(page,cat)
                if desktop['issues']:raise ValueError(f'{cat}: desktop error: {desktop["issues"]}')
                desktop_png=sub/'desktop.png'
                page.screenshot(path=str(desktop_png),full_page=True,animations='disabled')
                page.close()
                row={'category':cat,'title':spec['title'],'article_dir':cat,'images':{
                  'original':dest.name,'facts':facts_png.name,'check':check_png.name},
                  'original_sha256':sha(dest),'original_same_sha256':original_same,
                  'image_sha256':image_hashes,
                  'original_credit':provenance['credit'],'image_captions':[spec['photo_caption'],'핵심 정보 비교','실행 전 점검'],
                  'diagram_qa':[qa1,qa2],'mobile_qa':mobile,'desktop_qa':desktop,
                  'screenshot_sha256':sha(mobile_png),'image_count':3,'published':False}
                row['evaluation']=score_result(cat,pack,article,row,mobile,desktop)
                (sub/'result.json').write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
                if row['evaluation']['status']!='REVIEW_CANDIDATE':raise ValueError(cat+' not at quality target')
                screens.append(mobile_png);outputs.append(row)
        finally:
            browser.close()
    contact_sheet(screens,out/'five_articles_contact_sheet.png')
    proof={'status':'PASS','threshold':MIN_SCORE,'categories':len(outputs),'images_total':sum(x['image_count'] for x in outputs),
           'screenshots_total':10,'published':False,'human_review':'PENDING',
           'all_browser_qa_passed':all(not x['mobile_qa']['issues'] and not x['desktop_qa']['issues'] for x in outputs),
           'results':outputs}
    (out/'proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return proof


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-archive',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    a=parser.parse_args(argv)
    try:
        info=generate(a.source_archive,a.out)
    except (ValueError,OSError,KeyError,zipfile.BadZipFile) as e:
        parser.exit(2,f'v3: {e}\n')
    print(json.dumps({'status':info['status'],'images':info['images_total'],'scores':[x['evaluation']['score'] for x in info['results']]},ensure_ascii=False))
    return 0

if __name__=='__main__':raise SystemExit(main())
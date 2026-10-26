# Blog Editorial V3 — 5 independent articles, 3 meaningful visuals each

**As of 2026-10-26 KST.** This is a review-only, deterministic renderer. It assembles previously researched source packs and reviewed draft bodies, **not a real-time AI writer or publisher**.

## Execution vs Markdown

- Machine code: `engine.py` — accepts a reviewed source artifact ZIP, verifies original image digests, checks five role-linked sections, generates original-photo + comparison card + checklist, captures via Playwright (desktop and mobile) and scores the recorded outputs.
- Human-maintained contracts: `INTEND.md` — pipeline boundaries and responsibilities; `IMAGE_ROLES.md` — three visual roles / short captions; `QUALITY_GATE.md` — 95/100 threshold and hard-stop conditions. Five existing `editorial_studio/profiles/*.md` remain the per-category voice/structure source of truth.
- `cases.json` — five distinct topics, editorial headings, infographic semantics and review headlines. Does not manufacture research claims.
- `tests/test_v3_engine.py` — fully offline synthetic image regression. A synthetic fixture verifies code paths **but never proves licensing or authentic source-photo suitability**.

## Run on previously verified authentic materials

Use an archive matching the earlier successful original-first editorial gallery artifact, with `articles/<category>-*/sourcepack.json`, `article-structure.json`, and `approved-originals/<category>.(jpg|png|webp)` with corresponding `<category>.source.json` containing the authentic `downloaded_sha256`.

```sh
python -m pip install 'Pillow>=12,<13' 'playwright>=1.50,<2'
python -m playwright install chromium
python -m editorial_studio.v3.engine \
  --source-archive real-reviewed-article-assets.zip \
  --out five-v3-articles
```

Outputs include each category's `article.html`, `draft.md`, `images/original.*`, `images/facts.png`, `images/check.png`, two mobile infographics, `mobile.png`, `desktop.png`, `result.json`; the root contains `five_articles_contact_sheet.png` and `proof.json`. The original photo file is preserved unchanged. A visual may be retried **up to 3 times**; failure blocks a passing build.

## Gate

Five different topics, 15 independently purposed images, 10 full-page screenshots and **score >=95 on each** with all QA flags clear. Because scoring is a deterministic production-quality *proxy* capped at 98, no score implies original document truth, professional aesthetic success, organic traffic, posting authorization or human review. The full repo still has a separate historical source-log test blocker from August 2026.

CI runs five category outputs with synthetic images to verify browser behavior without credentials; actual real-photo content requires the explicit verified ZIP input. No automatic Notion/Naver publishing is implemented.

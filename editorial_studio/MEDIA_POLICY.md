# Original-first media policy (2026-10-25)

## Principle

For material the user has identified as usage-authorized, prefer the actual original photograph or original still over fabricated AI art. This includes approved real people, film/TV stills, products, places, and real documents. Do not repeatedly request the same permission. Preserve a byte-identical local copy and SHA-256. Do not claim blanket licensing rights for new unrelated web images that were never identified as approved.

## Routing

1. User-provided or explicitly approved actual photo: use ORIGINAL. Preserve exact bytes. Overlay editorial information with HTML/CSS only when it helps; default CSS object-fit: contain to avoid unwanted cropping.
2. Comparison chart, process diagram, timeline, checklist, numerical graph: create precise HTML/CSS/SVG. Cite actual data; never draw invented values.
3. Mixed layout: original photo plus HTML/CSS information cards; maintain original file next to final composite screenshot.
4. Original not available and a purely conceptual illustration is justified: optionally use a generative image service, clearly label as generated, keep permissions/cost gates.

## Commands

Install local visual dependencies:

    python -m pip install 'Pillow>=12,<13' 'playwright>=1.50,<2'
    python -m playwright install chromium

Integrate with editorial engine (requires actual local photo path):

    python -m editorial_studio.engine --case my-case.json --output-root results --text-backend fixture --image-backend auto --image-file /path/to/approved-original.jpg

No photo exists: omit the image-file parameter. Auto mode then uses HTML/CSS graphics. To force choice, use --image-backend original or --image-backend html. Legacy --image-backend none, import, openai remain supported.

## QA

Chromium renders 1536x1024 desktop cover and a 390px mobile preview. Wait for fonts and loaded images. Check real DOM bounds, glyph overflow, content/footer collisions, horizontal overflow, image loading, PNG decoding, contrast and dimensions. On failure, alter padding/font size at most 3 times. Do not hide the problem by truncating text, changing facts, or fabricating evidence. Each attempt saves an HTML document, PNG and QA JSON.

If no attempt passes, return QA_FAIL and do not create a final cover.png. Passing structural checks is not proof of visual beauty, historical factual accuracy, image context, or search-traffic performance; those need editorial evaluation.

## Provenance and authority

An approved original is retained byte-for-byte, with source SHA-256 and review state. These captures are not Naver or Notion publication and do not confer Gate A/B permission. No external network requests or credentials are needed for capture. Use only approved local photos; new third-party URLs require normal source/license provenance recording.

## References

- https://playwright.dev/python/docs/screenshots
- https://playwright.dev/python/docs/api/class-locator

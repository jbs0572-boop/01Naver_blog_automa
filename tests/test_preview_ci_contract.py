"""Guard the v2 preview workflow against silently skipping browser evidence.

This is a structural safety assertion; GitHub Actions still launches a real
Chromium process and captures both screenshots on every PR and main push.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "blog-v2-preview.yml"


def test_v2_preview_ci_fails_closed_without_chromium_or_screenshots():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "python -m playwright install --with-deps chromium" in workflow
    assert "with sync_playwright() as playwright:" in workflow
    assert "browser = playwright.chromium.launch(headless=True)" in workflow
    assert 'page.screenshot(path=str(target), full_page=True' in workflow
    for name in ("mobile", "desktop"):
        assert f"test -s preview/screenshots/{name}.png" in workflow
    assert "No Chrome executable" not in workflow
    assert 'if [ -n "$BROWSER" ]' not in workflow


def test_v2_preview_ci_runs_on_main_without_external_writes():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "  push:\n    branches:\n      - main" in workflow
    assert "context.route(" in workflow
    assert 'route.request.url.startswith("file:") else route.abort()' in workflow
    assert 'test "$(find preview -name approval.json | wc -l)" -eq 0' in workflow
    assert 'test "$(find preview -name naver-copy.md | wc -l)" -eq 0' in workflow
    assert "contents: read" in workflow

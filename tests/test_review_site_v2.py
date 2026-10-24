"""Verify the static review UI is safe, deterministic and honest."""
from __future__ import annotations
import json

import pytest
from tools.blog_v2 import ContentError, build_bundle, verify_bundle
from tools.review_site_v2 import main, render_preview, write_preview


def source_pack():
    return {"schema_version": 2, "title": "차 한 잔", "audience": "독자",
            "angle": "입력 자료를 확인합니다.", "as_of": "2026-10-22",
            "sources": [{"id": "A", "title": "공식 문서", "url": "https://example.com/article",
                         "published_at": "2025-03-01"}],
            "claims": [
                {"id": "a", "source_id": "A", "section": "특징", "evidence_level": "official",
                 "text": "출처의 사실."},
                {"id": "b", "source_id": "A", "section": "활용", "evidence_level": "interpretation",
                 "text": "자료에 근거한 해석."},
            ]}


def prepare(tmp_path, pack=None):
    spec = tmp_path / "source.json"
    spec.write_text(json.dumps(source_pack() if pack is None else pack, ensure_ascii=False), encoding="utf-8")
    return build_bundle(spec, tmp_path / "output")


def test_preview_is_deterministic_and_never_approves_or_posts(tmp_path):
    bundle = prepare(tmp_path)
    output = write_preview(bundle)
    assert output == write_preview(bundle)
    html = output.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert '<html lang="ko">' in html
    assert 'aria-label="검수 상태"' in html
    assert 'name="viewport"' in html
    assert "Content-Security-Policy" in html
    assert "noopener noreferrer" in html
    assert "미검수 초안" in html
    assert "게시되지 않습니다" in html
    assert "<script" not in html.lower()
    assert not (bundle / "approval.json").exists()
    assert not (bundle / "naver-copy.md").exists()
    assert verify_bundle(bundle)["state"] == "review_required"


def test_preview_prefers_korean_font_without_external_font_download():
    html = render_preview(source_pack(), "RUN-test")
    assert "'Noto Sans CJK KR'" in html
    assert "@import" not in html
    assert "@font-face" not in html


def test_html_attribute_and_text_injection_are_escaped(tmp_path):
    pack = source_pack()
    pack["title"] = "<script>alert(1)</script>"
    pack["claims"][0]["text"] = '<img src=x onerror="alert(1)">'
    pack["sources"][0]["url"] = 'https://example.com/"onclick="alert(1)'
    bundle = prepare(tmp_path, pack)
    text = write_preview(bundle).read_text(encoding="utf-8")
    assert "<script>" not in text
    assert "<img" not in text
    assert "&lt;script&gt;" in text
    assert "&lt;img" in text
    assert '&quot;onclick=&quot;' in text


def test_tampering_blocks_html_export(tmp_path):
    bundle = prepare(tmp_path)
    (bundle / "draft.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ContentError, match="integrity"):
        write_preview(bundle)


def test_html_changes_are_not_silently_overwritten(tmp_path):
    bundle = prepare(tmp_path)
    path = write_preview(bundle)
    path.write_text("<html>changed</html>", encoding="utf-8")
    with pytest.raises(ContentError, match="refusing overwrite"):
        write_preview(bundle)


def test_preview_symlink_is_rejected(tmp_path):
    bundle = prepare(tmp_path)
    outside = tmp_path / "outside.html"
    outside.write_text("secret")
    (bundle / "preview.html").symlink_to(outside)
    with pytest.raises(ContentError, match="symlink"):
        write_preview(bundle)


def test_cli_returns_read_only_path(tmp_path, capsys):
    bundle = prepare(tmp_path)
    assert main(["--folder", str(bundle)]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["published"] is False
    assert response["preview"].endswith("/preview.html")


def test_preview_rehashes_second_evidence_snapshot(tmp_path, monkeypatch):
    from tools import review_site_v2 as preview_module
    folder = prepare(tmp_path)
    original = preview_module._read_json

    def swapped_after_verify(path):
        if path.name == "evidence.json":
            tampered = source_pack()
            tampered["claims"][0]["text"] = "Changed after verification"
            return tampered
        return original(path)

    monkeypatch.setattr(preview_module, "_read_json", swapped_after_verify)
    with pytest.raises(ContentError, match="preview evidence changed"):
        preview_module.write_preview(folder)
    assert not (folder / "preview.html").exists()

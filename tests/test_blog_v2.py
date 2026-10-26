"""Independent test suite for the new evidence-first offline pipeline."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from tools.blog_v2 import (
    ContentError, approve_bundle, build_bundle, canonical, digest, export_copy,
    iso_date, main, markdown_url, render_draft, safe_text, validate_pack, verified_url, verify_bundle,
)


def pack():
    return {
        "schema_version": 2,
        "title": "근거 기반 블로그 자동화",
        "audience": "개발자",
        "angle": "검증 가능한 글의 초안을 만듭니다.",
        "as_of": "2026-10-22",
        "sources": [
            {"id": "S1", "title": "공식 발표", "url": "https://developers.naver.com/notice/article/7527", "published_at": "2020-04-06"},
            {"id": "S2", "title": "API 안내", "url": "https://developers.naver.com/notice/article/32530", "published_at": "2026-06-29"},
        ],
        "claims": [
            {"id": "A", "source_id": "S1", "evidence_level": "official", "section": "글쓰기", "text": "외부 블로그 글쓰기 API가 종료됐습니다."},
            {"id": "B", "source_id": "S2", "evidence_level": "interpretation", "section": "검색", "text": "검색 API는 별도로 취급해야 합니다."},
        ],
    }


def fixture(tmp_path):
    input_path = tmp_path / "spec.json"
    input_path.write_text(json.dumps(pack(), ensure_ascii=False), encoding="utf-8")
    return input_path


def test_canonical_is_deterministic_and_sha256_is_stable():
    assert canonical({"b": 2, "a": 1}) == canonical({"a": 1, "b": 2})
    assert len(digest(b"content")) == 64


def test_build_verify_and_idempotent_copy(tmp_path):
    src = fixture(tmp_path)
    result = build_bundle(src, tmp_path / "build")
    assert result == build_bundle(src, tmp_path / "build")
    manifest = verify_bundle(result)
    assert manifest["state"] == "review_required"
    assert manifest["source_count"] == 2
    assert manifest["claim_count"] == 2
    assert manifest["external_writes"] is False
    text = (result / "draft.md").read_text(encoding="utf-8")
    assert "[자료 기반 해석]" in text
    assert "https://developers.naver.com/notice/article/7527" in text
    assert "## 편집자 점검" in text


def test_manual_approval_export_and_idempotence(tmp_path):
    folder = build_bundle(fixture(tmp_path), tmp_path / "out")
    digest_value = verify_bundle(folder)["draft_sha256"]
    with pytest.raises(ContentError, match="cannot read JSON"):
        export_copy(folder)
    with pytest.raises(ContentError, match="reviewed digest"):
        approve_bundle(folder, "editor", "wrong")
    approve_bundle(folder, "editor", digest_value)
    output = export_copy(folder)
    assert output.read_bytes() == (folder / "draft.md").read_bytes()
    assert export_copy(folder) == output
    with pytest.raises(ContentError, match="already approved"):
        approve_bundle(folder, "editor", digest_value)
    assert verify_bundle(folder)["draft_sha256"] == digest_value


def test_tampering_invalidates_both_approval_and_export(tmp_path):
    folder = build_bundle(fixture(tmp_path), tmp_path / "out")
    approve_bundle(folder, "editor", verify_bundle(folder)["draft_sha256"])
    (folder / "draft.md").write_text("malicious replacement", encoding="utf-8")
    with pytest.raises(ContentError, match="draft integrity"):
        export_copy(folder)
    with pytest.raises(ContentError, match="draft integrity"):
        build_bundle(fixture(tmp_path), tmp_path / "out")


def test_modified_evidence_is_blocked(tmp_path):
    folder = build_bundle(fixture(tmp_path), tmp_path / "out")
    (folder / "evidence.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ContentError):
        verify_bundle(folder)


@pytest.mark.parametrize(("field", "value"), [
    ("state", "published"),
    ("external_writes", True),
    ("external_writes", 0),
    ("as_of", "2026-10-21"),
    ("source_count", 100),
    ("source_count", True),
    ("claim_count", 1),
])
def test_manifest_forged_status_and_counts_fail_closed(tmp_path, field, value):
    folder = build_bundle(fixture(tmp_path), tmp_path / "out")
    path = folder / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest[field] = value
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ContentError, match="manifest review state or evidence metadata invalid"):
        verify_bundle(folder)


def test_fake_or_stale_approval_is_rejected(tmp_path):
    folder = build_bundle(fixture(tmp_path), tmp_path / "out")
    (folder / "approval.json").write_text('{"run_id":"wrong"}', encoding="utf-8")
    with pytest.raises(ContentError, match="approval record"):
        export_copy(folder)


def test_conflicting_existing_export_is_never_overwritten(tmp_path):
    folder = build_bundle(fixture(tmp_path), tmp_path / "out")
    approve_bundle(folder, "editor", verify_bundle(folder)["draft_sha256"])
    (folder / "naver-copy.md").write_text("manual changes", encoding="utf-8")
    with pytest.raises(ContentError, match="refuse overwrite"):
        export_copy(folder)


def test_symlink_substitution_is_detected(tmp_path):
    folder = build_bundle(fixture(tmp_path), tmp_path / "out")
    outside = tmp_path / "external.md"
    outside.write_text("secret", encoding="utf-8")
    (folder / "draft.md").unlink()
    (folder / "draft.md").symlink_to(outside)
    with pytest.raises(ContentError, match="symlinks"):
        verify_bundle(folder)


@pytest.mark.parametrize("url", [
    "http://naver.com/a", "javascript:alert(1)", "https://localhost/a",
    "https://127.0.0.1/foo", "https://[::1]/a", "https://example.local/a",
    "https://user:pw@example.com/x", "https://example.com:8443/a",
    "https://intranet/internal", "https://example.com:invalid/a",
])
def test_forbidden_evidence_urls(url):
    with pytest.raises(ContentError):
        verified_url(url)


@pytest.mark.parametrize("url", [
    "https://localhost./admin",
    "https://localhost.localdomain./admin",
    "https://router.home.arpa/admin",
    "https://router.home.arpa./admin",
    "https://home.arpa/admin",
    "https://@developers.naver.com/notice/article/7527",
    "https://:@developers.naver.com/notice/article/7527",
    "https://service.local./internal",
    "https://service.internal./internal",
    "https://review.test./internal",
    "https://docs.invalid./internal",
    "https://sub.localhost./internal",
    "https://127.0.0.1./internal",
    "https://0177.0.0.1/internal",
    "https://0x7f.1/internal",
    "https://0x7f.0x1/internal",
    "https://0x7f.0x/internal",
    "https://0x7f.0x./internal",
    "https://0x7f.0x0.0x0.0x1/internal",
    "https://example..com/invalid",
    "https://-example.com/invalid",
    "https://example-.com/invalid",
    "https://./invalid",
])
def test_disguised_local_and_invalid_evidence_hosts_are_rejected(url):
    with pytest.raises(ContentError):
        verified_url(url)

    edited = pack()
    edited["sources"][0]["url"] = url
    with pytest.raises(ContentError):
        validate_pack(edited)


def test_valid_public_fqdn_with_single_trailing_dot_remains_allowed():
    url = "https://developers.naver.com./notice/article/7527"
    assert verified_url(url) == url


def test_valid_public_https_url():
    assert verified_url("https://developers.naver.com/notice/article/7527") == "https://developers.naver.com/notice/article/7527"


@pytest.mark.parametrize("value", [None, "", " a\nb ", 100, "x" * 501])
def test_invalid_or_unsafe_plain_text(value):
    with pytest.raises(ContentError):
        safe_text(value, "test")


@pytest.mark.parametrize("stamp", ["2026-10-23T00:00:00", "2026-13-01", "2026-1-1", "invalid"])
def test_invalid_date(stamp):
    with pytest.raises(ContentError):
        iso_date(stamp, "as_of")


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(schema_version=1),
    lambda p: p.update(sources=[]),
    lambda p: p.update(claims=[]),
    lambda p: p["sources"][0].update(id="S2"),
    lambda p: p["sources"][0].update(url="http://bad.org"),
    lambda p: p["sources"][0].update(published_at="2026-10-23"),
    lambda p: p["claims"][0].update(evidence_level="unverified"),
    lambda p: p["claims"][0].update(source_id="UNKNOWN"),
    lambda p: p["claims"][0].update(id="B"),
    lambda p: p["claims"][0].update(text=p["claims"][1]["text"]),
    lambda p: p.update(title="invalid\nheading"),
    lambda p: p["claims"][0].update(section=""),
    lambda p: p["claims"][0].update(text=""),
])
def test_invalid_source_pack_fails_closed(mutate):
    item = pack()
    mutate(item)
    with pytest.raises(ContentError):
        validate_pack(item)


def test_untrusted_markdown_html_is_escaped_in_export():
    item = pack()
    item["claims"][0]["text"] = "<script>alert(1)</script> [link]"
    text = render_draft(validate_pack(item))
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "\\[link\\]" in text


def test_demo_cli_outputs_local_bundle_only(tmp_path, capsys):
    assert main(["demo", "--output-root", str(tmp_path / "demo")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "review_required"
    folder = Path(result["bundle"])
    assert folder.is_dir()
    assert not (folder / "approval.json").exists()
    assert not (folder / "naver-copy.md").exists()
    assert verify_bundle(folder)["external_writes"] is False


def test_cli_verify_and_build(tmp_path, capsys):
    src = fixture(tmp_path)
    assert main(["build", "--input", str(src), "--output-root", str(tmp_path / "out")]) == 0
    response = json.loads(capsys.readouterr().out)
    assert main(["verify", "--folder", response["bundle"]]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "review_required"


def test_cli_requires_explicit_approval_before_manual_export(tmp_path, capsys):
    src = fixture(tmp_path)
    folder = build_bundle(src, tmp_path / "out")
    with pytest.raises(SystemExit) as exc:
        main(["export", "--folder", str(folder)])
    assert exc.value.code == 2
    md = verify_bundle(folder)
    assert main(["approve", "--folder", str(folder), "--reviewer", "human", "--digest", md["draft_sha256"]]) == 0
    capsys.readouterr()
    assert main(["export", "--folder", str(folder)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["posted"] is False
    assert Path(result["manual_copy"]).exists()


def test_markdown_url_encodes_injection_delimiters():
    url = "https://example.com/)![tracking](https://attacker.example/pixel)"
    formatted = markdown_url(url)
    assert "%29" in formatted and "%5B" in formatted
    assert ")![" not in formatted
    item = pack()
    item["sources"][0]["url"] = url
    draft = render_draft(validate_pack(item))
    assert ")![tracking]" not in draft
    assert "%29%21%5Btracking%5D%28" in draft


def test_export_rehashes_exact_bytes_after_approval_lookup(tmp_path, monkeypatch):
    from tools import blog_v2
    folder = build_bundle(fixture(tmp_path), tmp_path / "out")
    approve_bundle(folder, "editor", verify_bundle(folder)["draft_sha256"])
    original = blog_v2._read_json

    def swap_draft_before_approval_read(path):
        if path.name == "approval.json":
            (folder / "draft.md").write_text("unreviewed new content", encoding="utf-8")
        return original(path)

    monkeypatch.setattr(blog_v2, "_read_json", swap_draft_before_approval_read)
    with pytest.raises(ContentError, match="draft changed during export"):
        export_copy(folder)
    assert not (folder / "naver-copy.md").exists()


def test_backslash_preceding_markdown_image_is_escaped():
    item = pack()
    item["claims"][0]["text"] = r"!\[tracking\](https://attacker.example/pixel)"
    text = render_draft(validate_pack(item))
    assert r"!\\\[" in text
    assert r"\]" in text

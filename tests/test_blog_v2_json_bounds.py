"""Untrusted JSON inputs must be bounded before the offline approval gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.blog_v2 import (
    MAX_BUNDLE_JSON_BYTES,
    ContentError,
    build_bundle,
    export_copy,
    verify_bundle,
)


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "blog-v2-sourcepack.json"


def _target(tmp_path: Path, boundary: str) -> tuple[Path, Path | None]:
    if boundary == "sourcepack":
        return tmp_path / "source.json", None
    bundle = build_bundle(EXAMPLE, tmp_path / "output")
    if boundary == "manifest":
        return bundle / "manifest.json", bundle
    return bundle / "approval.json", bundle


@pytest.mark.parametrize("boundary", ["sourcepack", "manifest", "approval"])
def test_oversized_json_cannot_exhaust_memory_or_pass_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    target, bundle = _target(tmp_path, boundary)
    # Sparse file: reproduces the old unbounded read without large disk use.
    with target.open("wb") as handle:
        handle.truncate(MAX_BUNDLE_JSON_BYTES + 1)

    original_read_text = Path.read_text

    def guarded_read_text(file: Path, *args: object, **kwargs: object) -> str:
        if file == target:
            raise AssertionError("unsafe unbounded JSON read_text used")
        return original_read_text(file, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    with pytest.raises(ContentError, match="JSON input exceeds"):
        if boundary == "sourcepack":
            build_bundle(target, tmp_path / "output")
        elif boundary == "manifest":
            assert bundle is not None
            verify_bundle(bundle)
        else:
            assert bundle is not None
            export_copy(bundle)
    if boundary == "sourcepack":
        assert not (tmp_path / "output").exists()
    else:
        assert bundle is not None
        assert not (bundle / "naver-copy.md").exists()


@pytest.mark.parametrize("boundary", ["sourcepack", "manifest", "approval"])
def test_invalid_utf8_json_is_rejected_without_manual_export(
    tmp_path: Path, boundary: str,
) -> None:
    target, bundle = _target(tmp_path, boundary)
    target.write_bytes(b'{"bad": "\xff"}\n')
    with pytest.raises(ContentError, match="cannot read JSON"):
        if boundary == "sourcepack":
            build_bundle(target, tmp_path / "output")
        elif boundary == "manifest":
            assert bundle is not None
            verify_bundle(bundle)
        else:
            assert bundle is not None
            export_copy(bundle)
    if boundary != "sourcepack":
        assert bundle is not None
        assert not (bundle / "naver-copy.md").exists()


@pytest.mark.parametrize("boundary", ["sourcepack", "manifest", "approval"])
def test_deeply_nested_json_fails_closed_at_every_approval_boundary(
    tmp_path: Path, boundary: str,
) -> None:
    target, bundle = _target(tmp_path, boundary)
    target.write_text("[" * 1500 + "0" + "]" * 1500, encoding="utf-8")
    with pytest.raises(ContentError, match="cannot read JSON"):
        if boundary == "sourcepack":
            build_bundle(target, tmp_path / "output")
        elif boundary == "manifest":
            assert bundle is not None
            verify_bundle(bundle)
        else:
            assert bundle is not None
            export_copy(bundle)
    if boundary != "sourcepack":
        assert bundle is not None
        assert not (bundle / "naver-copy.md").exists()

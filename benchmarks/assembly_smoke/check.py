from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.manifest import build_manifest, verify_manifest
from tools.manifest_input import ManifestBuildInput
from tools.notion_content_models import BlankBlock, ParsedNotionCopy
from tools.notion_copy_parser import parse_naver_copy

KEYWORD = "한정선 찹쌀떡"
ASSETS = (
    "image-map.md",
    "thumbnail.png",
    "image-01.png",
    "image-02.png",
    "image-03.png",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def without_blanks(parsed: ParsedNotionCopy) -> ParsedNotionCopy:
    return ParsedNotionCopy(
        parsed.title,
        tuple(block for block in parsed.blocks if not isinstance(block, BlankBlock)),
    )


def validate_contract(root: Path, keyword: str = KEYWORD) -> None:
    for suffix in ("layout", "copy", "input"):
        _ = parse_naver_copy(root / "final" / f"{keyword}-naver-{suffix}.md")
    manifest = build_manifest(
        ManifestBuildInput(
            root, keyword, "BENCH-ASSEMBLY", "BENCH-TOPIC", "2026-09-27T00:00:00+09:00"
        )
    )
    path = root / "benchmark-manifest.json"
    _ = path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    _ = verify_manifest(root, path)


def compare_golden(root: Path, reference: Path, keyword: str = KEYWORD) -> None:
    filename = f"{keyword}.md"
    if sha(root / "drafts" / filename) != sha(reference / "drafts" / filename):
        raise ValueError("input draft changed")
    if (root / "final" / filename).read_bytes() != (
        reference / "final" / filename
    ).read_bytes():
        raise ValueError("final Markdown differs from historical golden")
    for suffix in ("layout", "copy", "input"):
        filename = f"{keyword}-naver-{suffix}.md"
        got = without_blanks(parse_naver_copy(root / "final" / filename))
        expected = without_blanks(parse_naver_copy(reference / "final" / filename))
        if got != expected:
            raise ValueError(f"content/order/structure mismatch: {suffix}")
    for filename in ASSETS:
        if sha(root / "assets" / keyword / filename) != sha(
            reference / "assets" / keyword / filename
        ):
            raise ValueError(f"asset changed: {filename}")

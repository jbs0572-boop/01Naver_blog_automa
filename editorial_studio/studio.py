"""Independent five-category editorial style lock (local-only; no publishing)."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

PROFILE_DIR = Path(__file__).resolve().parent / "profiles"
PROFILE_IDS = (
    "life_health", "news_policy", "culture_people", "real_estate", "economy"
)
ARTIFACT_FILES = ("writer-brief.md", "image-brief.md", "layout-template.md")


class StyleError(ValueError):
    """The editorial contract, routing decision, or output is invalid."""


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _normalize(value: str) -> str:
    return re.sub(r"[^\w가-힣]+", "", value.casefold())


def profiles() -> dict[str, dict]:
    """Load all five independent Markdown sources of truth."""
    result: dict[str, dict] = {}
    for profile_id in PROFILE_IDS:
        path = PROFILE_DIR / f"{profile_id}.md"
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StyleError(f"missing category style: {path}") from exc
        fence = re.escape(chr(96) * 3)
        match = re.search(r"^" + fence + r"profile\s*\n(.*?)\n" + fence, raw, re.M | re.S)
        if not match:
            raise StyleError(f"{profile_id}: missing profile data block")
        try:
            item = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise StyleError(f"{profile_id}: invalid profile JSON") from exc
        required = ("id", "title", "keywords", "tone", "stance", "opening",
                    "sections", "image_style", "image_plan", "avoid")
        if not isinstance(item, dict) or any(key not in item for key in required):
            raise StyleError(f"{profile_id}: incomplete profile contract")
        if item["id"] != profile_id:
            raise StyleError(f"{profile_id}: category ID mismatch")
        if not isinstance(item["keywords"], list) or not item["keywords"] or any(
            not isinstance(x, str) or not x.strip() for x in item["keywords"]
        ):
            raise StyleError(f"{profile_id}: missing routing keywords")
        if not isinstance(item["sections"], list) or len(item["sections"]) != 5 or any(
            not isinstance(s, str) or not s.strip() for s in item["sections"]
        ) or len(set(item["sections"])) != 5:
            raise StyleError(f"{profile_id}: exactly five distinct headings required")
        if not isinstance(item["avoid"], list) or any(
            not isinstance(s, str) or not s.strip() for s in item["avoid"]
        ):
            raise StyleError(f"{profile_id}: invalid prohibited expressions")
        for field in ("title", "tone", "stance", "opening", "image_style", "image_plan"):
            if not isinstance(item[field], str) or not item[field].strip():
                raise StyleError(f"{profile_id}: invalid {field}")
        item["raw_md"] = raw
        item["sha256"] = sha256(raw.encode("utf-8"))
        result[profile_id] = item
    for field in ("tone", "stance", "image_style"):
        if len({result[k][field] for k in PROFILE_IDS}) != len(PROFILE_IDS):
            raise StyleError(f"styles are not independent: duplicate {field}")
    if len({tuple(result[k]["sections"]) for k in PROFILE_IDS}) != len(PROFILE_IDS):
        raise StyleError("styles are not independent: duplicate outlines")
    return result


def choose(topic: str, explicit: str | None = None) -> tuple[dict, dict[str, int]]:
    """Route by transparent literal keywords; never silently mix ties."""
    if not isinstance(topic, str) or not topic.strip() or len(topic) > 200:
        raise StyleError("topic must be 1..200 non-whitespace characters")
    catalog = profiles()
    if explicit is not None and explicit not in catalog:
        raise StyleError("unknown category; use one of: " + ", ".join(PROFILE_IDS))
    normalized = _normalize(topic)
    scores = {
        name: sum(1 for keyword in item["keywords"] if _normalize(keyword) in normalized)
        for name, item in catalog.items()
    }
    if explicit:
        return catalog[explicit], scores
    highest = max(scores.values())
    winners = [key for key, score in scores.items() if score == highest]
    if highest == 0 or len(winners) != 1:
        raise StyleError("ambiguous topic; set --category explicitly. scores="
                         + json.dumps(scores, ensure_ascii=False, sort_keys=True))
    return catalog[winners[0]], scores


def build_bundle(topic: str, output_root: Path, explicit: str | None = None) -> Path:
    """Create style-locked instructions and an empty layout, not an article."""
    profile, scores = choose(topic, explicit)
    slug = sha256(topic.strip().encode("utf-8"))[:12]
    bundle = output_root / f"{profile['id']}-{slug}"
    try:
        bundle.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise StyleError(f"bundle already exists; no overwrite: {bundle}") from exc
    style_hash = profile["sha256"]
    ordered_headings = "\n".join(
        f"{index}. {role}" for index, role in enumerate(profile["sections"], start=1)
    )
    writer = (
        f"# 글쓰기 작업 지시서: {topic.strip()}\n\n"
        f"선택 카테고리: {profile['title']} ({profile['id']})\n"
        f"스타일 잠금 SHA-256: {style_hash}\n\n"
        "다른 카테고리 스타일은 적용하지 않습니다. 아래 스타일 문서만 사용합니다.\n"
        "원문 자료를 실제로 확인한 뒤 사실마다 원문 URL·날짜를 연결하세요.\n"
        "자료의 사실과 해석을 분리하고 실제로 겪지 않은 개인 경험은 쓰지 마세요.\n"
        "아래 5개 편집 역할을 순서대로 수행하세요. 실제 소제목은 주제에 맞게 바꾸되 역할을 생략하지 마세요.\n\n"
        f"{ordered_headings}\n\n"
        "---\n\n"
        f"{profile['raw_md']}"
    )
    image = (
        f"# 이미지 제작 작업 지시서: {topic.strip()}\n\n"
        f"카테고리 고정: {profile['title']} ({profile['id']})\n"
        f"스타일 잠금 SHA-256: {style_hash}\n\n"
        f"표현 스타일: {profile['image_style']}\n"
        f"시각적 DNA: {profile.get('visual_dna', profile['image_style'])}\n"
        f"추천 배치: {profile['image_plan']}\n\n"
        "이미지를 실제 생성하기 전에는 생성 완료라고 표시하지 마세요.\n"
        "저해상도 자리표시자, 깨진 한글, 출처 없는 수치·로고·인물 사진을 금지합니다.\n"
        "최종 이미지가 있다면 최소 1280×720픽셀, 선명한 문자·축·선, "
        "출처와 권리 확인을 사람이 검수하세요.\n"
        "인용 자료와 이미지 생성물을 혼동하지 말고 이미지별 출처·생성 여부를 적으세요.\n"
        "실제 수치 도표는 확인된 데이터로만 그리세요.\n"
    )
    layout = (
        f"# [제목: {topic.strip()}]\n\n"
        "<!-- 이미지가 없으면 이 파일은 글·그림 조립 완료본이 아닙니다. -->\n\n"
        "<!-- THUMBNAIL_SLOT: 독립 이미지 자산을 검수 후 삽입 -->\n\n"
        + "\n\n".join(
            f"## [주제에 맞는 제목: {heading}]\n\n[검증된 내용 작성]\n\n"
            + ("<!-- IMAGE_SLOT: 정보가 추가되는 검수된 이미지만 배치 -->" if idx in (1, 3) else "")
            for idx, heading in enumerate(profile["sections"])
        ) + "\n"
    )
    content = {
        "writer-brief.md": writer.encode("utf-8"),
        "image-brief.md": image.encode("utf-8"),
        "layout-template.md": layout.encode("utf-8"),
    }
    for filename, raw in content.items():
        (bundle / filename).write_bytes(raw)
    manifest = {
        "schema_version": 1, "status": "BRIEF_ONLY",
        "topic": topic.strip(), "category": profile["id"], "category_name": profile["title"],
        "profile_file": f"editorial_studio/profiles/{profile['id']}.md",
        "profile_sha256": style_hash, "route_scores": scores,
        "headings": profile["sections"],
        "artifacts_sha256": {name: sha256(content[name]) for name in ARTIFACT_FILES},
        "draft_generated": False, "images_generated": False,
        "notion_or_naver_write": False,
    }
    (bundle / "style-lock.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return bundle


def check_bundle(bundle: Path, draft: Path | None = None) -> dict:
    """Check deterministic invariants, NOT subjective prose or image quality."""
    try:
        manifest = json.loads((bundle / "style-lock.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StyleError("missing or invalid style lock") from exc
    catalog = profiles()
    category = manifest.get("category")
    if category not in catalog:
        raise StyleError("unknown locked category")
    profile = catalog[category]
    if manifest.get("profile_sha256") != profile["sha256"]:
        raise StyleError("style drift: source Markdown changed after bundle was built")
    if manifest.get("headings") != profile["sections"]:
        raise StyleError("style drift: outline mismatch")
    expected = manifest.get("artifacts_sha256")
    if not isinstance(expected, dict) or set(expected) != set(ARTIFACT_FILES):
        raise StyleError("missing or unexpected brief artifacts")
    for filename in ARTIFACT_FILES:
        try:
            actual_hash = sha256((bundle / filename).read_bytes())
        except OSError as exc:
            raise StyleError(f"missing artifact: {filename}") from exc
        if actual_hash != expected[filename]:
            raise StyleError(f"modified or corrupt artifact: {filename}")
    result = {
        "category": category, "style_lock": "passed",
        "draft_structure": "not_checked", "prose_tone": "human_review_required",
        "source_truth": "not_checked", "image_quality": "not_checked",
    }
    if draft is not None:
        try:
            source = draft.read_text(encoding="utf-8")
        except OSError as exc:
            raise StyleError("draft file not found") from exc
        found = re.findall(r"^## (.+?)\s*$", source, re.M)
        # The profile fixes five narrative roles, NOT identical literal H2 titles.
        # Their semantic fulfillment and the prose tone still require editorial review.
        if len(found) != 5 or len(set(found)) != 5:
            raise StyleError("category outline mismatch; require five unique H2 sections")
        prohibited = profile["avoid"] + [
            "제가 직접 사용해", "제가 다녀온", "제가 실제로 계약한",
            "[검증된 내용 작성]", "<!-- IMAGE_SLOT",
        ]
        for phrase in prohibited:
            if phrase.casefold() in source.casefold():
                raise StyleError(f"forbidden or unfinished draft phrase: {phrase}")
        result["draft_structure"] = "passed"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Five independent, category-specific blog style contracts (offline only)"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser("build", help="route topic and lock ONE style")
    builder.add_argument("--topic", required=True)
    builder.add_argument("--category", choices=PROFILE_IDS)
    builder.add_argument("--output-root", type=Path, default=Path("editorial-preview"))
    checker = commands.add_parser("check", help="verify style lock and optional draft outline")
    checker.add_argument("--bundle", required=True, type=Path)
    checker.add_argument("--draft", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            bundle = build_bundle(args.topic, args.output_root, args.category)
            print(json.dumps({"bundle": str(bundle), "status": "BRIEF_ONLY"},
                             ensure_ascii=False))
        else:
            print(json.dumps(check_bundle(args.bundle, args.draft), ensure_ascii=False))
    except (StyleError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

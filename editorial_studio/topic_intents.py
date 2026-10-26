"""Category purpose contracts and per-article intend.md generation, offline-only.

A category intend.md describes WHY to research/write/show images. The style
profiles describe HOW to speak and look. Neither authorizes publishing.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

TOPIC_DIR = Path(__file__).resolve().parent / "topics"
CATEGORIES = (
    "life_health", "news_policy", "culture_people", "real_estate", "economy",
)
IMAGE_ROLES = ("context-photo", "information-graphic", "action-checklist")

class IntentError(ValueError):
    """Missing, invalid or mismatched authorial purpose; do not proceed."""


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 700


def _list(value: object, minimum: int = 2, maximum: int = 12) -> bool:
    return (
        isinstance(value, list)
        and minimum <= len(value) <= maximum
        and all(_nonempty(item) for item in value)
        and len(set(value)) == len(value)
    )


def load(category: str) -> dict:
    """Validate the five versioned intent contracts and bind their raw digest."""
    if category not in CATEGORIES:
        raise IntentError("unknown category for intend.md")
    path = TOPIC_DIR / category / "intend.md"
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as error:
        raise IntentError("missing category intend.md: " + str(path)) from error
    marker = re.escape(chr(96) * 3)
    blocks = re.findall(
        r"^" + marker + r"intent[ \t]*\n(.*?)\n" + marker + r"[ \t]*$",
        source, flags=re.M | re.S,
    )
    if len(blocks) != 1:
        raise IntentError("intend.md must contain exactly one intent JSON block")
    try:
        data = json.loads(blocks[0])
    except json.JSONDecodeError as error:
        raise IntentError("invalid intend.md JSON") from error
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise IntentError("unsupported intend.md schema")
    if data.get("id") != category:
        raise IntentError("intend.md category ID mismatch")
    text_fields = (
        "label", "reader_problem", "search_intent", "answer_promise",
        "original_visual_target", "success_signal", "staleness",
    )
    if any(not _nonempty(data.get(field)) for field in text_fields):
        raise IntentError("missing required category purpose")
    for field in ("research_priority", "source_hierarchy", "fact_checks",
                  "forbidden"):
        if not _list(data.get(field)):
            raise IntentError("incomplete intend.md evidence/stop rules: " + field)
    if not _list(data.get("story_flow"), minimum=5, maximum=5):
        raise IntentError("intend.md requires five distinct narrative roles")
    visuals = data.get("image_roles")
    if (not isinstance(visuals, dict)
            or set(visuals) != set(IMAGE_ROLES)
            or not all(_nonempty(v) for v in visuals.values())
            or len(set(visuals.values())) != 3):
        raise IntentError("intend.md requires three distinct useful image roles")
    if not all("## " + heading in source for heading in (
        "목적", "자료 조사 및 원문 검증", "본문 구성", "이미지 원본 사용과 생성 의도",
        "성공 기준과 중단 조건", "실행기가 검증할 구조화된 의도",
    )):
        raise IntentError("intend.md missing human-readable editorial sections")
    return {
        **data,
        "source_path": "editorial_studio/topics/" + category + "/intend.md",
        "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
    }


def load_all() -> dict[str, dict]:
    """All five categories must have independent intent, not copied templates."""
    catalog = {category: load(category) for category in CATEGORIES}
    for field in ("reader_problem", "search_intent", "answer_promise",
                  "original_visual_target", "success_signal"):
        if len({value[field] for value in catalog.values()}) != len(CATEGORIES):
            raise IntentError("category intend.md purposes are not independent: " + field)
    return catalog


def _literal(value: str) -> str:
    """Keep untrusted article titles/data inert inside Markdown instructions."""
    value = value.replace("\\", "\\\\")
    for token in ("[", "]", "*", "_", chr(96), "<", ">", "#", "|"):
        value = value.replace(token, "\\" + token)
    return value.replace("\r", " ").replace("\n", " ")


def render_for_topic(category: str, topic: str, *,
                     case: dict | None = None,
                     contract: dict | None = None) -> str:
    """Materialize per-ARTICLE intend.md, not just a folder-wide style guide.

    A source pack is input data, not a claim of live retrieval or approval.
    """
    selected = contract if contract is not None else load(category)
    if selected.get("id") != category:
        raise IntentError("intend.md source category mismatch")
    if not _nonempty(topic) or len(topic) > 200:
        raise IntentError("article title/topic must be nonempty <=200 chars")
    if "\x00" in topic:
        raise IntentError("article topic contains invalid control")
    lines = [
        "# 이 글의 제작 의도: " + _literal(topic),
        "",
        "- 선택 카테고리: " + selected["label"] + " (" + category + ")",
        "- 카테고리 의도 원본: " + selected["source_path"],
        "- 카테고리 의도 SHA-256: " + selected["sha256"],
        "- 상태: 검수 전 기획 / 네이버·Notion 미저장 / 발행 승인 아님",
        "",
        "## 왜 이 주제를 쓰는가",
        selected["reader_problem"],
        "",
        "## 이 글이 답해야 할 검색 질문",
        selected["search_intent"],
        "",
        "**독자에게 주는 답:** " + selected["answer_promise"],
        "",
        "## 이번 글의 필수 조사 순서",
    ]
    lines += ["- " + item for item in selected["research_priority"]]
    lines += ["", "## 출처 우선순위"]
    lines += [str(i) + ". " + value for i, value in
              enumerate(selected["source_hierarchy"], 1)]
    lines += ["", "## 주장 검증과 불합격 조건"]
    lines += ["- 확인: " + item for item in selected["fact_checks"]]
    lines += ["- 금지: " + item for item in selected["forbidden"]]
    lines += ["", "## 독자 흐름 (5개 서로 다른 정보 역할)"]
    lines += [str(i) + ". " + value for i, value in
              enumerate(selected["story_flow"], 1)]
    lines += ["", "## 원본 이미지와 3가지 시각 역할",
              "**원본 우선 대상:** " + selected["original_visual_target"]]
    lines += ["- " + role + ": " + selected["image_roles"][role]
              for role in IMAGE_ROLES]
    lines += ["", "## 글과 이미지 조립 및 화면 검증",
              "- 각 사실 ID를 원문 링크·기준일과 연결하고, 새로운 사실을 창작하지 않습니다.",
              "- 세 이미지를 같은 장식으로 반복하지 않고 설명 위치에 배치합니다.",
              "- HTML/CSS와 Playwright로 PC·모바일 캡처를 확인합니다.",
              "- 원본 파일·사진 크레딧·면책 문구와 이미지 SHA를 유지합니다.",
              "- 95/100 구조 점수는 사람의 진실성·미감 검수 또는 유입 실적이 아닙니다.",
              "- 사실관계·권리·콘텐츠 미감·승인 Gate가 미확인되면 발행하지 않습니다.",
              "",
              "## 독자가 얻어야 할 결과", selected["success_signal"],
              "", "**재조사 시점:** " + selected["staleness"], ""]
    if case is not None:
        if not isinstance(case, dict):
            raise IntentError("article evidence must be a mapping")
        source_topic = case.get("topic") or case.get("source_topic")
        date = case.get("as_of") or case.get("source_as_of")
        sources = case.get("sources", [])
        facts = case.get("facts", [])
        if not _nonempty(source_topic) or not _nonempty(date):
            raise IntentError("article evidence is missing source topic/date")
        if not isinstance(sources, list) or not isinstance(facts, list):
            raise IntentError("article source/fact metadata malformed")
        lines += [
            "## 이번 글에 연결된 근거팩 (원문 대조 별도 필요)",
            "- 근거팩 주제: " + _literal(source_topic),
            "- 근거 기준일: " + _literal(date),
            "- 수록 출처: " + str(len(sources)),
            "- 수록 사실 ID: " + str(len(facts)),
            "- 실제 원문과의 대조: 아직 사람 확인 필요",
            "- 최신성/정합성 확인: 원본 링크를 열어 확인하기 전에는 PASS 아님",
            "",
        ]
        # URLs here are candidate DATA, not vetted source quotations. Preserve
        # the article-specific research locations without activating Markdown links.
        lines += ["### 직접 열어 사실을 대조할 출처"]
        for source in sources:
            if not isinstance(source, dict):
                raise IntentError("source metadata must be a mapping")
            title = source.get("title")
            url = source.get("url")
            if not _nonempty(title) or not _nonempty(url):
                raise IntentError("source title/URL missing from reviewed source pack")
            lines.append("- " + _literal(str(title)) + ": " + _literal(str(url)))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"

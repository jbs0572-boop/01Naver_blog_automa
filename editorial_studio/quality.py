"""Versioned offline quality proxies. These scores are NOT organic traffic metrics."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import studio

RUBRICS = Path(__file__).resolve().parent / "rubrics"


class QualityError(ValueError):
    pass


def policy(version: str) -> dict:
    if version not in ("v1", "v2"):
        raise QualityError("unknown frozen rubric version")
    item = json.loads((RUBRICS / f"{version}.json").read_text(encoding="utf-8"))
    weights = item.get("weights")
    expected = {"source_integrity", "category_consistency", "search_intent",
                "reader_value", "image_quality", "mobile_layout"}
    if not isinstance(weights, dict) or set(weights) != expected:
        raise QualityError("rubric weights do not match required dimensions")
    if (sum(weights.values()) != 100
            or any(type(x) is not int or x < 5 for x in weights.values())
            or weights["source_integrity"] < 25
            or weights["image_quality"] < 15):
        raise QualityError("rubric reduces hard safety/accuracy floor")
    return item


def _safe_ratio(hit: bool) -> float:
    return 1.0 if hit else 0.0


def assess(bundle: Path, rubric: str = "v2") -> dict:
    """Read saved draft and real image. Never infer quality from a metadata claim."""
    from .engine import EngineError, inspect_cover, load_case
    guide = policy(rubric)
    raw = json.loads((bundle / "sourcepack.json").read_text(encoding="utf-8"))
    case = load_case(raw)
    profile = studio.profiles()[case["category"]]
    lock = json.loads((bundle / "style-lock.json").read_text(encoding="utf-8"))
    article = json.loads((bundle / "article-structure.json").read_text(encoding="utf-8"))
    md = (bundle / "draft.md").read_text(encoding="utf-8")
    preview = (bundle / "preview.html").read_text(encoding="utf-8")
    cover = bundle / "cover.png"
    studio.check_bundle(bundle, draft=bundle / "draft.md")
    title = article.get("title", "")
    intro = article.get("intro", "")
    sections = article.get("sections", [])
    facts = case["facts"]
    covered = {fid for section in sections for fid in section.get("fact_ids", [])}
    expected = {fact["id"] for fact in facts}
    source_ids = {src["id"] for src in case["sources"]}
    citation_ok = all(
        ("[" + src["id"] + "](" + src["url"] + ")") in md
        for src in case["sources"]
    )
    site_is_explicit = all(
        fact["source_id"] in source_ids and any(
            fact["id"] in section["fact_ids"]
            for section in sections if section["role"] == fact["role"]
        )
        for fact in facts
    )
    source_ratio = (0.20 * _safe_ratio(bool(facts)) +
                    0.20 * _safe_ratio(site_is_explicit) +
                    0.25 * _safe_ratio(covered == expected) +
                    0.20 * _safe_ratio(citation_ok) +
                    0.15 * _safe_ratio(len(set(source_ids)) == len(source_ids)))
    style_ratio = (0.50 * _safe_ratio(lock.get("profile_sha256") == profile["sha256"]) +
                   0.30 * _safe_ratio(len(sections) == 5) +
                   0.20 * _safe_ratio(all(
                       expr.casefold() not in md.casefold()
                       for expr in profile["avoid"]
                   )))
    tokens = [token for token in re.findall(r"[\w가-힣]{2,}", case["topic"])
              if token not in ("아파트", "영화", "정책", "경제", "방법")]
    title_match = any(token in title for token in tokens) if tokens else bool(title)
    intent_match = any(token in intro for token in tokens) if tokens else bool(intro)
    hype = any(mark in title for mark in ("무조건", "대박", "충격", "100%", "!!!"))
    repetition = len(re.findall(r"([\w가-힣]{3,})\1\1", md)) > 0
    search_ratio = (0.5 * _safe_ratio(title_match) +
                    0.2 * _safe_ratio(intent_match) +
                    0.2 * _safe_ratio(len(title) <= 60) +
                    0.1 * _safe_ratio(not hype and not repetition))
    body = " ".join(section.get("body", "") for section in sections)
    count = len(re.sub(r"\s", "", body))
    editor_ratio = (0.30 * _safe_ratio(count >= 260) +
                    0.20 * _safe_ratio(count <= 2500) +
                    0.30 * _safe_ratio(len(covered) == len(facts)) +
                    0.20 * _safe_ratio(all(len(section.get("body", "")) >= 25 for section in sections)))
    image_info = None
    if cover.exists():
        image_info = inspect_cover(cover.read_bytes())
        # Measured technical fidelity is not aesthetic or topic-alignment verification.
        image_ratio = (0.35 * _safe_ratio(image_info["width"] >= 1280) +
                       0.30 * _safe_ratio(image_info["color_bins"] >= 30) +
                       0.15 * _safe_ratio(image_info["contrast"] >= 8) +
                       0.20 * _safe_ratio("alt=" in preview))
    else:
        image_ratio = 0.0
    layout_ratio = (0.35 * _safe_ratio(len(re.findall(r"^## ", md, re.M)) == 5) +
                    0.35 * _safe_ratio("viewport" in preview) +
                    0.30 * _safe_ratio("<h1>" in preview and "<h2>" in preview))
    scores = {
        "source_integrity": source_ratio,
        "category_consistency": style_ratio,
        "search_intent": search_ratio,
        "reader_value": editor_ratio,
        "image_quality": image_ratio,
        "mobile_layout": layout_ratio,
    }
    components = {
        name: round(guide["weights"][name] * value, 2)
        for name, value in scores.items()
    }
    total = round(sum(components.values()), 2)
    fail = []
    if not image_info:
        fail.append("image_missing")
    if not citation_ok or not site_is_explicit:
        fail.append("source_links_missing")
    if len(covered) != len(facts):
        fail.append("facts_not_used")
    if not title_match:
        fail.append("search_intent_not_in_headline")
    if total < guide["review_candidate_score"]:
        fail.append("offline_score_below_candidate_threshold")
    # Review is always pending, since automation cannot verify image art direction,
    # claims against original documents, novelty or real-world clicks.
    return {
        "rubric_version": rubric, "score": total,
        "dimensions": components,
        "hard_flags": fail,
        "image_verified_technically": image_info is not None,
        "evidence_truth": "NOT_AUTOMATICALLY_VERIFIED",
        "semantic_style_consistency": "HUMAN_REVIEW_REQUIRED",
        "image_aesthetics": "HUMAN_REVIEW_REQUIRED",
        "organic_impressions": None, "organic_clicks": None,
        "organic_ctr": None, "organic_growth": None,
        "ready_for_publication": False,
        "status": "CANDIDATE_FOR_HUMAN_REVIEW" if not fail else "REVISION_NEEDED",
    }


def compare_rubrics(bundle: Path) -> dict:
    old = assess(bundle, "v1")
    new = assess(bundle, "v2")
    return {
        "v1_score": old["score"],
        "v2_score": new["score"],
        "v2_minus_v1": round(new["score"] - old["score"], 2),
        "not_comparable_across_versions_without_backtest": True,
        "quality_floor_changed": "v2 has stricter emphasis on real images and search intent",
    }


def measured_outcomes(records: list[dict]) -> dict:
    """Post-publication metrics ONLY. Never impute zero traffic to unpublished posts."""
    if not records:
        return {"state": "NO_POST_PUBLICATION_DATA", "organic_clicks": None}
    grouped: dict[str, dict] = {}
    for row in records:
        category = row.get("category")
        impressions = row.get("organic_impressions")
        clicks = row.get("organic_clicks")
        if (category not in studio.PROFILE_IDS
                or type(impressions) is not int or type(clicks) is not int
                or impressions < 0 or clicks < 0 or clicks > impressions):
            raise QualityError("analytics requires valid real impressions/clicks and category")
        agg = grouped.setdefault(category, {"posts": 0, "impressions": 0, "clicks": 0})
        agg["posts"] += 1
        agg["impressions"] += impressions
        agg["clicks"] += clicks
    for agg in grouped.values():
        agg["ctr"] = round(agg["clicks"] / agg["impressions"], 4) if agg["impressions"] else None
    return {"state": "MEASURED_POST_PUBLICATION", "by_category": grouped,
            "warning": "CTR alone is not causal evidence that a style caused traffic growth."}

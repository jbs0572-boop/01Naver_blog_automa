"""Run exactly two distinct topics per editorial category as a frozen offline regression."""
from __future__ import annotations

import argparse
import difflib
import json
import tempfile
from pathlib import Path

from . import engine, quality, studio


class BenchmarkError(ValueError):
    pass


def run_benchmark(cases_file: Path, output_dir: Path,
                  image_map: dict[str, str] | None = None) -> dict:
    dataset = json.loads(cases_file.read_text(encoding="utf-8"))
    cases = dataset.get("cases")
    if not isinstance(cases, list) or len(cases) != 10:
        raise BenchmarkError("frozen suite requires exactly 10 cases")
    categories = {k: [] for k in studio.PROFILE_IDS}
    ids: set[str] = set()
    scores = []
    output_dir.mkdir(parents=True, exist_ok=True)
    roots = output_dir / "articles"
    roots.mkdir(parents=True, exist_ok=True)
    for original in cases:
        case = engine.load_case(original)
        case_id = original.get("case_id")
        if not isinstance(case_id, str) or not case_id or case_id in ids:
            raise BenchmarkError("invalid or repeated case ID")
        ids.add(case_id)
        if case["category"] not in categories:
            raise BenchmarkError("unexpected category")
        categories[case["category"]].append(case_id)
        casepath = roots / (case_id + ".json")
        casepath.write_bytes(engine.canonical(original) + b"\n")
        image_file = image_map.get(case_id) if image_map else None
        bundle = engine.run(
            casepath, roots, text_backend="fixture",
            image_backend="import" if image_file else "none",
            imported_image=Path(image_file) if image_file else None,
            rubric="v2",
        )
        result = json.loads((bundle / "result.json").read_text(encoding="utf-8"))
        check = studio.check_bundle(bundle, bundle / "draft.md")
        score_v1_v2 = quality.compare_rubrics(bundle)
        scores.append({
            "id": case_id, "category": case["category"],
            "topic": case["topic"], "bundle": str(bundle),
            "profile_sha256": result["profile_sha256"],
            "sourcepack_sha256": result["sourcepack_sha256"],
            "text_backend": "fixture_non_llm",
            "image_backend": result["image_backend"], "state": result["state"],
            "image_sha256": result["image_inspection"]["sha256"] if result["image_inspection"] else None,
            "style_check": check["style_lock"],
            "score": result["score"],
            "versions": score_v1_v2,
            "image_prompt": (bundle / "cover-prompt.txt").read_text(encoding="utf-8"),
            "draft": (bundle / "draft.md").read_text(encoding="utf-8"),
        })
    if any(len(v) != 2 for v in categories.values()):
        raise BenchmarkError("each category must have exactly two independent topics")
    pairs = []
    for key in studio.PROFILE_IDS:
        a, b = [r for r in scores if r["category"] == key]
        if a["profile_sha256"] != b["profile_sha256"]:
            raise BenchmarkError("same-category style drift: " + key)
        if a["image_prompt"] == b["image_prompt"] or a["draft"] == b["draft"]:
            raise BenchmarkError("identical output across different topics: " + key)
        similarity = difflib.SequenceMatcher(None, a["draft"], b["draft"]).ratio()
        if similarity >= 0.90:
            raise BenchmarkError("near-duplicate article generated: " + key)
        if a["image_sha256"] and a["image_sha256"] == b["image_sha256"]:
            raise BenchmarkError("same category used duplicate cover image: " + key)
        pairs.append({
            "category": key, "topics": [a["topic"], b["topic"]],
            "style_hash_equal": True, "draft_similarity": round(similarity, 3),
            "image_prompts_distinct": True, "cover_images_distinct": (
                a["image_sha256"] != b["image_sha256"]
                if a["image_sha256"] and b["image_sha256"] else None
            ),
            "v2_scores": [a["score"]["score"], b["score"]["score"]],
        })
    summary = {
        "benchmark_version": "2026-10-25-10-official-source-topics",
        "category_count": len(categories), "article_count": len(scores),
        "pair_count": len(pairs), "pairs": pairs,
        "style_lock": "passed", "no_near_duplicates": True,
        "all_images_present": all(x["image_sha256"] for x in scores),
        "all_real_model_calls": False,
        "uses_fixture_writer": True,
        "traffic_metrics_available": False,
        "objective": "maximize later-measured organic search visits",
        "rubric_comparison": {
            "v1_mean": round(sum(r["versions"]["v1_score"] for r in scores) / 10, 2),
            "v2_mean": round(sum(r["versions"]["v2_score"] for r in scores) / 10, 2),
            "scores_are_not_traffic": True,
        },
        "non_claim": "Passing this fixture benchmark is not proof that paid model calls work or that any articles rank or receive clicks.",
    }
    (output_dir / "benchmark-results.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = ["# 5 categories × 2 independent topics: frozen regression", "",
             "No paid API calls, no Naver/Notion writes and NO real traffic measured.", "",
             "| category | pair A / B | identical style MD? | draft similarity | v2 scores |",
             "|---|---|---|---:|---:|"]
    for pair in pairs:
        lines.append(
            "| {category} | {topics} | yes | {draft_similarity} | {v2_scores} |".format(
                category=pair["category"], topics=" / ".join(pair["topics"]),
                draft_similarity=pair["draft_similarity"],
                v2_scores=", ".join(map(str, pair["v2_scores"]))
            )
        )
    lines += ["", "## Gate",
              "- Identical profile hash within each category: passed",
              "- Unique image prompts and article content: passed",
              "- Real image assets: " + ("all inspected" if summary["all_images_present"] else "missing: image pipeline not fully exercised"),
              "- Human source, style and image review: PENDING",
              "- SEO organic click measurement: NOT AVAILABLE",
              "", "## Rubric version comparison", "",
              "v1 mean: " + str(summary["rubric_comparison"]["v1_mean"]),
              "v2 mean: " + str(summary["rubric_comparison"]["v2_mean"]),
              "Different weighted scales, not evidence of quality improvement or traffic.", ""]
    (output_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="10-case no-publish repeatability benchmark")
    p.add_argument("--cases", type=Path, default=Path(
        "editorial_studio/benchmarks/cases.json"))
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--image-map", type=Path, help="JSON: case_id -> PNG path")
    args = p.parse_args(argv)
    try:
        image_map = json.loads(args.image_map.read_text(encoding="utf-8")) if args.image_map else None
        summary = run_benchmark(args.cases, args.out_dir, image_map)
    except (BenchmarkError, engine.EngineError, studio.StyleError, quality.QualityError,
            OSError, ValueError) as exc:
        p.exit(2, "benchmark: " + str(exc) + "\n")
    print(json.dumps({
        "cases": summary["article_count"], "pairs": summary["pair_count"],
        "style_lock": summary["style_lock"],
        "images_ready": summary["all_images_present"],
        "organic_traffic_measured": False,
        "v1_mean": summary["rubric_comparison"]["v1_mean"],
        "v2_mean": summary["rubric_comparison"]["v2_mean"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

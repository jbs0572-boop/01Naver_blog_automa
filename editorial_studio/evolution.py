"""Holdout-validated, fail-closed rubric upgrade proposals.

No online learning, no silent rewrites of historical scores, no publishing.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from . import quality, studio

CAUSES = {
    "image_offtopic": "image_quality",
    "blurry_image": "image_quality",
    "broken_text_in_image": "image_quality",
    "false_claim": "source_integrity",
    "uncited_claim": "source_integrity",
    "wrong_search_intent": "search_intent",
    "stiff_category_voice": "category_consistency",
    "thin_original_value": "reader_value",
    "mobile_overflow": "mobile_layout",
}
DONOR_MINIMUM = {
    "source_integrity": 25, "image_quality": 20,
    "search_intent": 15, "reader_value": 10,
    "category_consistency": 10, "mobile_layout": 5,
}


class EvolutionError(ValueError):
    pass


def _score(weights: dict, components: dict) -> float:
    return round(sum(weights[k] * components[k] for k in weights), 2)


def validate_feedback(raw: object) -> list[dict]:
    if not isinstance(raw, list) or len(raw) < 15:
        raise EvolutionError("need at least 15 independently human-reviewed cases")
    ids = set()
    sets = Counter()
    cats = Counter()
    validated = []
    for item in raw:
        if not isinstance(item, dict):
            raise EvolutionError("feedback must be JSON objects")
        docid = item.get("case_id")
        category = item.get("category")
        sample = item.get("set")
        decision = item.get("human_review")
        measurements = item.get("dimensions")
        reasons = item.get("failure_reasons", [])
        if (not isinstance(docid, str) or not docid.strip() or docid in ids
                or category not in studio.PROFILE_IDS
                or sample not in {"train", "holdout"}
                or decision not in {"pass", "fail"}
                or not isinstance(measurements, dict)
                or set(measurements) != set(quality.policy("v2")["weights"])
                or not isinstance(reasons, list)
                or any(x not in CAUSES for x in reasons)
                or (decision == "pass" and reasons)):
            raise EvolutionError("bad review schema or duplicate case")
        for score in measurements.values():
            if type(score) not in (int, float) or not 0 <= score <= 1:
                raise EvolutionError("dimension inputs must be normalized 0..1")
        ids.add(docid)
        cats[category] += 1
        sets[sample] += 1
        validated.append(item)
    if sets["train"] < 10 or sets["holdout"] < 5 or any(
        cats[x] < 2 for x in studio.PROFILE_IDS
    ):
        raise EvolutionError("need at least 10 train / 5 untouched holdout and 2 per category")
    return validated


def propose(records: list[dict]) -> dict:
    """Candidate is accepted as a PROPOSAL only when untouched holdout improves."""
    rows = validate_feedback(records)
    old = quality.policy("v2")
    train = [x for x in rows if x["set"] == "train"]
    holdout = [x for x in rows if x["set"] == "holdout"]
    issues = Counter(CAUSES[reason] for r in train if r["human_review"] == "fail"
                     for reason in r["failure_reasons"])
    if not issues or max(issues.values()) < 3:
        return {"decision": "INSUFFICIENT_REVIEW_FAILURE_SIGNAL",
                "issues": dict(issues), "modified_rubric": None}
    focus, occur = issues.most_common(1)[0]
    next_weights = dict(old["weights"])
    sources = sorted(
        (k for k in next_weights if k != focus
         and next_weights[k] >= DONOR_MINIMUM[k] + 5
         and k not in {"source_integrity", "image_quality"}),
        key=lambda x: (issues[x], -next_weights[x], x)
    )
    if not sources:
        return {"decision": "NO_SAFE_WEIGHT_REALLOCATION",
                "focus": focus, "modified_rubric": None}
    donor = sources[0]
    next_weights[focus] += 5
    next_weights[donor] -= 5
    if (sum(next_weights.values()) != 100
            or next_weights["source_integrity"] < 25
            or next_weights["image_quality"] < 20):
        raise EvolutionError("candidate would reduce non-negotiable floor")
    cutoff = old["review_candidate_score"]

    def confusion(weights: dict) -> dict:
        fp = fn = tp = tn = 0
        for row in holdout:
            predict = _score(weights, row["dimensions"]) >= cutoff
            actual = row["human_review"] == "pass"
            if predict and not actual:
                fp += 1
            elif actual and not predict:
                fn += 1
            elif predict and actual:
                tp += 1
            else:
                tn += 1
        return {"false_positive": fp, "false_negative": fn,
                "true_positive": tp, "true_negative": tn}
    before = confusion(old["weights"])
    after = confusion(next_weights)
    improvement = (after["false_positive"] < before["false_positive"] and
                   after["false_negative"] <= before["false_negative"])
    return {
        "decision": "HOLDOUT_SUPPORTED_PROPOSAL" if improvement else "REJECTED_HOLDOUT",
        "focus": focus, "issue_count_in_train": occur, "donor": donor,
        "train_count": len(train), "holdout_count": len(holdout),
        "before": before, "after": after,
        "modified_rubric": ({
            "version": "v3-proposal", "weights": next_weights,
            "review_candidate_score": cutoff,
            "status": "NOT_ACTIVE_HUMAN_APPROVAL_REQUIRED",
            "rationale": "Promoted only as a candidate after frozen holdout discrimination test"
        } if improvement else None),
        "warning": "Never compare raw scores across version weights as traffic performance.",
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Propose rubric version from reviewed outcomes")
    p.add_argument("--feedback", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        data = json.loads(args.feedback.read_text(encoding="utf-8"))
        candidate = propose(data)
        if args.out.exists() or args.out.is_symlink():
            raise EvolutionError("output exists; no overwrite")
        with args.out.open("x", encoding="utf-8") as handle:
            json.dump(candidate, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        print(json.dumps({"decision": candidate["decision"], "out": str(args.out)},
                         ensure_ascii=False))
    except (EvolutionError, ValueError, OSError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

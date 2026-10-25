"""Aggregate manually supplied real organic search metrics; no provider connections."""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from . import studio


class TrafficError(ValueError):
    pass


def summarize(records: object) -> dict:
    if not records:
        return {
            "state": "NO_MEASURED_SEARCH_TRAFFIC", "by_category": {},
            "natural_search_visits": None, "ctr": None,
            "optimization_decision": "INSUFFICIENT_REAL_DATA",
        }
    if not isinstance(records, list):
        raise TrafficError("metrics must be an array")
    totals: dict[str, dict] = defaultdict(
        lambda: {"posts": set(), "impressions": 0, "clicks": 0, "sessions": 0}
    )
    keys = set()
    cohort = set()
    for item in records:
        if not isinstance(item, dict):
            raise TrafficError("metric row must be object")
        category = item.get("category")
        post = item.get("post_id")
        start, end = item.get("start_date"), item.get("end_date")
        analytics_source = item.get("analytics_source")
        if (category not in studio.PROFILE_IDS or not isinstance(post, str)
                or not post.strip() or not isinstance(analytics_source, str)
                or not analytics_source.strip()):
            raise TrafficError("real post ID, category, analytics source required")
        try:
            if date.fromisoformat(start) > date.fromisoformat(end):
                raise ValueError("period reversed")
        except (ValueError, TypeError):
            raise TrafficError("invalid data window") from None
        marker = (post, start, end)
        if marker in keys:
            raise TrafficError("duplicate post/window")
        keys.add(marker)
        cohort.add((start, end))
        ints = [item.get(k) for k in
                ("organic_impressions", "organic_clicks", "organic_sessions")]
        if any(type(x) is not int or x < 0 for x in ints) or ints[1] > ints[0]:
            raise TrafficError("invalid measured search impressions/clicks/sessions")
        group = totals[category]
        group["posts"].add(post)
        group["impressions"] += ints[0]
        group["clicks"] += ints[1]
        group["sessions"] += ints[2]
    output = {}
    for category in studio.PROFILE_IDS:
        if category in totals:
            item = totals[category]
            output[category] = {
                "posts": len(item["posts"]), "impressions": item["impressions"],
                "clicks": item["clicks"], "sessions": item["sessions"],
                "ctr": (round(item["clicks"] / item["impressions"], 4)
                        if item["impressions"] else None),
            }
    windows_match = len(cohort) == 1
    mature = windows_match and len(output) == 5 and all(x["posts"] >= 3 for x in output.values())
    return {
        "state": "MEASURED_INPUT_PROVIDED",
        "by_category": output,
        "natural_search_visits": sum(x["sessions"] for x in output.values()),
        "organic_clicks": sum(x["clicks"] for x in output.values()),
        "organic_impressions": sum(x["impressions"] for x in output.values()),
        "ctr": round(sum(x["clicks"] for x in output.values()) /
                     sum(x["impressions"] for x in output.values()), 4)
               if sum(x["impressions"] for x in output.values()) else None,
        "windows_comparable": windows_match,
        "optimization_decision": ("CANDIDATE_CATEGORY_ALLOCATION_REVIEW"
                                  if mature else "INSUFFICIENT_COMPARABLE_POSTS"),
        "warning": ("Reported visits and CTR are descriptive; without matched controls "
                    "and equal exposure windows they cannot prove causal style effects."),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Aggregate real search traffic without posting")
    p.add_argument("--analytics", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        report = summarize(json.loads(args.analytics.read_text(encoding="utf-8")))
        if args.out.exists() or args.out.is_symlink():
            raise TrafficError("output exists")
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print(json.dumps({"state": report["state"],
                          "natural_search_visits": report["natural_search_visits"],
                          "optimization_decision": report["optimization_decision"]},
                         ensure_ascii=False))
        return 0
    except (TrafficError, OSError, ValueError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

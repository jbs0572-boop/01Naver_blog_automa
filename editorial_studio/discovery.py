"""Read-only relative Naver Search Trend topic discovery; no guessed volume.

Naver Datalab compares at most 5 keyword groups *in one response*.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

from . import studio

ENDPOINT = "https://openapi.naver.com/v1/datalab/search"


class DiscoveryError(ValueError):
    pass


def make_plan(topic_groups: list[dict], start: str, end: str) -> dict:
    for item in (start, end):
        try:
            stamp = date.fromisoformat(item)
        except ValueError as exc:
            raise DiscoveryError("start/end require YYYY-MM-DD") from exc
        if stamp.isoformat() != item:
            raise DiscoveryError("start/end require exact YYYY-MM-DD")
    if start >= end:
        raise DiscoveryError("startDate must precede endDate")
    if not 2 <= len(topic_groups) <= 5:
        raise DiscoveryError("compare 2..5 groups ONLY per relative trend cohort")
    names = set()
    groups = []
    for entry in topic_groups:
        if not isinstance(entry, dict):
            raise DiscoveryError("topic group must be object")
        name = entry.get("query")
        category = entry.get("category")
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise DiscoveryError("invalid query")
        if category not in studio.PROFILE_IDS:
            raise DiscoveryError("category must be one of five editorial styles")
        if name in names:
            raise DiscoveryError("duplicate topic group")
        names.add(name)
        groups.append({"groupName": name, "keywords": [name]})
    return {"startDate": start, "endDate": end, "timeUnit": "week",
            "keywordGroups": groups, "device": "mo"}


def _post(plan: dict) -> dict:
    client_id = os.environ.get("NAVER_CLIENT_ID")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise DiscoveryError("NAVER_CLIENT_ID/SECRET missing, no request sent")
    request = Request(ENDPOINT, data=json.dumps(plan, ensure_ascii=False).encode("utf-8"),
                      method="POST", headers={
                          "X-Naver-Client-Id": client_id,
                          "X-Naver-Client-Secret": client_secret,
                          "Content-Type": "application/json",
                      })
    try:
        with urlopen(request, timeout=12) as response:
            raw = response.read(1024 * 1024 + 1)
    except Exception:
        raise DiscoveryError("Naver Datalab API unavailable; no logged credentials") from None
    if len(raw) > 1024 * 1024:
        raise DiscoveryError("oversized trend response")
    try:
        obj = json.loads(raw)
    except ValueError as exc:
        raise DiscoveryError("malformed trend JSON") from exc
    if not isinstance(obj, dict):
        raise DiscoveryError("malformed trend payload")
    return obj


def score_trends(plan: dict, response: dict) -> dict:
    """Relative indices, never search volume or CTR estimates."""
    results = response.get("results")
    if not isinstance(results, list) or len(results) != len(plan["keywordGroups"]):
        raise DiscoveryError("trend group count mismatch")
    names = {item["groupName"] for item in plan["keywordGroups"]}
    scored = []
    for result in results:
        if not isinstance(result, dict) or result.get("title") not in names:
            raise DiscoveryError("unexpected trend group")
        records = result.get("data")
        if not isinstance(records, list) or len(records) < 4:
            raise DiscoveryError("at least 4 periods required to rank trends")
        values = []
        for record in records:
            if not isinstance(record, dict):
                raise DiscoveryError("invalid trend row")
            ratio = record.get("ratio")
            if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not 0 <= ratio <= 100:
                raise DiscoveryError("relative ratio must be 0..100")
            values.append(float(ratio))
        latest = sum(values[-2:]) / 2
        earlier = sum(values[-4:-2]) / 2
        # Do not make a spiky single period automatically win the cohort.
        index = round(0.75 * latest + 0.25 * max(0.0, min(100.0, 50 + latest - earlier)), 2)
        scored.append({"query": result["title"],
                       "relative_current_interest": round(latest, 2),
                       "relative_previous_interest": round(earlier, 2),
                       "momentum": round(latest - earlier, 2),
                       "relative_opportunity_index": index})
    if len({x["query"] for x in scored}) != len(scored):
        raise DiscoveryError("duplicate returned trend series")
    return {
        "source": "NAVER Datalab public search trend API",
        "source_url": "https://developers.naver.com/docs/serviceapi/datalab/search/search.md",
        "date_window": [plan["startDate"], plan["endDate"]],
        "query_cohort": sorted(names),
        "absolute_volume": None, "estimated_clicks": None,
        "competition": None,
        "comparability": "RELATIVE_IN_THIS_RESPONSE_ONLY",
        "requires_original_source_validation_before_writing": True,
        "ranked": sorted(scored, key=lambda x: (-x["relative_opportunity_index"], x["query"])),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Naver 5-topic relative discovery")
    parser.add_argument("--topics", required=True, type=Path,
                        help='JSON: {"topics":[{"query": "...","category":"economy"}, ...]}')
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--execute", action="store_true",
                        help="explicitly make one read-only Datalab request")
    parser.add_argument("--mock-response", type=Path,
                        help="offline mock data only, never label as live trend")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        topic_groups = json.loads(args.topics.read_text(encoding="utf-8"))["topics"]
        plan = make_plan(topic_groups, args.start, args.end)
        if args.execute and args.mock_response:
            raise DiscoveryError("mock and live execute cannot be combined")
        if args.out.exists() or args.out.is_symlink():
            raise DiscoveryError("output exists; refuse overwrite")
        if not args.out.parent.is_dir():
            raise DiscoveryError("parent directory missing")
        if args.execute:
            payload = score_trends(plan, _post(plan))
            payload["live_request_executed"] = True
        elif args.mock_response:
            mock = json.loads(args.mock_response.read_text(encoding="utf-8"))
            payload = score_trends(plan, mock)
            payload["live_request_executed"] = False
            payload["data_provenance"] = "MOCK_NOT_NAVER_DATA"
        else:
            payload = {"query_plan": plan, "status": "PLAN_ONLY",
                       "live_request_executed": False,
                       "relative_interest": None}
        with args.out.open("x", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(json.dumps({"out": str(args.out), "status": payload.get("status", "RANKED"),
                          "live_request_executed": payload["live_request_executed"]},
                         ensure_ascii=False))
        return 0
    except (DiscoveryError, OSError, ValueError, KeyError, TypeError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

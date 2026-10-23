"""Bounded, opt-in, read-only NAVER API HUB blog search discovery.

Never treat search snippets as verified article facts. Never store API keys.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from tools.blog_v2 import ContentError, iso_date, safe_text, verified_url

API_ENDPOINT = "https://naverapihub.apigw.ntruss.com/search/v1/blog"
MAX_BYTES = 1_000_000


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ContentError("NAVER API HUB redirected; refusing credential forwarding")


def search_request(query: str, limit: int) -> urllib.request.Request:
    query = safe_text(query, "query", maximum=120)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
        raise ContentError("limit must be 1..10 (one bounded request)")
    params = urllib.parse.urlencode({"query": query, "display": limit, "start": 1, "sort": "date", "format": "json"})
    return urllib.request.Request(API_ENDPOINT + "?" + params, headers={"Accept": "application/json"}, method="GET")


def normalize_response(raw: object, query: str, as_of: str, limit: int) -> dict:
    cutoff = iso_date(as_of, "as_of")
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        raise ContentError("invalid NAVER search response")
    result: list[dict] = []
    for item in raw["items"][:limit]:
        if not isinstance(item, dict):
            continue
        try:
            raw_date = safe_text(item.get("postdate"), "postdate", maximum=8)
            if not re.fullmatch(r"\d{8}", raw_date):
                continue
            postdate = date.fromisoformat(f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}")
            if postdate > cutoff:
                continue
            url = verified_url(item.get("link"))
            title = safe_text(html.unescape(re.sub(r"<[^>]+>", "", str(item.get("title", "")))), "title", maximum=200)
            excerpt = html.unescape(re.sub(r"<[^>]+>", "", str(item.get("description", ""))))[:400]
        except (ValueError, ContentError):
            continue
        result.append({"title": title, "url": url, "postdate": postdate.isoformat(),
                       "snippet": excerpt, "evidence_level": "unverified_snippet",
                       "suitable_for_claims": False})
    return {"source": "NAVER_API_HUB_BLOG_SEARCH", "query": query, "as_of": as_of,
            "result_count": len(result), "items": result, "read_only": True,
            "source_verification_required": True}


def fetch_search(query: str, limit: int, as_of: str, *, network_authorized: bool,
                 env: dict[str, str] | None = None, opener=None) -> dict:
    """One request, only to a fixed NAVER API HUB host, never retries or follows redirects."""
    request = search_request(query, limit)
    iso_date(as_of, "as_of")
    if not network_authorized:
        raise ContentError("network call requires explicit --execute authorization")
    secrets = env if env is not None else os.environ
    client_id = secrets.get("NAVER_API_HUB_CLIENT_ID", "")
    client_secret = secrets.get("NAVER_API_HUB_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise ContentError("NAVER API HUB environment credentials are missing")
    request.add_header("X-NCP-APIGW-API-KEY-ID", client_id)
    request.add_header("X-NCP-APIGW-API-KEY", client_secret)
    opener = opener if opener is not None else urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=8) as response:
            if response.status != 200:
                raise ContentError("NAVER API HUB returned non-200 response")
            raw_bytes = response.read(MAX_BYTES + 1)
    except (urllib.error.URLError, OSError) as error:
        raise ContentError("NAVER API HUB request failed (no automatic retry)") from error
    if len(raw_bytes) > MAX_BYTES:
        raise ContentError("NAVER API HUB response exceeds size limit")
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise ContentError("NAVER API HUB returned invalid JSON") from error
    return normalize_response(payload, query, as_of, limit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--execute", action="store_true", help="Explicitly authorize one read-only API call")
    parser.add_argument("--out", type=Path, help="Optional local JSON output (never contains credentials)")
    args = parser.parse_args(argv)
    try:
        request = search_request(args.query, args.limit)
        iso_date(args.as_of, "as_of")
        if not args.execute:
            print(json.dumps({"plan_only": True, "method": "GET", "url": request.full_url,
                              "external_calls": 0, "credential_source": "environment only",
                              "results_are_unverified": True}, ensure_ascii=False))
            return 0
        result = fetch_search(args.query, args.limit, args.as_of, network_authorized=True)
        if args.out:
            with args.out.open("x", encoding="utf-8") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        if args.out:
            print(json.dumps({"result_count": result["result_count"], "out": str(args.out),
                              "source_verification_required": True}, ensure_ascii=False))
        else:
            # Preserve the actual candidates when output to disk was not requested.
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ContentError, FileExistsError, OSError) as error:
        parser.exit(2, message=f"naver-search-v2: {error}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

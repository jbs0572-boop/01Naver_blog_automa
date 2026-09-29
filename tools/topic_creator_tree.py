from __future__ import annotations

import hashlib
import re

from tools.contract_types import JSONMap

_HEADING = re.compile(r'^\s*- heading "(?P<value>.+)" \[level=3\]')
_LINK = re.compile(r'^\s*- link "(?P<value>.+)" \[ref=')
_DEMOGRAPHIC = re.compile(r"^\d{1,2}(?:-\d{1,2}|세-)세? (?:여자|남자)$")


def _keyword_and_badge(value: str) -> tuple[str, str | None]:
    if value.endswith(" new"):
        return value[:-4], "new"
    keyword, separator, possible_number = value.rpartition(" ")
    if separator and possible_number.isdigit():
        return keyword, possible_number
    return value, None


def candidate_observations(tree: str) -> tuple[JSONMap, ...]:
    active = False
    category: str | None = None
    category_rank = 0
    candidates: list[JSONMap] = []
    for line in tree.splitlines():
        if 'link "주제별 인기유입검색어"' in line:
            active = True
            continue
        if not active:
            continue
        heading = _HEADING.match(line)
        if heading is not None:
            value = heading.group("value")
            if _DEMOGRAPHIC.match(value):
                break
            category = value
            category_rank = 0
            continue
        link = _LINK.match(line)
        if link is None or category is None:
            continue
        keyword, badge = _keyword_and_badge(link.group("value"))
        if not keyword:
            continue
        category_rank += 1
        identity = f"{category}\0{category_rank}\0{keyword}".encode()
        candidates.append(
            {
                "keyword": keyword,
                "keyword_text": keyword,
                "rank": category_rank,
                "category_key": category,
                "category_rank": category_rank,
                "candidate_id": hashlib.sha256(identity).hexdigest(),
                "raw_observation": {
                    "keyword_text": keyword,
                    "badge": badge,
                },
                "missing_fields": [],
                "aliases": [],
            }
        )
    return tuple(candidates)


__all__ = ["candidate_observations"]

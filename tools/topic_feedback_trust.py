from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

from tools.contract_types import JSONMap, JSONValue

CURRENT_TERMS_URL: Final = "https://policy.naver.com/rules/service.html"
TRUSTED_TERMS_CHECKED_AT: Final = "2026-09-08"
TRUSTED_TERMS_EFFECTIVE_DATE: Final = "2025-07-10"
_SECRET_KEY: Final = re.compile(
    r"(?:cookie|client_secret|access[_-]?key|password|authorization|token|api[_-]?key|session|credential)"
)
_PROFILE_KEY: Final = re.compile(
    r"(?:profile[_-]?(?:dir|path)|(?:browser|chrome|chromium|edge|firefox|safari)[_-]?(?:profile|path|dir)|user[_-]?data(?:[_-]?(?:dir|path))?)"
)
_SECRET_VALUE: Final = re.compile(
    r"(?:\b(?:sk|pk)-[a-z0-9_-]{8,}|\bbearer\s+|\bnid_(?:aut|ses)\b|(?:client_secret|access[_-]?key|password|cookie|token)\s*=)"
)
_PROFILE_VALUE: Final = re.compile(
    r"(?:^~(?:/|$)|^[a-z]:/|^/(?:users/[^/]+/)?library/safari(?:/|$)|application support/.+(?:chrome|chromium|edge|firefox|mozilla|safari)|appdata/.+(?:chrome|chromium|edge|firefox|mozilla|safari)|/\.config/(?:google-chrome|chromium|microsoft-edge|mozilla|firefox)|/\.mozilla/firefox/[^/]+\.default-release(?:/|$)|\bprofile\s+\d+\b|\b[\w-]+\.default-release\b|\buser[-_ ]?data[-_ ]?(?:dir|path)\b)"
)


@dataclass(frozen=True, slots=True)
class TrustedSourceIdentity:
    display_name: str
    official_url: str | None
    terms_checked_at: str | None


_TRUSTED_SOURCE_IDENTITIES: Final = {
    "naver-datalab": TrustedSourceIdentity(
        "NAVER DataLab",
        "https://developers.naver.com/docs/serviceapi/datalab/search/search.md",
        TRUSTED_TERMS_CHECKED_AT,
    ),
    "naver-search-ads-keyword-tool": TrustedSourceIdentity(
        "NAVER Search Ads Keyword Tool",
        "https://naver.github.io/searchad-apidoc/",
        TRUSTED_TERMS_CHECKED_AT,
    ),
    "blackkiwi": TrustedSourceIdentity(
        "BlackKiwi", "https://blackkiwi.notion.site/36111283133e80cca0c3ff3f757af7de", None
    ),
    "ecommerce-ai-extension": TrustedSourceIdentity("Ecommerce AI Extension", None, None),
    "datalab-tools-helper": TrustedSourceIdentity(
        "DataLab Tools Helper",
        "https://chromewebstore.google.com/detail/%EB%8D%B0%EC%9D%B4%ED%84%B0%EB%9E%A9%ED%88%B4%EC%A6%88-%ED%97%AC%ED%8D%BC/ldoknfkedngbdfgdkeicojmhnojgpdcb?hl=ko",
        None,
    ),
    "n-supporter": TrustedSourceIdentity(
        "N Supporter", "https://www.reviewerns.com/portal/lab/nsupporter/", None
    ),
    "daglo": TrustedSourceIdentity(
        "Daglo", "https://daglo.ai/d/ko/guide/getting-started/what-is-daglo", None
    ),
    "chatgpt-codex-structuring": TrustedSourceIdentity(
        "ChatGPT", "https://openai.com/", None
    ),
    "naver-blog-statistics": TrustedSourceIdentity(
        "NAVER Blog Statistics",
        "https://help.naver.com/service/5593/category/3108?lang=ko",
        TRUSTED_TERMS_CHECKED_AT,
    ),
    "missing-tenth-tool": TrustedSourceIdentity("Missing tenth tool", None, None),
}


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().replace("\\", "/")


def contains_sensitive_config_data(value: JSONValue) -> bool:
    if isinstance(value, str):
        normalized = _normalized(value)
        return _SECRET_VALUE.search(normalized) is not None or _PROFILE_VALUE.search(normalized) is not None
    if isinstance(value, list):
        return any(contains_sensitive_config_data(item) for item in value)
    if isinstance(value, dict):
        return any(
            _SECRET_KEY.search(_normalized(key)) is not None
            or _PROFILE_KEY.search(_normalized(key)) is not None
            or contains_sensitive_config_data(item)
            for key, item in value.items()
        )
    return False


def matches_trusted_source_identity(raw: JSONMap) -> bool:
    source_id = raw.get("id")
    if not isinstance(source_id, str):
        return False
    identity = _TRUSTED_SOURCE_IDENTITIES.get(source_id)
    return (
        identity is not None
        and raw.get("display_name") == identity.display_name
        and raw.get("official_url") == identity.official_url
        and raw.get("terms_checked_at") == identity.terms_checked_at
    )

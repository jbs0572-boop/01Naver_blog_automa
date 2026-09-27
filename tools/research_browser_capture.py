from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Final
from urllib.parse import parse_qs, quote, urlparse
from zoneinfo import ZoneInfo

from tools.aside_browser import AsideCliConfig, AsideReplSession, resolve_aside_cli
from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.research_capture_policy import load_policy
from tools.research_capture_store import (
    append_capture,
    completed_capture,
    file_digest,
    load_ledger,
    persist_raw_capture,
    reserve_capture_attempt,
)
from tools.research_crawler_bridge import load_source_profiles, profile_for_url

_KST: Final = ZoneInfo("Asia/Seoul")


def capture_research_browser(keyword: str) -> JSONMap:
    policy = load_policy()
    profiles = tuple(
        profile
        for profile in load_source_profiles()
        if profile.kind in {"official", "supporting"}
    )
    profile_payload = [
        {"host": profile.host, "kind": profile.kind, "source_id": profile.source_id}
        for profile in profiles
    ]
    selection = Path("research") / f"topic-selection-{keyword}.md"
    seed_urls = (
        re.findall(r"https?://[^\s)\]>]+", selection.read_text(encoding="utf-8"))
        if selection.is_file()
        else []
    )
    operation = f"""
const url = 'https://search.naver.com/search.naver?query={quote(keyword)}';
const profiles = {json.dumps(profile_payload, ensure_ascii=True)};
const seedUrls = {json.dumps(seed_urls, ensure_ascii=True)};
const documentReadTimeoutMs = {policy.read_timeout_seconds * 1000};
const documentMaxAttempts = {policy.same_url_retry_limit + 1};
const withDocumentReadTimeout = async (read) => {{
  let timeoutId;
  try {{
    return await new Promise((resolve, reject) => {{
      timeoutId = setTimeout(
        () => reject(new Error('document read timeout')),
        documentReadTimeoutMs,
      );
      Promise.resolve(read()).then(resolve, reject);
    }});
  }} finally {{
    clearTimeout(timeoutId);
  }}
}};
const isTransientReadError = (error) => {{
  const name = String(error?.name ?? '');
  const message = String(error?.message ?? error).toLowerCase();
  return name === 'TimeoutError' || /timeout|temporary|network|net::err|econnreset|eai_again/.test(message);
}};
const readOriginal = async (requested, profile) => {{
  const opened = Promise.resolve().then(() => openTab(requested.href));
  let document;
  try {{
    document = await withDocumentReadTimeout(() => opened);
  }} catch (error) {{
    void opened.then((tab) => closeTab(tab), () => undefined);
    throw error;
  }}
  try {{
    const documentSnap = await withDocumentReadTimeout(() => snapshot(document));
    const finalUrl = document.url();
    let finalHost = '';
    try {{ finalHost = new URL(finalUrl).host; }} catch {{ finalHost = ''; }}
    const finalProfile = profiles.find(value => value.host === finalHost && value.kind === profile.kind);
    return {{
      requested_keyword: {json.dumps(keyword, ensure_ascii=True)},
      requested_url: requested.href,
      source_url: finalUrl,
      source_kind: profile.kind === 'official' ? 'official_document' : 'supporting_document',
      source_profile_id: finalProfile?.source_id ?? profile.source_id,
      tree: documentSnap.tree,
      result: finalProfile ? 'observed' : 'redirect_blocked'
    }};
  }} finally {{
    await closeTab(document);
  }}
}};
const tabs = await listBrowserTabs();
const existing = tabs.find(tab => tab.url.startsWith('https://search.naver.com/search.naver'));
const target = existing ? (getTabByTargetId(existing.targetId) ?? await attachBrowserTab(existing.targetId)) : await openTab(url);
if (target.url() !== url) await target.goto(url);
const snap = await snapshot(target);
const hrefs = [...seedUrls, ...await target.locator('a[href]').evaluateAll(nodes => nodes.map(node => node.href).filter(Boolean))];
const documents = [];
const documentFailures = [];
const seen = new Set();
const laneCandidates = {{ official: [], supporting: [] }};
let unprofiledCandidates = 0;
for (const requestedUrl of hrefs) {{
  let requested;
  try {{ requested = new URL(requestedUrl); }} catch {{ continue; }}
  const profile = profiles.find(value => value.host === requested.host);
  if (!profile) {{ unprofiledCandidates += 1; continue; }}
  if (seen.has(requested.href)) continue;
  seen.add(requested.href);
  laneCandidates[profile.kind].push({{ requested, profile }});
}}
for (const lane of ['official', 'supporting']) {{
  for (const {{ requested, profile }} of laneCandidates[lane]) {{
    if (documents.length >= {policy.default_documents}) break;
    for (let attempt = 1; attempt <= documentMaxAttempts; attempt += 1) {{
      try {{
        documents.push(await readOriginal(requested, profile));
        break;
      }} catch (error) {{
        const transient = isTransientReadError(error);
        if (!transient || attempt === documentMaxAttempts) {{
          documentFailures.push({{
            requested_keyword: {json.dumps(keyword, ensure_ascii=True)},
            requested_url: requested.href,
            source_url: requested.href,
            final_url: null,
            source_kind: profile.kind === 'official' ? 'official_document' : 'supporting_document',
            source_profile_id: profile.source_id,
            result: 'failed',
            attempts: attempt,
            limitation: transient ? 'read_retry_exhausted' : 'read_failure_not_retryable'
          }});
          break;
        }}
      }}
    }}
  }}
}}
const limitations = [
  ...(unprofiledCandidates ? [{{"reason": "source_profile_unverified", "candidate_count": unprofiledCandidates}}] : []),
  ...(documentFailures.length ? [{{"reason": "document_read_failed", "documents": documentFailures}}] : []),
];
console.log('__ASIDE_RESULT__' + JSON.stringify({{"requested_keyword": {json.dumps(keyword, ensure_ascii=True)}, "requested_url": url, "source_url": target.url(), "tree": snap.tree, "document_observations": documents, "document_failures": documentFailures, "limitations": limitations}}));
"""
    session = AsideReplSession(
        AsideCliConfig(resolve_aside_cli(), timeout_seconds=policy.host_budget_seconds)
    )
    try:
        result: JSONValue = session.run_json(operation)
        if not isinstance(result, dict):
            raise ContractError("자료조사 브라우저 관찰 결과가 객체가 아닙니다.")
        return result
    finally:
        session.close()


def _capture(keyword: str) -> JSONMap:
    return capture_research_browser(keyword)


def capture_research_sources(
    keyword: str,
    root: Path | None = None,
    run_id: str | None = None,
    as_of_date: str | None = None,
    selection_path: Path | None = None,
) -> JSONMap:
    policy_path = (
        (root / "config/research-capture-policy.json")
        if root
        else Path("config/research-capture-policy.json")
    )
    profile_path = (
        (root / "config/research-source-profiles.json")
        if root
        else Path("config/research-source-profiles.json")
    )
    policy = load_policy(policy_path)
    binding: JSONMap = {
        "keyword": keyword,
        "as_of_date": as_of_date or "unspecified",
        "selection_input_digest": (
            file_digest(selection_path) if selection_path is not None else "unspecified"
        ),
        "policy_digest": file_digest(policy_path),
        "profile_digest": file_digest(profile_path),
    }
    if policy.max_searches < 1:
        raise ContractError("research capture search budget is zero")
    reservation_capture_id: str | None = None
    if root is not None and run_id is not None:
        prior = load_ledger(root, run_id)
        completed = completed_capture(prior, binding)
        if completed is not None:
            return completed
        reservation = reserve_capture_attempt(root, run_id, binding, policy.max_searches)
        capture_id = reservation.get("capture_id")
        if not isinstance(capture_id, str):
            raise ContractError("research capture reservation is malformed")
        reservation_capture_id = capture_id
    try:
        observation = _capture(keyword)
    except ContractError as error:
        raise ContractError(
            f"Aside 연결 또는 자료조사 페이지 접근 실패: {error}. 모델은 호출하지 않았습니다."
        ) from error
    if not isinstance(observation.get("source_url"), str) or not isinstance(
        observation.get("tree"), str
    ):
        raise ContractError(
            "자료조사 브라우저 관찰 결과가 비어 있습니다. 모델은 호출하지 않았습니다."
        )
    source_url = observation["source_url"]
    requested_keyword = observation.get("requested_keyword")
    requested_url = observation.get("requested_url")
    if (
        not isinstance(source_url, str)
        or requested_keyword != keyword
        or not isinstance(requested_url, str)
        or parse_qs(urlparse(source_url).query).get("query") != [keyword]
        or parse_qs(urlparse(requested_url).query).get("query") != [keyword]
    ):
        raise ContractError(
            "자료조사 브라우저가 요청한 정확한 검색어를 표시하지 않습니다. 모델은 호출하지 않았습니다."
        )
    search_observation: JSONMap = {
        **observation,
        "source_kind": "search_results",
        "evidence_status": "observed",
    }
    raw_documents = observation.get("document_observations", [])
    if not isinstance(raw_documents, list) or len(raw_documents) > policy.max_documents:
        raise ContractError("research capture document budget exceeded")
    documents: list[JSONValue] = []
    profiles = load_source_profiles(profile_path)
    for raw_document in raw_documents:
        if not isinstance(raw_document, dict):
            raise ContractError("research document observation is malformed")
        if raw_document.get("source_kind") not in {
            "official_document",
            "supporting_document",
        }:
            raise ContractError("research document source_kind must be explicit")
        document_url = raw_document.get("source_url")
        requested_document_url = raw_document.get("requested_url", document_url)
        document_tree = raw_document.get("tree")
        if (
            not isinstance(document_url, str)
            or not isinstance(requested_document_url, str)
            or not isinstance(document_tree, str)
        ):
            raise ContractError("research document observation is incomplete")
        if not document_tree.strip():
            raise ContractError("research document observation is incomplete")
        requested_profile = profile_for_url(requested_document_url, profiles)
        final_profile = profile_for_url(document_url, profiles)
        if (
            requested_profile.source_id != final_profile.source_id
            or raw_document.get("result", "observed") != "observed"
        ):
            raise ContractError(
                "research document redirect is outside its configured source profile"
            )
        documents.append(
            {
                **raw_document,
                "requested_keyword": keyword,
                "requested_url": requested_document_url,
                "source_profile_id": requested_profile.source_id,
                "evidence_status": "observed",
            }
        )
    observations: list[JSONValue] = [search_observation, *documents]
    result: JSONMap = {
        "capture_id": "RAW-" + uuid.uuid4().hex,
        "captured_at": datetime.now(_KST).isoformat(),
        "timezone": "Asia/Seoul",
        "keyword": keyword,
        "capture_binding": binding,
        "observations": observations,
        "policy": {
            "max_searches": policy.max_searches,
            "max_documents": policy.max_documents,
            "max_social_sources": policy.max_social_sources,
            "host_budget_seconds": policy.host_budget_seconds,
        },
    }
    if reservation_capture_id is not None:
        result["capture_state"] = "completed"
        result["reservation_capture_id"] = reservation_capture_id
    if root is not None and run_id is not None:
        raw_path, raw_digest = persist_raw_capture(root, run_id, result)
        result["raw_evidence_path"] = raw_path
        result["raw_evidence_sha256"] = raw_digest
        _ = append_capture(root, run_id, result)
    return result


def compact_research_evidence(evidence: JSONMap, limit: int = 12000) -> JSONMap:
    compact: JSONMap = dict(evidence)
    raw_observations = evidence.get("observations")
    if not isinstance(raw_observations, list):
        return compact
    observations: list[JSONValue] = []
    for raw in raw_observations:
        if not isinstance(raw, dict):
            continue
        item: JSONMap = dict(raw)
        tree = item.get("tree")
        if isinstance(tree, str) and len(tree) > limit:
            lines = tree.splitlines()
            item["tree"] = "\n".join(
                lines[:40]
                + [
                    line
                    for line in lines[40:]
                    if 'link "' in line or 'heading "' in line
                ][:120]
            )
        observations.append(item)
    compact["observations"] = observations
    return compact


__all__ = [
    "capture_research_browser",
    "capture_research_sources",
    "compact_research_evidence",
]

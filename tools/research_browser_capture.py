from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import struct
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
from tools.research_crawler_bridge import (
    SourceProfile,
    load_source_profiles,
    profile_for_url,
)

_KST: Final = ZoneInfo("Asia/Seoul")
_MAX_MEDIA_BYTES: Final = 4 * 1024 * 1024
_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"


def _image_extension(content_type: str, payload: bytes) -> str | None:
    if content_type == "image/png" and payload.startswith(_PNG_SIGNATURE):
        if (
            len(payload) < 33
            or payload[12:16] != b"IHDR"
            or b"IEND" not in payload[-12:]
        ):
            return None
        width, height = struct.unpack(">II", payload[16:24])
        if width * height > 50_000_000:
            return None
        return ".png"
    if (
        content_type == "image/jpeg"
        and payload.startswith(b"\xff\xd8\xff")
        and payload.endswith(b"\xff\xd9")
    ):
        return ".jpg"
    if (
        content_type == "image/webp"
        and payload.startswith(b"RIFF")
        and payload[8:12] == b"WEBP"
    ):
        return ".webp"
    return None


def _capture_media_candidates(
    raw_document: JSONMap,
    source_kind: str,
    profiles: tuple[SourceProfile, ...],
    root: Path | None,
    run_id: str | None,
) -> list[JSONValue]:
    raw_candidates = raw_document.get("media_candidates", [])
    if not isinstance(raw_candidates, list):
        raise ContractError("research media candidates must be a list")
    source_url = raw_document.get("source_url")
    if not isinstance(source_url, str):
        raise ContractError("research media source page URL is missing")
    source_profile = profile_for_url(source_url, profiles)
    trusted_hosts = {source_profile.host, *source_profile.media_hosts}
    candidates: list[JSONValue] = []
    for value in raw_candidates:
        if not isinstance(value, dict):
            raise ContractError("research media candidate is malformed")
        candidate: JSONMap = {
            key: field for key, field in value.items() if key != "content_base64"
        }
        image_url = value.get("image_url")
        if not isinstance(image_url, str):
            raise ContractError("research media candidate URL is missing")
        parsed_image_url = urlparse(image_url)
        final_image_url = value.get("final_image_url", image_url)
        parsed_final_image_url = (
            urlparse(final_image_url) if isinstance(final_image_url, str) else None
        )
        verified = (
            parsed_image_url.scheme == "https"
            and parsed_image_url.hostname is not None
            and parsed_image_url.hostname.lower() in trusted_hosts
            and parsed_image_url.username is None
            and parsed_image_url.password is None
            and parsed_final_image_url is not None
            and parsed_final_image_url.scheme == "https"
            and parsed_final_image_url.hostname is not None
            and parsed_final_image_url.hostname.lower() in trusted_hosts
            and parsed_final_image_url.username is None
            and parsed_final_image_url.password is None
        )
        candidate["source_page_url"] = source_url
        candidate["source_profile_id"] = source_profile.source_id
        page_host = urlparse(source_url).hostname
        if not verified:
            candidate["provenance_status"] = "unverified_media_host"
        elif parsed_image_url.hostname == page_host:
            candidate["provenance_status"] = f"{source_kind}_same_origin"
        else:
            candidate["provenance_status"] = f"{source_kind}_allowlisted_media_host"
        encoded = value.get("content_base64")
        fetch_status = value.get("fetch_status")
        if not verified or not isinstance(encoded, str) or fetch_status != "downloaded":
            candidate["fetch_status"] = fetch_status or "not_cached"
            candidates.append(candidate)
            continue
        if root is None or run_id is None:
            candidate["fetch_status"] = "not_cached_no_run_context"
            candidates.append(candidate)
            continue
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id) is None:
            raise ContractError("research media run id is unsafe")
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ContractError("research media payload is not valid base64") from error
        if not payload or len(payload) > _MAX_MEDIA_BYTES:
            candidate["fetch_status"] = "over_size_limit"
            candidates.append(candidate)
            continue
        content_type = value.get("content_type")
        extension = (
            _image_extension(content_type, payload)
            if isinstance(content_type, str)
            else None
        )
        width = value.get("width")
        height = value.get("height")
        png_dimensions_match = True
        if extension == ".png" and isinstance(width, int) and isinstance(height, int):
            png_dimensions_match = struct.unpack(">II", payload[16:24]) == (
                width,
                height,
            )
        if (
            extension is None
            or not png_dimensions_match
            or not isinstance(width, int)
            or isinstance(width, bool)
            or not isinstance(height, int)
            or isinstance(height, bool)
            or width < 1
            or height < 1
            or width * height > 50_000_000
        ):
            candidate["fetch_status"] = "invalid_image_payload"
            candidates.append(candidate)
            continue
        digest = hashlib.sha256(payload).hexdigest()
        relative_path = (
            Path(".automation/work")
            / run_id
            / "research-media"
            / f"{digest}{extension}"
        )
        destination = root / relative_path
        parent = root
        for part in relative_path.parts[:-1]:
            parent = parent / part
            if parent.is_symlink():
                raise ContractError("research media cache path cannot contain symlinks")
        _ = destination.parent.mkdir(parents=True, exist_ok=True)
        resolved_root = root.resolve()
        if not destination.resolve().is_relative_to(resolved_root):
            raise ContractError("research media output escaped the project root")
        if destination.exists():
            if (
                destination.is_symlink()
                or hashlib.sha256(destination.read_bytes()).hexdigest() != digest
            ):
                raise ContractError(
                    "existing research media cache does not match its digest"
                )
        else:
            temporary = destination.with_name(
                f".{destination.name}.{uuid.uuid4().hex}.tmp"
            )
            _ = temporary.write_bytes(payload)
            _ = temporary.replace(destination)
        candidate["local_path"] = relative_path.as_posix()
        candidate["sha256"] = f"sha256:{digest}"
        candidate["fetch_status"] = "cached_verified"
        candidates.append(candidate)
    return candidates


def capture_research_browser(keyword: str) -> JSONMap:
    policy = load_policy()
    profiles = tuple(
        profile
        for profile in load_source_profiles()
        if profile.kind in {"official", "supporting"}
    )
    profile_payload = [
        {
            "host": profile.host,
            "kind": profile.kind,
            "media_hosts": list(profile.media_hosts),
            "source_id": profile.source_id,
        }
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
    const allowedMediaHosts = [finalHost, ...(finalProfile?.media_hosts ?? [])];
    const mediaCandidates = await document.locator('img').evaluateAll(async (nodes, trustedHosts) => {{
      const isDecorativePath = /(?:^|[/_-])(?:icon|logo|qr|tile|map)(?:[/_.-]|$)/i;
      const candidates = nodes.map(image => {{
        const rawUrl = image.currentSrc || image.getAttribute('src') || image.getAttribute('data-src') || image.getAttribute('data-original');
        if (!rawUrl) return null;
        let parsed;
        try {{ parsed = new URL(rawUrl, location.href); }} catch {{ return null; }}
        const width = image.naturalWidth || image.width || 0;
        const height = image.naturalHeight || image.height || 0;
        const contentRegion = image.closest('main, article, [role=main], #contents, .contents, .view_cont, .view-content');
        const context = [image.closest('figure')?.innerText, image.parentElement?.innerText, contentRegion?.innerText].find(value => value?.trim()) || '';
        const isRelevantSize = Math.max(width, height) >= 320 && Math.min(width, height) >= 120;
        const dimensionsUnknown = width === 0 || height === 0;
        const isSafeUrl = parsed.protocol === 'https:';
        const isTrustedHost = trustedHosts.includes(parsed.host);
        const isDecorative = isDecorativePath.test(parsed.pathname);
        return {{
          image_url: parsed.href,
          host: parsed.host,
          alt: image.alt || '',
          title: image.title || '',
          width,
          height,
          nearby_text: context.trim().slice(0, 300),
          page_title: window.document.title,
          candidate_status: isSafeUrl && (isRelevantSize || dimensionsUnknown) && !isDecorative ? (isTrustedHost ? 'eligible' : 'unverified_media_host') : 'not_eligible',
        }};
      }}).filter(candidate => candidate && candidate.candidate_status !== 'not_eligible')
        .sort((left, right) => Number(right.candidate_status === 'eligible') - Number(left.candidate_status === 'eligible'))
        .slice(0, 12);
      for (const candidate of candidates.filter(value => value.candidate_status === 'eligible').slice(0, 2)) {{
        try {{
          const response = await fetch(candidate.image_url);
          if (!response.ok) {{ candidate.fetch_status = `http_${{response.status}}`; continue; }}
          const finalImageUrl = new URL(response.url);
          candidate.final_image_url = finalImageUrl.href;
          if (finalImageUrl.protocol !== 'https:' || !trustedHosts.includes(finalImageUrl.host)) {{ candidate.fetch_status = 'redirected_to_unverified_host'; continue; }}
          const contentType = (response.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
          if (!['image/png', 'image/jpeg', 'image/webp'].includes(contentType)) {{ candidate.fetch_status = 'unsupported_content_type'; continue; }}
          const declaredLength = Number(response.headers.get('content-length') || 0);
          if (declaredLength > 4194304) {{ candidate.fetch_status = 'over_size_limit'; continue; }}
          const bytes = new Uint8Array(await response.arrayBuffer());
          if (bytes.byteLength === 0 || bytes.byteLength > 4194304) {{ candidate.fetch_status = 'over_size_limit'; continue; }}
          const bitmap = await createImageBitmap(new Blob([bytes], {{ type: contentType }}));
          candidate.width = bitmap.width;
          candidate.height = bitmap.height;
          bitmap.close();
          if (Math.max(candidate.width, candidate.height) < 320 || Math.min(candidate.width, candidate.height) < 120) {{ candidate.fetch_status = 'image_too_small'; continue; }}
          let binary = '';
          for (let offset = 0; offset < bytes.length; offset += 0x8000) {{
            binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
          }}
          candidate.content_type = contentType;
          candidate.content_base64 = btoa(binary);
          candidate.fetch_status = 'downloaded';
        }} catch (error) {{
          candidate.fetch_status = 'fetch_failed';
        }}
      }}
      for (const candidate of candidates) {{
        if (candidate.candidate_status === 'unverified_media_host') candidate.fetch_status = 'not_downloaded_unverified_host';
      }}
      return candidates;
    }}, allowedMediaHosts);
    return {{
      requested_keyword: {json.dumps(keyword, ensure_ascii=True)},
      requested_url: requested.href,
      source_url: finalUrl,
      source_kind: profile.kind === 'official' ? 'official_document' : 'supporting_document',
      source_profile_id: finalProfile?.source_id ?? profile.source_id,
      tree: documentSnap.tree,
      media_candidates: mediaCandidates,
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
        reservation = reserve_capture_attempt(
            root, run_id, binding, policy.max_searches
        )
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
        **{
            key: value
            for key, value in observation.items()
            if key not in {"document_observations"}
        },
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
        media_candidates = _capture_media_candidates(
            raw_document,
            final_profile.kind,
            profiles,
            root,
            run_id,
        )
        documents.append(
            {
                **raw_document,
                "requested_keyword": keyword,
                "requested_url": requested_document_url,
                "source_profile_id": requested_profile.source_id,
                "evidence_status": "observed",
                "media_candidates": media_candidates,
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

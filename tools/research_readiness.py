from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from tools.contract_types import ContractError


@dataclass(frozen=True, slots=True)
class ResearchReadiness:
    status: str
    source_count: int
    missing: tuple[str, ...]


def assess_research_readiness(path: Path) -> ResearchReadiness:
    if not path.is_file():
        return ResearchReadiness("insufficient", 0, ("research_file",))
    text = path.read_text(encoding="utf-8")
    sources = _source_rows(text)
    claims = _claim_rows(text)
    evidence = _evidence_rows(text)
    missing_list: list[str] = []
    if len(text.strip()) < 200:
        missing_list.append("substantive_notes")
    if not claims:
        missing_list.append("claim_evidence_linkage")
    known_ids = {row[0] for row in sources}
    official_ids = {
        source_id
        for source_id, kind, url, status in sources
        if kind == "official_document" and url and status in {"confirmed", "passed"}
    }
    evidence_ids = {
        source_id
        for source_id, observed_text, captured_at in evidence
        if observed_text and captured_at
    }
    confirmed_ids = {
        source_id
        for source_id, _kind, url, status in sources
        if url and status in {"confirmed", "passed"}
    } & evidence_ids
    if not confirmed_ids:
        missing_list.append("document_evidence")
    if _official_source_required(text) and (
        not official_ids or not (official_ids & evidence_ids)
    ):
        missing_list.append("official_document_evidence")
    for claim_id, source_ids, status in claims:
        if not claim_id or status not in {"confirmed", "passed"}:
            missing_list.append(f"claim:{claim_id or 'unknown'}")
        if not source_ids or not set(source_ids) <= known_ids:
            missing_list.append(f"source_id:{claim_id or 'unknown'}")
        elif not set(source_ids) & confirmed_ids:
            missing_list.append(f"evidence:{claim_id}")
        elif (
            _official_source_required(text)
            and not set(source_ids) & official_ids & evidence_ids
        ):
            missing_list.append(f"official_evidence:{claim_id}")
    return _result(sources, tuple(dict.fromkeys(missing_list)))


def require_research_readiness(path: Path) -> ResearchReadiness:
    result = assess_research_readiness(path)
    if result.status != "ready":
        missing = ",".join(result.missing)
        raise ContractError(f"research readiness insufficient: {missing}")
    return result


def _result(
    sources: list[tuple[str, str, str, str]], missing: tuple[str, ...] | list[str]
) -> ResearchReadiness:
    normalized = tuple(dict.fromkeys(missing))
    return ResearchReadiness(
        "ready" if not normalized else "insufficient", len(sources), normalized
    )


def _official_source_required(text: str) -> bool:
    return bool(
        re.search(r"^official_source_required:\s*(?:true|True)\s*$", text, re.MULTILINE)
    )


def _table_rows(text: str, required: set[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    lines = text.splitlines()
    for index, line in enumerate(lines[:-1]):
        if not line.lstrip().startswith("|"):
            continue
        headers = [part.strip().lower() for part in line.strip().strip("|").split("|")]
        separator = lines[index + 1].strip().strip("|").split("|")
        if (
            not required <= set(headers)
            or not separator
            or not all("-" in part for part in separator)
        ):
            continue
        for row in lines[index + 2 :]:
            if not row.lstrip().startswith("|"):
                break
            values = [part.strip() for part in row.strip().strip("|").split("|")]
            if len(values) == len(headers):
                rows.append(dict(zip(headers, values, strict=True)))
    return rows


def _source_rows(text: str) -> list[tuple[str, str, str, str]]:
    return [
        (
            row.get("source_id", ""),
            row.get("source_kind", ""),
            row.get("url", ""),
            row.get("evidence_status", ""),
        )
        for row in _table_rows(
            text, {"source_id", "source_kind", "url", "evidence_status"}
        )
    ]


def _claim_rows(text: str) -> list[tuple[str, tuple[str, ...], str]]:
    result: list[tuple[str, tuple[str, ...], str]] = []
    for row in _table_rows(text, {"claim_id", "source_ids", "evidence_status"}):
        source_ids = tuple(
            item.strip(" []`")
            for item in re.split(r"[,;]", row.get("source_ids", ""))
            if item.strip(" []`")
        )
        result.append(
            (row.get("claim_id", ""), source_ids, row.get("evidence_status", ""))
        )
    return result


def _evidence_rows(text: str) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    current: dict[str, str] = {}
    for line in text.splitlines() + [""]:
        match = re.match(r"\s*-?\s*(source_id|observed_text|captured_at):\s*(.+)", line)
        if match:
            if match.group(1) == "source_id" and current.get("source_id"):
                result.append(
                    (
                        current.get("source_id", ""),
                        current.get("observed_text", ""),
                        current.get("captured_at", ""),
                    )
                )
                current = {}
            current[match.group(1)] = match.group(2).strip(" `")
        elif current:
            result.append(
                (
                    current.get("source_id", ""),
                    current.get("observed_text", ""),
                    current.get("captured_at", ""),
                )
            )
            current = {}
    return result


__all__ = [
    "ResearchReadiness",
    "assess_research_readiness",
    "require_research_readiness",
]

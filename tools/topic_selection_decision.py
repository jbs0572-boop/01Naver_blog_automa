from __future__ import annotations

import fcntl
import hashlib
import json
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from tools.contract_types import ContractError, JSONValue
from tools.topic_feedback_scoring_models import AuxiliarySignal
from tools.topic_metadata import (
    CreatorAdvisorSnapshot,
    normalize_keyword,
)
from tools.topic_selection_history import (
    completed_selection_history,
    nonproduction_selection_keywords,
)
from tools.topic_selection_models import (
    DECISION_SCHEMA_VERSION,
    CandidateExclusion,
    TopicSelectionDecision,
    TopicSelectionHistory,
    decision_digest,
)


def decide_topic(
    snapshot: CreatorAdvisorSnapshot,
    excluded_keywords: tuple[str, ...],
    excluded_aliases: tuple[str, ...],
    history: tuple[TopicSelectionHistory, ...],
    *,
    run_id: str = "unreserved",
    snapshot_path: str = "",
    snapshot_digest: str = "",
    created_at: str = "",
    signals: tuple[AuxiliarySignal, ...] = (),
    selection_mode: Literal["baseline", "shadow", "active"] = "baseline",
    score_version: str | None = None,
    score_config_digest: str | None = None,
    feedback_manifest_digest: str | None = None,
) -> TopicSelectionDecision:
    from tools.topic_selection_policy import decide_topic as decide

    return decide(
        snapshot, excluded_keywords, excluded_aliases, history,
        run_id=run_id, snapshot_path=snapshot_path, snapshot_digest=snapshot_digest,
        created_at=created_at, signals=signals, selection_mode=selection_mode,
        score_version=score_version, score_config_digest=score_config_digest,
        feedback_manifest_digest=feedback_manifest_digest,
    )


def _parse_decision(value: JSONValue) -> TopicSelectionDecision:
    if not isinstance(value, dict) or value.get("schema_version") != DECISION_SCHEMA_VERSION:
        raise ContractError("selection decision schema is invalid")
    exclusions_value = value.get("exclusions")
    if not isinstance(exclusions_value, list):
        raise ContractError("selection decision exclusions are invalid")
    exclusions: list[CandidateExclusion] = []
    for item in exclusions_value:
        if not isinstance(item, dict):
            raise ContractError("selection decision exclusion is invalid")
        keyword, reason = item.get("keyword"), item.get("reason")
        if not isinstance(keyword, str) or not isinstance(reason, str):
            raise ContractError("selection decision exclusion is invalid")
        exclusions.append(CandidateExclusion(keyword, reason))
    unsigned = dict(value)
    digest = unsigned.pop("digest", None)
    if not isinstance(digest, str) or digest != decision_digest(unsigned):
        raise ContractError("selection decision digest is invalid")
    required_strings = (
        "run_id", "as_of_date", "selected_category", "selected_keyword",
        "selected_candidate_id", "snapshot_path", "snapshot_sha256",
        "selection_policy_version", "ranking_policy_version", "exclusions_digest", "category_history_digest",
        "created_at",
    )
    if any(not isinstance(value.get(field), str) for field in required_strings):
        raise ContractError("selection decision fields are invalid")
    count, rank = value.get("category_selection_count"), value.get("selected_category_rank")
    trend, last = value.get("selected_trend_index"), value.get("category_last_selected_at")
    if not isinstance(count, int) or not isinstance(rank, int):
        raise ContractError("selection decision ranking fields are invalid")
    if trend is not None and not isinstance(trend, int | float):
        raise ContractError("selection decision trend is invalid")
    if last is not None and not isinstance(last, str):
        raise ContractError("selection decision history is invalid")
    score_version_value = value.get("score_version")
    score_config_value = value.get("score_config_digest")
    feedback_manifest_value = value.get("feedback_manifest_digest")
    return TopicSelectionDecision(
        str(value["run_id"]), str(value["as_of_date"]), str(value["selected_category"]),
        str(value["selected_keyword"]), str(value["selected_candidate_id"]),
        str(value["snapshot_path"]), str(value["snapshot_sha256"]),
        str(value["selection_policy_version"]), str(value["ranking_policy_version"]),
        score_version_value if isinstance(score_version_value, str) else None,
        score_config_value if isinstance(score_config_value, str) else None,
        feedback_manifest_value if isinstance(feedback_manifest_value, str) else None,
        tuple(exclusions),
        str(value["exclusions_digest"]), str(value["category_history_digest"]), count,
        last, rank, float(trend) if trend is not None else None,
        str(value["created_at"]), digest,
    )


def read_selection_decision(path: Path) -> TopicSelectionDecision:
    try:
        return _parse_decision(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError("selection decision is unreadable") from error


@contextmanager
def _reservation_lock(root: Path) -> Generator[None, None, None]:
    directory = root / ".automation"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "topic-selection.lock").open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _reservation_path(root: Path, run_id: str) -> Path:
    run_key = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    return root / ".automation" / "topic-selection-reservations" / f"{run_key}.json"


def _write_decision(path: Path, decision: TopicSelectionDecision) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    _ = temporary.write_text(
        json.dumps(decision.as_json(), ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _ = temporary.replace(path)


def _reservation_decisions(root: Path) -> tuple[TopicSelectionDecision, ...]:
    directory = root / ".automation" / "topic-selection-reservations"
    return tuple(read_selection_decision(path) for path in sorted(directory.glob("*.json")))


def _project_output_keywords(
    root: Path, nonproduction_keywords: tuple[str, ...]
) -> tuple[str, ...]:
    values: list[str] = []
    research = root / "research"
    if research.is_dir():
        values.extend(
            path.name[len("topic-selection-") : -len(".md")]
            for path in research.glob("topic-selection-*.md")
        )
    drafts = root / "drafts"
    if drafts.is_dir():
        values.extend(path.stem for path in drafts.glob("*.md"))
    final = root / "final"
    if final.is_dir():
        values.extend(
            path.stem
            for path in final.glob("*.md")
            if not path.stem.endswith(("-naver-layout", "-naver-copy", "-naver-input"))
        )
    placeholders = {"auto-topic", "자동 주제"}
    omitted = {
        *placeholders,
        *(normalize_keyword(value) for value in nonproduction_keywords),
    }
    return tuple(value for value in values if normalize_keyword(value) not in omitted)


def reserve_topic_decision(
    *,
    root: Path,
    work_dir: Path,
    run_id: str,
    snapshot_path: Path,
    snapshot: CreatorAdvisorSnapshot,
    created_at: str,
    excluded_keywords: tuple[str, ...] = (),
    excluded_aliases: tuple[str, ...] = (),
    history: tuple[TopicSelectionHistory, ...] = (),
    signals: tuple[AuxiliarySignal, ...] = (),
    selection_mode: Literal["baseline", "shadow", "active"] = "baseline",
    score_version: str | None = None,
    score_config_digest: str | None = None,
    feedback_manifest_digest: str | None = None,
) -> TopicSelectionDecision:
    destination = work_dir / "selection-decision.json"
    reservation = _reservation_path(root, run_id)
    with _reservation_lock(root):
        if destination.is_file():
            decision = read_selection_decision(destination)
            if decision.run_id != run_id:
                raise ContractError("selection decision run identity is invalid")
            if not reservation.is_file():
                _write_decision(reservation, decision)
            return decision
        if reservation.is_file():
            decision = read_selection_decision(reservation)
            if decision.run_id != run_id:
                raise ContractError("selection reservation run identity is invalid")
            _write_decision(destination, decision)
            return decision
        reservations = _reservation_decisions(root)
        nonproduction_keywords = nonproduction_selection_keywords(root, reservations)
        nonproduction_keys = {
            normalize_keyword(value) for value in nonproduction_keywords
        }
        reserved_keywords = tuple(
            item.selected_keyword
            for item in reservations
            if item.run_id != run_id
            and normalize_keyword(item.selected_keyword) not in nonproduction_keys
        )
        discovered_history = completed_selection_history(root, run_id, reservations)
        history_by_run = {
            item.run_id: item for item in (*discovered_history, *history)
        }
        decision = decide_topic(
            snapshot,
            (
                *excluded_keywords,
                *_project_output_keywords(root, nonproduction_keywords),
                *reserved_keywords,
            ),
            excluded_aliases,
            tuple(history_by_run.values()),
            run_id=run_id,
            snapshot_path=snapshot_path.resolve().relative_to(root.resolve()).as_posix(),
            snapshot_digest="sha256:" + hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
            created_at=created_at,
            signals=signals,
            selection_mode=selection_mode,
            score_version=score_version,
            score_config_digest=score_config_digest,
            feedback_manifest_digest=feedback_manifest_digest,
        )
        _write_decision(reservation, decision)
        _write_decision(destination, decision)
        return decision


def verify_selection_output(decision: TopicSelectionDecision, resolved_keyword: str) -> None:
    if resolved_keyword != decision.selected_keyword:
        message = (
            f"selection_decision_mismatch: expected={decision.selected_keyword!r} "
            f"actual={resolved_keyword!r} decision_digest={decision.digest}"
        )
        raise ContractError(message)


__all__ = [
    "CandidateExclusion", "TopicSelectionDecision", "TopicSelectionHistory",
    "decide_topic", "read_selection_decision", "reserve_topic_decision",
    "verify_selection_output",
]

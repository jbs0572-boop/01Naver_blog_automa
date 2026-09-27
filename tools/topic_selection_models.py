from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final

from tools.contract_types import JSONMap, JSONValue

SELECTION_POLICY_VERSION: Final = "category-balanced-v1"
DECISION_SCHEMA_VERSION: Final = "selection-decision-v1"


@dataclass(frozen=True, slots=True)
class TopicSelectionHistory:
    run_id: str
    category: str
    selected_at: str
    provenance: str
    completed: bool


@dataclass(frozen=True, slots=True)
class CandidateExclusion:
    keyword: str
    reason: str

    def as_json(self) -> JSONMap:
        return {"keyword": self.keyword, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class TopicSelectionDecision:
    run_id: str
    as_of_date: str
    selected_category: str
    selected_keyword: str
    selected_candidate_id: str
    snapshot_path: str
    snapshot_sha256: str
    selection_policy_version: str
    ranking_policy_version: str
    score_version: str | None
    score_config_digest: str | None
    feedback_manifest_digest: str | None
    exclusions: tuple[CandidateExclusion, ...]
    exclusions_digest: str
    category_history_digest: str
    category_selection_count: int
    category_last_selected_at: str | None
    selected_category_rank: int
    selected_trend_index: float | None
    created_at: str
    digest: str

    def as_json(self) -> JSONMap:
        return {
            "schema_version": DECISION_SCHEMA_VERSION,
            "run_id": self.run_id,
            "as_of_date": self.as_of_date,
            "selected_category": self.selected_category,
            "selected_keyword": self.selected_keyword,
            "selected_candidate_id": self.selected_candidate_id,
            "snapshot_path": self.snapshot_path,
            "snapshot_sha256": self.snapshot_sha256,
            "selection_policy_version": self.selection_policy_version,
            "ranking_policy_version": self.ranking_policy_version,
            "score_version": self.score_version,
            "score_config_digest": self.score_config_digest,
            "feedback_manifest_digest": self.feedback_manifest_digest,
            "exclusions": [item.as_json() for item in self.exclusions],
            "exclusions_digest": self.exclusions_digest,
            "category_history_digest": self.category_history_digest,
            "category_selection_count": self.category_selection_count,
            "category_last_selected_at": self.category_last_selected_at,
            "selected_category_rank": self.selected_category_rank,
            "selected_trend_index": self.selected_trend_index,
            "created_at": self.created_at,
            "digest": self.digest,
        }


def decision_digest(value: JSONValue) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


__all__ = [
    "DECISION_SCHEMA_VERSION",
    "SELECTION_POLICY_VERSION",
    "CandidateExclusion",
    "TopicSelectionDecision",
    "TopicSelectionHistory",
    "decision_digest",
]

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from tools.codex_topic_selection import SelectionEvidence
from tools.contract_types import ContractError
from tools.runner_types import StageExecutionContext
from tools.topic_feedback_pinning import signals_for_pinned_context
from tools.topic_metadata import read_snapshot
from tools.topic_selection_decision import reserve_topic_decision
from tools.topic_selection_models import TopicSelectionDecision


@dataclass(frozen=True, slots=True)
class PreparedSelection:
    evidence: SelectionEvidence | None
    decision: TopicSelectionDecision | None


def prepare_selection(
    context: StageExecutionContext, evidence: SelectionEvidence | None
) -> PreparedSelection:
    selection = context.selection_context
    if (
        context.stage != "topic-selector"
        or context.keyword is not None
        or selection is None
        or evidence is None
        or not evidence.from_context
    ):
        return PreparedSelection(evidence, None)
    snapshot_path = context.root / evidence.relative_path
    if selection.capture_id is None:
        raise ContractError("batch snapshot context is incomplete")
    snapshot = read_snapshot(
        snapshot_path,
        expected_capture_id=selection.capture_id,
        expected_as_of_date=selection.as_of_date,
    )
    if not any(candidate.category_key for candidate in snapshot.candidates):
        return PreparedSelection(evidence, None)
    decision = reserve_topic_decision(
        root=context.root,
        work_dir=context.work_dir,
        run_id=context.run_id,
        snapshot_path=snapshot_path,
        snapshot=snapshot,
        created_at=datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
        excluded_keywords=selection.excluded_keywords,
        signals=(
            signals_for_pinned_context(context.root, selection)
            if selection.score_version is not None
            else ()
        ),
        selection_mode=selection.feedback_selection_mode or "baseline",
        score_version=selection.score_version,
        score_config_digest=selection.score_config_digest,
        feedback_manifest_digest=selection.feedback_manifest_digest,
    )
    return PreparedSelection(evidence, decision)


def append_decision(prompt: str, decision: TopicSelectionDecision | None) -> str:
    if decision is None:
        return prompt
    encoded = json.dumps(decision.as_json(), ensure_ascii=False, sort_keys=True)
    return (
        prompt
        + " The host program has committed the topic selection decision below. "
        + "Use selected_keyword exactly as resolved_keyword. Write only the selection reason, article angle, reader questions, and visual plan around that fixed topic. "
        + "Do not choose, rank, replace, or add candidates. Treat this as immutable input. "
        + f"<selection-decision>{encoded}</selection-decision>"
    )


__all__ = ["PreparedSelection", "append_decision", "prepare_selection"]

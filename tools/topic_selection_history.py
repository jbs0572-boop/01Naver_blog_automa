from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.contract_types import JSONValue
from tools.topic_selection_models import TopicSelectionDecision, TopicSelectionHistory


def nonproduction_selection_keywords(
    root: Path, decisions: tuple[TopicSelectionDecision, ...]
) -> tuple[str, ...]:
    values: list[str] = []
    for decision in decisions:
        state_path = root / ".automation" / "state" / f"{decision.run_id}.json"
        try:
            state: JSONValue = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(state, dict)
            and state.get("run_id") == decision.run_id
            and state.get("mode") in {"demo", "beta", "legacy"}
        ):
            values.append(decision.selected_keyword)
    return tuple(values)


def completed_selection_history(
    root: Path,
    current_run_id: str,
    decisions: tuple[TopicSelectionDecision, ...],
) -> tuple[TopicSelectionHistory, ...]:
    values: list[TopicSelectionHistory] = []
    for decision in decisions:
        run_key = hashlib.sha256(decision.run_id.encode("utf-8")).hexdigest()
        reservation = root / ".automation/topic-selection-reservations" / f"{run_key}.json"
        if decision.run_id == current_run_id or not reservation.is_file():
            continue
        state_path = root / ".automation" / "state" / f"{decision.run_id}.json"
        selection_path = root / "research" / f"topic-selection-{decision.selected_keyword}.md"
        try:
            state: JSONValue = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        stages = state.get("stages") if isinstance(state, dict) else None
        if (
            not isinstance(state, dict)
            or state.get("run_id") != decision.run_id
            or state.get("topic_source") != "auto_selected"
            or state.get("mode") not in (None, "formal")
            or not isinstance(stages, dict)
            or stages.get("topic-selector") != "passed"
            or not selection_path.is_file()
        ):
            continue
        values.append(
            TopicSelectionHistory(
                decision.run_id,
                decision.selected_category,
                decision.created_at,
                "formal_selection_decision",
                True,
            )
        )
    return tuple(values)


__all__ = ["completed_selection_history", "nonproduction_selection_keywords"]

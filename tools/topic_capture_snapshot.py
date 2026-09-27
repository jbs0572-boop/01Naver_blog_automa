from __future__ import annotations

from pathlib import Path

from tools.contract_types import ContractError, JSONMap
from tools.runner_types import TopicSelectionContext
from tools.topic_metadata import canonicalize_snapshot_observations, write_snapshot


def materialize_host_snapshot(
    root: Path,
    raw_path: Path,
    observation: JSONMap,
    selection: TopicSelectionContext | None,
) -> tuple[Path, str] | None:
    if selection is None or not observation.get("candidates"):
        return None
    if selection.capture_id is None:
        raise ContractError("batch snapshot context is incomplete")
    snapshot = canonicalize_snapshot_observations(
        raw_path,
        expected_capture_id=selection.capture_id,
        expected_as_of_date=selection.as_of_date,
    )
    canonical_path = write_snapshot(root, snapshot)
    return canonical_path, canonical_path.read_text(encoding="utf-8")


__all__ = ["materialize_host_snapshot"]

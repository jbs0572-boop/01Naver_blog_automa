from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from tools.contract_types import ContractError, JSONMap
from tools.topic_feedback_import import ImportRequest, prepare_topic_signal
from tools.topic_feedback_store import TopicFeedbackStore, encode_snapshot


@dataclass(frozen=True, slots=True)
class ImportCliRequest:
    input_path: str
    source: str
    as_of_date: str
    root: Path
    dry_run: bool


def run_import_capture(request: ImportCliRequest) -> int:
    prepared = prepare_topic_signal(
        ImportRequest(
            request.input_path,
            request.source,
            request.as_of_date,
            sys.stdin.read() if request.input_path == "-" else None,
        )
    )
    location = prepared.location
    artifact = prepared.artifact
    planned_path = request.root / location.relative_path
    digest = artifact.payload.get("digest")
    if not isinstance(digest, str):
        raise ContractError("snapshot digest is invalid")
    if request.dry_run:
        encoded = encode_snapshot(artifact)
        result: JSONMap = {
            "path": str(planned_path),
            "digest": digest,
            "size_bytes": len(encoded),
            "dry_run": True,
        }
    else:
        with TopicFeedbackStore(request.root) as store:
            stored = store.store(location, artifact)
        result = {
            "path": str(stored.path),
            "digest": stored.digest,
            "size_bytes": len(stored.encoded),
            "dry_run": False,
        }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


__all__ = ["ImportCliRequest", "run_import_capture"]

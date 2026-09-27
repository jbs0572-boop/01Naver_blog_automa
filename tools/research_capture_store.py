from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.runner_state import atomic_write_json


@dataclass(frozen=True, slots=True)
class CaptureLedger:
    run_id: str
    entries: tuple[JSONMap, ...]


def ledger_path(root: Path, run_id: str) -> Path:
    if Path(run_id).name != run_id:
        raise ContractError("research capture run_id is unsafe")
    return root / "metadata/research-capture" / f"{run_id}.json"


def load_ledger(root: Path, run_id: str) -> CaptureLedger:
    path = ledger_path(root, run_id)
    if not path.is_file():
        return CaptureLedger(run_id, ())
    try:
        raw: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError("research capture ledger is malformed") from error
    if not isinstance(raw, dict) or raw.get("run_id") != run_id:
        raise ContractError("research capture ledger is malformed")
    entries = raw.get("entries")
    if not isinstance(entries, list) or not all(
        isinstance(item, dict) for item in entries
    ):
        raise ContractError("research capture ledger entries are malformed")
    typed_entries: list[JSONMap] = [item for item in entries if isinstance(item, dict)]
    return CaptureLedger(run_id, tuple(typed_entries))


def append_capture(root: Path, run_id: str, capture: JSONMap) -> CaptureLedger:
    current = load_ledger(root, run_id)
    capture_id = capture.get("capture_id")
    if not isinstance(capture_id, str):
        raise ContractError("research capture_id is required")
    if any(item.get("capture_id") == capture_id for item in current.entries):
        return current
    entries = (*current.entries, capture)
    payload: JSONMap = {
        "schema_version": "research-capture-ledger-v1",
        "run_id": run_id,
        "entries": list(entries),
    }
    atomic_write_json(ledger_path(root, run_id), payload)
    return CaptureLedger(run_id, entries)


def completed_capture(
    ledger: CaptureLedger, binding: JSONMap
) -> JSONMap | None:
    for entry in ledger.entries:
        if entry.get("capture_binding") != binding:
            raise ContractError(
                "research capture ledger binding mismatch; existing evidence preserved"
            )
    for entry in reversed(ledger.entries):
        if entry.get("capture_state") == "completed" or "observations" in entry:
            return entry
    return None


def reserve_capture_attempt(
    root: Path, run_id: str, binding: JSONMap, max_searches: int
) -> JSONMap:
    ledger = load_ledger(root, run_id)
    if completed_capture(ledger, binding) is not None:
        raise ContractError("research capture is already complete")
    searches_used = 0
    for entry in ledger.entries:
        if entry.get("capture_state") != "reserved":
            continue
        counters = entry.get("attempt_counters")
        if not isinstance(counters, dict):
            raise ContractError("research capture reservation is malformed")
        searches = counters.get("searches")
        if not isinstance(searches, int) or isinstance(searches, bool) or searches < 1:
            raise ContractError("research capture reservation is malformed")
        searches_used += searches
    if searches_used >= max_searches:
        raise ContractError("research capture search budget exhausted")
    reservation: JSONMap = {
        "capture_id": "ATTEMPT-" + uuid.uuid4().hex,
        "capture_state": "reserved",
        "capture_binding": binding,
        "attempt_counters": {"searches": 1},
        "remaining_searches": max_searches - searches_used - 1,
    }
    _ = append_capture(root, run_id, reservation)
    return reservation


def file_digest(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ContractError(
            f"research capture binding input unavailable: {path}"
        ) from error
    return "sha256:" + hashlib.sha256(data).hexdigest()


def persist_raw_capture(root: Path, run_id: str, capture: JSONMap) -> tuple[str, str]:
    capture_id = capture.get("capture_id")
    if not isinstance(capture_id, str):
        raise ContractError("research capture_id is required")
    relative = Path("metadata/research-capture") / f"{run_id}-{capture_id}-raw.json"
    path = root / relative
    if path.is_file():
        raise ContractError("research raw capture already exists")
    atomic_write_json(path, capture)
    return relative.as_posix(), file_digest(path)


__all__ = [
    "CaptureLedger",
    "append_capture",
    "completed_capture",
    "file_digest",
    "ledger_path",
    "load_ledger",
    "persist_raw_capture",
    "reserve_capture_attempt",
]

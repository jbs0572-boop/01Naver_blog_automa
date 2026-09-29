from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from tools.contract_types import ContractError, JSONMap, JSONValue

CancellationScope = Literal["queued_only", "remaining"]
SCHEMA_VERSION = "run-cancellation-v1"


@dataclass(frozen=True, slots=True)
class CancellationRequest:
    run_id: str
    batch_id: str
    child_id: str
    scope: CancellationScope
    requested_at: str
    nonce_sha256: str

    def as_json(self) -> JSONMap:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "batch_id": self.batch_id,
            "child_id": self.child_id,
            "scope": self.scope,
            "requested_at": self.requested_at,
            "nonce_sha256": self.nonce_sha256,
        }


def cancellation_path(root: Path, run_id: str) -> Path:
    if not _safe_component(run_id, "RUN-"):
        raise ContractError("run_id is invalid")
    return root / ".automation" / "control" / f"{run_id}.cancel.json"


def nonce_digest(nonce: str) -> str:
    if not nonce:
        raise ContractError("cancellation nonce is invalid")
    return "sha256:" + hashlib.sha256(nonce.encode("utf-8")).hexdigest()


def create_cancellation(
    root: Path,
    *,
    run_id: str,
    batch_id: str,
    child_id: str,
    scope: CancellationScope,
    nonce: str,
    requested_at: str | None = None,
) -> CancellationRequest:
    _validate_ids(run_id, batch_id, child_id)
    if scope not in {"queued_only", "remaining"}:
        raise ContractError("cancellation scope is invalid")
    request = CancellationRequest(
        run_id,
        batch_id,
        child_id,
        scope,
        requested_at or datetime.now(UTC).isoformat(),
        nonce_digest(nonce),
    )
    path = cancellation_path(root, run_id)
    if path.parent.is_symlink() or path.is_symlink():
        raise ContractError("cancellation path is unsafe")
    encoded = (json.dumps(request.as_json(), ensure_ascii=False, sort_keys=True) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        existing = read_cancellation(root, run_id)
        if existing is None or (existing.batch_id, existing.child_id, existing.scope, existing.nonce_sha256) != (request.batch_id, request.child_id, request.scope, request.nonce_sha256):
            raise ContractError("cancellation request already exists with a different nonce or scope")
        return existing
    except OSError as error:
        raise ContractError("could not create cancellation request") from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            _ = handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        raise ContractError("could not persist cancellation request") from error
    return request


def read_cancellation(root: Path, run_id: str) -> CancellationRequest | None:
    path = cancellation_path(root, run_id)
    try:
        if path.is_symlink() or (path.parent.exists() and path.parent.is_symlink()):
            raise ContractError("cancellation path is unsafe")
    except OSError as error:
        raise ContractError("cancellation path is unsafe") from error
    try:
        value: JSONValue = json.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("could not read cancellation request") from error
    if not isinstance(value, dict):
        raise ContractError("cancellation request must be an object")
    required = {"schema_version", "run_id", "batch_id", "child_id", "scope", "requested_at", "nonce_sha256"}
    if set(value) != required or value.get("schema_version") != SCHEMA_VERSION:
        raise ContractError("cancellation request schema is invalid")
    run = value.get("run_id"); batch = value.get("batch_id"); child = value.get("child_id")
    scope = value.get("scope"); at = value.get("requested_at"); digest = value.get("nonce_sha256")
    if not all(isinstance(item, str) for item in (run, batch, child, scope, at, digest)):
        raise ContractError("cancellation request fields are invalid")
    assert isinstance(run, str) and isinstance(batch, str) and isinstance(child, str)
    assert isinstance(scope, str) and isinstance(at, str) and isinstance(digest, str)
    _validate_ids(run, batch, child)
    if run != run_id or scope not in {"queued_only", "remaining"} or not digest.startswith("sha256:") or len(digest) != 71:
        raise ContractError("cancellation request fields are invalid")
    typed_scope: CancellationScope = "queued_only" if scope == "queued_only" else "remaining"
    return CancellationRequest(run, batch, child, typed_scope, at, digest)


def cancellation_requested(root: Path, run_id: str, *, scope: CancellationScope | None = None) -> CancellationRequest | None:
    request = read_cancellation(root, run_id)
    if request is None or scope is None or request.scope == scope or request.scope == "remaining":
        return request
    return None


def _safe_component(value: str, prefix: str) -> bool:
    return value.startswith(prefix) and len(Path(value).parts) == 1 and "/" not in value and "\\" not in value and value not in {".", ".."}


def _validate_ids(run_id: str, batch_id: str, child_id: str) -> None:
    if not _safe_component(run_id, "RUN-") or not _safe_component(batch_id, "BATCH-") or not _safe_component(child_id, "CHILD-"):
        raise ContractError("cancellation identity is invalid")


__all__ = ["SCHEMA_VERSION", "CancellationRequest", "CancellationScope", "cancellation_path", "cancellation_requested", "create_cancellation", "nonce_digest", "read_cancellation"]

from __future__ import annotations

from collections.abc import Callable
from math import isfinite

from tools.contract_types import ContractError, JSONMap
from tools.external_adapter import (
    ExternalWritePlan,
    ExternalWriteRequest,
    NotionWriteOperation,
)


def authorize_create(
    authorizer: Callable[[ExternalWriteRequest, NotionWriteOperation], JSONMap],
    request: ExternalWriteRequest,
    plan: ExternalWritePlan,
    operation: NotionWriteOperation,
) -> None:
    verified = authorizer(request, operation)
    if verified.get("verified_artifact_digest") != plan.artifact_digest:
        raise ContractError(
            "Notion operation authorization artifact digest does not match plan"
        )


def operation_deadline(clock: Callable[[], float], timeout_seconds: float) -> float:
    if not isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ContractError("Notion overall timeout must be a positive finite value")
    return clock() + timeout_seconds

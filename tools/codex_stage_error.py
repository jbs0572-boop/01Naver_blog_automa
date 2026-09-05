from __future__ import annotations

from dataclasses import dataclass
from typing import override

from tools.contract_types import ContractError


@dataclass(frozen=True, slots=True)
class StageExecutionError(ContractError):
    stage: str
    reason: str

    @override
    def __str__(self) -> str:
        return f"{self.stage} stage result invalid: {self.reason}"


__all__ = ["StageExecutionError"]

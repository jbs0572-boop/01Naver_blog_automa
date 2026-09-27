from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import override

from tools.contract_types import ContractError


class StageFailureType(StrEnum):
    RATE_LIMITED = "rate_limited"
    PROCESS_TIMEOUT = "process_timeout"
    TEMPORARY_IO = "temporary_io"
    PROCESS_UNAVAILABLE = "process_unavailable"
    INVALID_CONFIGURATION = "invalid_configuration"
    CONTRACT_FAILED = "contract_failed"
    EXTERNAL_RESULT_UNCERTAIN = "external_result_uncertain"
    QUALITY_FAILED = "quality_failed"


@dataclass(frozen=True, slots=True)
class FailurePolicy:
    retryable: bool
    next_action: str


FAILURE_POLICIES = {
    StageFailureType.RATE_LIMITED: FailurePolicy(True, "요청 제한이 해제된 뒤 해당 단계를 다시 시도하세요."),
    StageFailureType.PROCESS_TIMEOUT: FailurePolicy(True, "단계 입력과 실행 환경을 확인한 뒤 해당 단계를 다시 시도하세요."),
    StageFailureType.TEMPORARY_IO: FailurePolicy(True, "로컬 저장 공간과 파일 접근 상태를 확인한 뒤 다시 시도하세요."),
    StageFailureType.PROCESS_UNAVAILABLE: FailurePolicy(False, "Codex 실행 파일과 PATH 설정을 확인하세요."),
    StageFailureType.INVALID_CONFIGURATION: FailurePolicy(False, "단계 모델·프로필 설정을 수정한 뒤 다시 시작하세요."),
    StageFailureType.CONTRACT_FAILED: FailurePolicy(False, "단계 산출물과 계약 오류를 수정한 뒤 해당 단계를 다시 시도하세요."),
    StageFailureType.EXTERNAL_RESULT_UNCERTAIN: FailurePolicy(False, "외부 결과를 조회하거나 checkpoint 복구 절차를 사용하세요."),
    StageFailureType.QUALITY_FAILED: FailurePolicy(False, "점수와 실패 근거를 확인하고 결과를 수정·재평가하세요. 품질 Gate를 우회해 저장하지 마세요."),
}


def failure_policy(error_type: str | None) -> FailurePolicy | None:
    if error_type is None:
        return None
    try:
        return FAILURE_POLICIES[StageFailureType(error_type)]
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class StageExecutionError(ContractError):
    stage: str
    reason: str
    error_type: str = StageFailureType.CONTRACT_FAILED.value
    retryable: bool = False
    next_action: str | None = None
    process_attempts: int = 0

    @override
    def __str__(self) -> str:
        return f"{self.stage} stage result invalid: {self.reason}"


__all__ = ["FailurePolicy", "StageExecutionError", "StageFailureType", "failure_policy"]

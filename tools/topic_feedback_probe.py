from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, Protocol

from tools.contract_types import ContractError

type ProbeStatus = Literal[403, 429]
type BlockStatus = Literal["blocked_missing_credentials", "blocked_rate_limited"]
_BLOCK_STATUS: Final[dict[ProbeStatus, BlockStatus]] = {
    403: "blocked_missing_credentials",
    429: "blocked_rate_limited",
}


@dataclass(frozen=True, slots=True)
class ProbeResponse:
    source_id: str
    status_code: int
    body: bytes


class ReadTransport(Protocol):
    def read(self, source_id: str) -> ProbeResponse: ...


@dataclass(frozen=True, slots=True)
class ProbeResult:
    source_id: str
    status: BlockStatus
    transport_calls: int
    promotion_allowed: bool
    signal_payload: bytes | None


def _status(value: int) -> ProbeStatus:
    if value == 403:
        return 403
    if value == 429:
        return 429
    raise ContractError("optional source probe status is unsupported")


def probe_optional_source(source_id: str, transport: ReadTransport) -> ProbeResult:
    if source_id != "naver-datalab":
        raise ContractError("optional source probe is not approved")
    response = transport.read(source_id)
    if response.source_id != source_id or response.body:
        raise ContractError("optional source probe response is invalid")
    status = _BLOCK_STATUS[_status(response.status_code)]
    return ProbeResult(source_id, status, 1, False, None)


__all__ = ["ProbeResponse", "ProbeResult", "ReadTransport", "probe_optional_source"]

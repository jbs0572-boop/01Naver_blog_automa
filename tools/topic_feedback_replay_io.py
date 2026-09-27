from __future__ import annotations

from collections.abc import Callable

from tools.contract_types import ContractError

type WriteOnce = Callable[[int, memoryview], int]
type DescriptorOperation = Callable[[int], None]


def write_all(descriptor: int, encoded: bytes, write_once: WriteOnce) -> None:
    remaining = memoryview(encoded)
    while remaining:
        try:
            written = write_once(descriptor, remaining)
        except InterruptedError:
            continue
        except OSError as error:
            raise ContractError("replay write failed safely") from error
        if written <= 0 or written > len(remaining):
            raise ContractError("replay write made no progress")
        remaining = remaining[written:]


def retry_sync(
    descriptor: int, operation: DescriptorOperation, error_message: str
) -> None:
    while True:
        try:
            operation(descriptor)
            return
        except InterruptedError:
            continue
        except OSError as error:
            raise ContractError(error_message) from error


def close_descriptor_once(
    descriptor: int, operation: DescriptorOperation, error_message: str
) -> None:
    try:
        operation(descriptor)
    except OSError as error:
        raise ContractError(error_message) from error


__all__ = ["close_descriptor_once", "retry_sync", "write_all"]

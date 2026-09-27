from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Final, Protocol

from tools.notion_keychain import KEYCHAIN_ACCOUNT, KEYCHAIN_SERVICE

PROFILE_NAME: Final = "naver-automation"
PERMISSION_PROFILE: Final = "naver-stage-isolated"
PROBE_TIMEOUT_SECONDS: Final = 5
KEYCHAIN_DENIED_RETURN_CODE: Final = 44


class ProbeRunner(Protocol):
    def __call__(
        self,
        command: list[str],
        *,
        stdout: int,
        stderr: int,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[bytes]: ...


def build_keychain_isolation_command(codex_binary: str) -> list[str]:
    return [
        codex_binary,
        "sandbox",
        "-p",
        PROFILE_NAME,
        "-P",
        PERMISSION_PROFILE,
        "/usr/bin/security",
        "find-generic-password",
        "-s",
        KEYCHAIN_SERVICE,
        "-a",
        KEYCHAIN_ACCOUNT,
        "-w",
    ]


def _sandbox_probe_runner(
    command: list[str], *, stdout: int, stderr: int, timeout: int, check: bool
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command, stdout=stdout, stderr=stderr, timeout=timeout, check=check
    )


def keychain_isolation_holds(
    *,
    codex_binary: str = "codex",
    trusted_lookup: Callable[[], bool],
    runner: ProbeRunner = _sandbox_probe_runner,
) -> bool:
    if not trusted_lookup():
        return False
    try:
        completed = runner(
            build_keychain_isolation_command(codex_binary),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == KEYCHAIN_DENIED_RETURN_CODE


__all__ = [
    "KEYCHAIN_ACCOUNT",
    "KEYCHAIN_DENIED_RETURN_CODE",
    "KEYCHAIN_SERVICE",
    "PERMISSION_PROFILE",
    "build_keychain_isolation_command",
    "keychain_isolation_holds",
]

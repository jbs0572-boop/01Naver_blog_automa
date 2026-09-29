from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, NewType, override

KEYCHAIN_SERVICE: Final = "naver-blog-automation-notion"
KEYCHAIN_ACCOUNT: Final = "notion-api-token"
KEYCHAIN_TIMEOUT_SECONDS: Final = 5
MAX_NOTION_API_TOKEN_LENGTH: Final = 1024
NotionApiToken = NewType("NotionApiToken", str)
KeychainCommandRunner = Callable[[tuple[str, ...]], bytes]


@dataclass(frozen=True, slots=True)
class NotionCredentialsUnavailable(Exception):
    code: str = "notion_credentials_unavailable"

    @override
    def __str__(self) -> str:
        return self.code


def load_notion_api_token(
    command_runner: KeychainCommandRunner | None = None,
) -> NotionApiToken:
    runner = _system_keychain_command if command_runner is None else command_runner
    try:
        raw_token = runner(
            (
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                KEYCHAIN_ACCOUNT,
                "-w",
            )
        )
    except (OSError, subprocess.TimeoutExpired):
        raise NotionCredentialsUnavailable() from None
    try:
        token = raw_token.decode("ascii").removesuffix("\n").removesuffix("\r")
    except UnicodeDecodeError:
        raise NotionCredentialsUnavailable() from None
    if (
        not token
        or len(token) > MAX_NOTION_API_TOKEN_LENGTH
        or not token.isascii()
        or not token.isprintable()
        or any(character.isspace() for character in token)
    ):
        raise NotionCredentialsUnavailable() from None
    return NotionApiToken(token)


def _system_keychain_command(command: tuple[str, ...]) -> bytes:
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=KEYCHAIN_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        raise OSError("keychain lookup failed")
    return completed.stdout


__all__ = [
    "KEYCHAIN_ACCOUNT",
    "KEYCHAIN_SERVICE",
    "NotionApiToken",
    "NotionCredentialsUnavailable",
    "load_notion_api_token",
]

from __future__ import annotations

import subprocess
import traceback

import pytest

from tools import notion_keychain


def test_keychain_loader_rejects_non_ascii_system_output_without_traceback_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the system Keychain command returns bytes that cannot be decoded as ASCII.
    def command(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess([], 0, b"\xffnotion-token", b"")

    monkeypatch.setattr("tools.notion_keychain.subprocess.run", command)

    # When: the loader reads the dedicated Keychain credential.
    with pytest.raises(notion_keychain.NotionCredentialsUnavailable) as raised:
        _ = notion_keychain.load_notion_api_token()
    rendered = "".join(traceback.format_exception(raised.value))

    # Then: the byte content and decoder failure stay outside the surfaced error.
    assert str(raised.value) == "notion_credentials_unavailable"
    assert raised.value.__cause__ is None
    assert "notion-token" not in rendered
    assert "UnicodeDecodeError" not in rendered


def test_keychain_loader_reads_only_the_dedicated_service_and_account() -> None:
    # Given: a command runner that records its security command without logging output.
    commands: list[tuple[str, ...]] = []

    def command_runner(command: tuple[str, ...]) -> bytes:
        commands.append(command)
        return b"credential-value\n"

    # When: the runtime loads the Notion credential.
    token = notion_keychain.load_notion_api_token(command_runner)

    # Then: it requests the approved Keychain item and returns its value.
    assert token == "credential-value"
    assert commands == [
        (
            "/usr/bin/security",
            "find-generic-password",
            "-s",
            "naver-blog-automation-notion",
            "-a",
            "notion-api-token",
            "-w",
        )
    ]


def test_keychain_loader_redacts_the_underlying_failure() -> None:
    # Given: a Keychain command failure whose detail contains a token-like value.
    def command_runner(_command: tuple[str, ...]) -> bytes:
        raise OSError("credential-value must not escape")

    # When: credential loading reaches the failed system command.
    # Then: callers receive only the stable, non-secret failure code.
    with pytest.raises(notion_keychain.NotionCredentialsUnavailable) as raised:
        _ = notion_keychain.load_notion_api_token(command_runner)
    assert str(raised.value) == "notion_credentials_unavailable"
    assert "credential-value" not in str(raised.value)


def test_keychain_loader_does_not_chain_a_secret_bearing_failure() -> None:
    # Given: a failed command whose error message includes the credential value.
    def command_runner(_command: tuple[str, ...]) -> bytes:
        raise OSError("credential-value must not escape")

    # When: the stable credential error is rendered as a traceback.
    with pytest.raises(notion_keychain.NotionCredentialsUnavailable) as raised:
        _ = notion_keychain.load_notion_api_token(command_runner)
    rendered = "".join(traceback.format_exception(raised.value))

    # Then: neither the traceback nor its stable error exposes the credential.
    assert "credential-value" not in rendered


@pytest.mark.parametrize(
    "invalid_token",
    [
        "ntn_토큰",
        "token with space",
        "token\nwith-newline",
        "token\x00with-control",
        "x" * 1025,
    ],
)
def test_keychain_loader_rejects_unsafe_tokens_without_leaking_them(
    invalid_token: str,
) -> None:
    # Given: Keychain output containing a value unsafe for an HTTP header.
    def command_runner(_command: tuple[str, ...]) -> bytes:
        return invalid_token.encode("utf-8")

    # When: the runtime loads the Notion credential.
    with pytest.raises(notion_keychain.NotionCredentialsUnavailable) as raised:
        _ = notion_keychain.load_notion_api_token(command_runner)
    rendered = "".join(traceback.format_exception(raised.value))

    # Then: the stable boundary error contains no raw credential value.
    assert str(raised.value) == "notion_credentials_unavailable"
    assert raised.value.__cause__ is None
    assert invalid_token not in rendered


@pytest.mark.parametrize(
    "safe_token",
    [
        "ntn_modern-token-example",
        "secret_legacy-token-example",
        "opaque-token-without-a-known-prefix",
    ],
)
def test_keychain_loader_accepts_opaque_ascii_tokens(safe_token: str) -> None:
    # Given: an opaque ASCII token accepted by the transport boundary.
    def command_runner(_command: tuple[str, ...]) -> bytes:
        return f"{safe_token}\n".encode("ascii")

    # When: the runtime loads the Notion credential.
    token = notion_keychain.load_notion_api_token(command_runner)

    # Then: the exact opaque value is preserved without prefix assumptions.
    assert token == safe_token

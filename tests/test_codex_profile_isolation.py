from __future__ import annotations

import subprocess

import pytest

from tools.codex_profile_isolation import (
    KEYCHAIN_ACCOUNT,
    KEYCHAIN_DENIED_RETURN_CODE,
    KEYCHAIN_SERVICE,
    PERMISSION_PROFILE,
    build_keychain_isolation_command,
    keychain_isolation_holds,
)


def test_isolation_probe_uses_explicit_profile_and_discards_output() -> None:
    # Given: the dedicated stage profile names its closed permission policy.
    calls: list[tuple[list[str], int | None, int | None, int | None]] = []

    def runner(command: list[str], **kwargs: int) -> subprocess.CompletedProcess[bytes]:
        calls.append(
            (command, kwargs.get("stdout"), kwargs.get("stderr"), kwargs.get("timeout"))
        )
        return subprocess.CompletedProcess(command, KEYCHAIN_DENIED_RETURN_CODE)

    # When: an existing trusted Keychain item is inaccessible in the sandbox.
    result = keychain_isolation_holds(
        codex_binary="codex",
        trusted_lookup=lambda: True,
        runner=runner,
    )

    # Then: the probe fails closed and never captures credential output.
    assert result is True
    assert calls == [
        (
            build_keychain_isolation_command("codex"),
            subprocess.DEVNULL,
            subprocess.DEVNULL,
            5,
        )
    ]
    command = calls[0][0]
    assert command[:2] == ["codex", "sandbox"]
    assert command[command.index("-p") + 1] == "naver-automation"
    assert command[command.index("-P") + 1] == PERMISSION_PROFILE
    assert command[-7:] == [
        "/usr/bin/security",
        "find-generic-password",
        "-s",
        KEYCHAIN_SERVICE,
        "-a",
        KEYCHAIN_ACCOUNT,
        "-w",
    ]


def test_isolation_probe_fails_closed_when_trusted_lookup_is_unavailable() -> None:
    # Given: the trusted process cannot prove the fixed Keychain item exists.
    called = False

    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        nonlocal called
        called = True
        return subprocess.CompletedProcess([], 1)

    # When: isolation is checked.
    result = keychain_isolation_holds(
        codex_binary="codex",
        trusted_lookup=lambda: False,
        runner=runner,
    )

    # Then: preflight cannot claim the boundary is verified.
    assert result is False
    assert called is False


def test_isolation_probe_fails_closed_on_runner_error() -> None:
    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise OSError("sandbox unavailable")

    assert keychain_isolation_holds(
        trusted_lookup=lambda: True,
        runner=runner,
    ) is False


@pytest.mark.parametrize("return_code", (0, 1))
def test_isolation_probe_rejects_success_and_generic_cli_failure(
    return_code: int,
) -> None:
    def runner(
        command: list[str], **_kwargs: int
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, return_code)

    assert keychain_isolation_holds(
        trusted_lookup=lambda: True,
        runner=runner,
    ) is False

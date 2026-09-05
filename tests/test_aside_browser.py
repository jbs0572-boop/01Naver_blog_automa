from __future__ import annotations

from pathlib import Path

import pytest

from tools.aside_browser import AsideCliConfig, AsideReplSession
from tools.contract_types import ContractError


def test_repl_session_runs_deterministic_browser_script() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(arguments: tuple[str, ...], _timeout: float) -> str:
        calls.append(arguments)
        return '__ASIDE_RESULT__"로그인됨"\n'

    session = AsideReplSession(
        AsideCliConfig(Path("/usr/local/bin/aside"), account="work"), runner
    )

    session.goto("https://blog.naver.com/sola_note", timeout_ms=12_000)
    assert session.text("#auth") == "로그인됨"
    assert calls[0][1:4] == ("repl", "--account", "work")
    assert "page.goto" in calls[0][-1]
    assert "page.locator" in calls[1][-1]


def test_repl_session_rejects_failed_cli() -> None:
    def runner(_arguments: tuple[str, ...], _timeout: float) -> str:
        raise ContractError("Aside CLI command failed")

    session = AsideReplSession(AsideCliConfig(Path("aside")), runner)

    with pytest.raises(ContractError, match="Aside CLI command failed"):
        session.click("button.save")

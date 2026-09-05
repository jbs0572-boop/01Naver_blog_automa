from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
PLISTS = tuple(sorted((ROOT / "launchd").glob("*.plist")))


@pytest.mark.parametrize("path", PLISTS)
def test_plist_has_absolute_runner_contract(path: Path) -> None:
    data = plistlib.loads(path.read_bytes())
    args = data["ProgramArguments"]

    assert data["Label"] == path.stem
    assert Path(args[0]).is_absolute()
    assert args[1:3] == ["-m", "tools.preflight_runner"]
    assert "--mode" not in args
    assert "beta" not in args
    assert "formal" not in args
    assert data["WorkingDirectory"] == str(ROOT)
    assert Path(data["StandardOutPath"]).is_absolute()
    assert Path(data["StandardErrorPath"]).is_absolute()
    environment = data["EnvironmentVariables"]
    assert environment["CODEX_HOME"] == "/Users/beomseok/.codex"
    assert "/opt/homebrew/bin" in environment["PATH"].split(":")
    assert "/.automation/logs/launchd/" in data["StandardOutPath"]
    assert "/.automation/logs/launchd/" in data["StandardErrorPath"]
    if path.stem == "com.naverblog.naver-publish":
        assert "StartCalendarInterval" not in data
        assert "__SET_RUN_ID__" in args
        assert "--dry-run" in args
    else:
        assert "StartCalendarInterval" in data
        assert "--dry-run" not in args
    assert "KeepAlive" not in data


def test_daily_and_naver_placeholders_are_manual_only() -> None:
    daily = plistlib.loads(
        (ROOT / "launchd/com.naverblog.daily-generate.plist").read_bytes()
    )
    naver = plistlib.loads(
        (ROOT / "launchd/com.naverblog.naver-publish.plist").read_bytes()
    )

    assert "--auto-topic" in daily["ProgramArguments"]
    assert "__SET_RUN_ID__" in naver["ProgramArguments"]


def test_launchd_files_contain_no_registration_actions() -> None:
    forbidden = ("launchctl", "bootstrap", "bootout", "kickstart")
    for path in PLISTS:
        text = path.read_text(encoding="utf-8")
        assert all(token not in text for token in forbidden)
        if path.stem != "com.naverblog.naver-publish":
            assert "StartCalendarInterval" in text
        assert "KeepAlive" not in text

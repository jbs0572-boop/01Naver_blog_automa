from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
PLISTS = tuple(sorted((ROOT / "launchd").glob("*.plist")))


@pytest.mark.parametrize("path", PLISTS)
def test_plist_has_absolute_disabled_dry_run_runner_contract(path: Path) -> None:
    data = plistlib.loads(path.read_bytes())
    args = data["ProgramArguments"]

    assert data["Label"] == path.stem
    assert Path(args[0]).is_absolute()
    assert args[1:3] == ["-m", "tools.automation_runner"]
    assert data["WorkingDirectory"] == str(ROOT)
    assert Path(data["StandardOutPath"]).is_absolute()
    assert Path(data["StandardErrorPath"]).is_absolute()
    assert "/.automation/logs/launchd/" in data["StandardOutPath"]
    assert "/.automation/logs/launchd/" in data["StandardErrorPath"]
    assert data["Disabled"] is True
    assert "--dry-run" in args
    assert "StartCalendarInterval" not in data
    assert "KeepAlive" not in data


def test_plist_placeholders_are_limited_to_required_inputs() -> None:
    daily = plistlib.loads(
        (ROOT / "launchd/com.naverblog.daily-generate.plist").read_bytes()
    )
    naver = plistlib.loads(
        (ROOT / "launchd/com.naverblog.naver-publish.plist").read_bytes()
    )

    assert "__SET_KEYWORD__" in daily["ProgramArguments"]
    assert "__SET_RUN_ID__" in naver["ProgramArguments"]


def test_launchd_files_contain_no_registration_or_schedule_actions() -> None:
    forbidden = ("launchctl", "bootstrap", "bootout", "kickstart")
    for path in PLISTS:
        text = path.read_text(encoding="utf-8")
        assert all(token not in text for token in forbidden)
        assert "StartCalendarInterval" not in text
        assert "KeepAlive" not in text

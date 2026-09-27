from __future__ import annotations

import inspect
import json
from datetime import datetime
from pathlib import Path

import pytest

from tools.contract_types import ContractError
from tools.dashboard_manual_models import ManualRunContext
from tools.dashboard_model_settings import ModelSettingsStore
from tools.dashboard_schedule import DailySchedule
from tools.test_dashboard import DashboardServerConfig

ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_has_no_user_facing_demo_or_mode_switch() -> None:
    markup = (ROOT / "dashboard/index.html").read_text(encoding="utf-8")
    app = (ROOT / "dashboard/app.js").read_text(encoding="utf-8")
    schedule = (ROOT / "dashboard/schedule.js").read_text(encoding="utf-8")

    assert "mode-select" not in markup
    assert 'id="schedule-mode"' not in markup
    assert "/api/mode" not in app
    assert "mode:" not in schedule
    assert "데모 모드" not in markup + app + schedule


def test_dashboard_runtime_contract_has_one_project_root_path() -> None:
    assert tuple(inspect.signature(DashboardServerConfig).parameters) == ("address", "root")
    assert tuple(inspect.signature(ManualRunContext).parameters) == ("root", "live_writes")


def test_schedule_rejects_retired_mode_payload_without_rewriting_file(
    tmp_path: Path,
) -> None:
    schedule = DailySchedule(tmp_path)
    with pytest.raises(ContractError, match="지원하지 않는 필드"):
        _ = schedule.save(
            {"times": ["08:00"], "enabled": True, "mode": "demo"},
            datetime.fromisoformat("2026-09-13T07:00:00+09:00"),
        )
    assert not schedule.path.exists()


def test_dashboard_cli_has_no_demo_or_live_write_switches() -> None:
    source = (ROOT / "tools/test_dashboard.py").read_text(encoding="utf-8")
    assert '"--demo"' not in source
    assert '"--live-writes"' not in source
    assert 'route == "/api/mode"' not in source


def test_manifest_keeps_internal_formal_audit_mode() -> None:
    source = (ROOT / "tools/manifest.py").read_text(encoding="utf-8")
    assert '"mode": "formal"' in source


def test_persisted_demo_schedule_is_inert_and_unchanged(tmp_path: Path) -> None:
    path = tmp_path / ".automation/dashboard/schedule.json"
    path.parent.mkdir(parents=True)
    raw = {
        "enabled": True,
        "times": ["08:00"],
        "mode": "demo",
        "since": "2026-09-12T07:00:00+09:00",
        "history": [{"at": "2026-09-12T08:00:00+09:00", "mode": "demo"}],
        "preset_id": "default",
        "model_config": ModelSettingsStore(tmp_path).snapshot().as_json(),
    }
    encoded = json.dumps(raw, ensure_ascii=False).encode()
    _ = path.write_bytes(encoded)
    launches: list[str] = []

    schedule = DailySchedule(tmp_path)
    schedule.tick(
        datetime.fromisoformat("2026-09-13T08:00:00+09:00"),
        lambda: False,
        lambda day, _scheduled_at, _entry_id, _config: launches.append(day) or "BATCH-forbidden",
    )

    view = schedule.view(datetime.fromisoformat("2026-09-13T07:00:00+09:00"))
    assert view["enabled"] is False
    assert launches == []
    assert path.read_bytes() == encoded

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.contract_types import ContractError
from tools.dashboard_model_settings import ModelSettingsStore


def test_default_preset_covers_only_llm_producer_stages(tmp_path: Path) -> None:
    snapshot = ModelSettingsStore(tmp_path).snapshot()

    assert snapshot.preset_id == "default"
    assert {setting.stage for setting in snapshot.stages} == {
        "topic-selector",
        "researcher",
        "writer",
        "image-maker",
        "content-assembler",
    }
    assert {
        setting.stage: (setting.model, setting.reasoning_effort)
        for setting in snapshot.stages
    } == {
        "topic-selector": ("gpt-5.6-luna", "medium"),
        "researcher": ("gpt-5.6-terra", "medium"),
        "writer": ("gpt-5.6-terra", "medium"),
        "image-maker": ("gpt-5.6-terra", "medium"),
        "content-assembler": ("gpt-5.6-luna", "low"),
    }
    assert snapshot.digest.startswith("sha256:")


def test_settings_save_rejects_stale_revision_and_unsupported_combo(
    tmp_path: Path,
) -> None:
    store = ModelSettingsStore(tmp_path)
    current = store.view()
    saved = store.save(
        {
            "revision": current["revision"],
            "active_preset_id": "default",
            "presets": current["presets"],
        }
    )
    assert saved["revision"] == 2

    with pytest.raises(ContractError, match="revision conflict"):
        _ = store.save(
            {
                "revision": 1,
                "active_preset_id": "default",
                "presets": current["presets"],
            }
        )

    payload = json.loads(json.dumps(saved))
    payload["presets"][0]["stages"]["writer"]["model"] = "unverified-model"
    with pytest.raises(ContractError, match="unsupported model configuration"):
        _ = store.save(
            {
                "revision": saved["revision"],
                "active_preset_id": "default",
                "presets": payload["presets"],
            }
        )


def test_missing_settings_file_loads_default_without_writing(tmp_path: Path) -> None:
    store = ModelSettingsStore(tmp_path)

    assert store.view()["schema_version"] == "model-settings-v1"
    assert not (tmp_path / ".automation/dashboard/model-settings.json").exists()

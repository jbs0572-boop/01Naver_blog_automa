from __future__ import annotations

import fcntl
import json
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.model_presets import (
    LLM_STAGES,
    ModelConfigSnapshot,
    StageModelConfig,
    default_stage_settings,
    model_config_snapshot,
)
from tools.runner_state import atomic_write_json

SCHEMA_VERSION: Final = "model-settings-v1"


class ModelSettingsStore:
    def __init__(self, root: Path) -> None:
        self.path: Path = root / ".automation/dashboard/model-settings.json"
        self._lock_path: Path = root / ".automation/dashboard/model-settings.lock"

    def view(self) -> JSONMap:
        return self._read() if self.path.is_file() else _default_document()

    def snapshot(self, preset_id: str | None = None) -> ModelConfigSnapshot:
        document = self.view()
        selected = preset_id or _string(document, "active_preset_id")
        revision = _integer(document, "revision")
        presets = _list(document, "presets")
        for raw in presets:
            preset = _map(raw, "model preset")
            if _string(preset, "id") == selected:
                return model_config_snapshot(
                    selected, revision, _stage_settings(preset)
                )
        raise ContractError("model preset does not exist")

    def save(self, payload: JSONMap) -> JSONMap:
        if set(payload) != {"revision", "active_preset_id", "presets"}:
            raise ContractError("model settings payload is invalid")
        with self._locked():
            current = self.view()
            if _integer(payload, "revision") != _integer(current, "revision"):
                raise ContractError("model settings revision conflict")
            updated: JSONMap = {
                "schema_version": SCHEMA_VERSION,
                "revision": _integer(current, "revision") + 1,
                "active_preset_id": _string(payload, "active_preset_id"),
                "presets": _list(payload, "presets"),
            }
            _validate_document(updated)
            atomic_write_json(self.path, updated)
            return updated

    def _read(self) -> JSONMap:
        try:
            raw: JSONValue = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ContractError("model settings file is invalid") from error
        document = _map(raw, "model settings")
        _validate_document(document)
        return document

    @contextmanager
    def _locked(self) -> Generator[None, None, None]:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _default_document() -> JSONMap:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 1,
        "active_preset_id": "default",
        "presets": [
            {
                "id": "default",
                "name": "기본",
                "stages": {
                    value.stage: value.as_json() for value in default_stage_settings()
                },
            }
        ],
    }


def _validate_document(value: JSONMap) -> None:
    if set(value) != {
        "schema_version", "revision", "active_preset_id", "presets"
    } or value.get("schema_version") != SCHEMA_VERSION:
        raise ContractError("model settings file is invalid")
    revision = _integer(value, "revision")
    if revision < 1:
        raise ContractError("model settings revision is invalid")
    active = _string(value, "active_preset_id")
    presets = _list(value, "presets")
    if not presets:
        raise ContractError("model settings presets are empty")
    identifiers: set[str] = set()
    for raw in presets:
        preset = _map(raw, "model preset")
        if set(preset) != {"id", "name", "stages"}:
            raise ContractError("model preset is invalid")
        identifier = _string(preset, "id")
        if not identifier or identifier in identifiers or not _string(preset, "name"):
            raise ContractError("model preset identity is invalid")
        identifiers.add(identifier)
        _ = model_config_snapshot(identifier, revision, _stage_settings(preset))
    if active not in identifiers:
        raise ContractError("active model preset does not exist")


def _stage_settings(preset: JSONMap) -> tuple[StageModelConfig, ...]:
    values = _map(preset.get("stages"), "model preset stages")
    if set(values) != set(LLM_STAGES):
        raise ContractError("model preset stages are invalid")
    return tuple(
        StageModelConfig(
            stage,
            _string(_map(values[stage], "stage model setting"), "model"),
            _string(
                _map(values[stage], "stage model setting"), "reasoning_effort"
            ),
        )
        for stage in LLM_STAGES
    )


def _map(value: JSONValue | None, label: str) -> JSONMap:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    return value


def _list(value: JSONMap, key: str) -> list[JSONValue]:
    item = value.get(key)
    if not isinstance(item, list):
        raise ContractError(f"model settings {key} is invalid")
    return item


def _string(value: JSONMap, key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise ContractError(f"model settings {key} is invalid")
    return item


def _integer(value: JSONMap, key: str) -> int:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int):
        raise ContractError(f"model settings {key} is invalid")
    return item


__all__ = ["ModelSettingsStore"]

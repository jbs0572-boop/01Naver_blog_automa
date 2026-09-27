from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final

from tools.contract_types import ContractError, JSONMap, JSONValue

LLM_STAGES: Final[tuple[str, ...]] = (
    "topic-selector",
    "researcher",
    "writer",
    "image-maker",
    "content-assembler",
)
DEFAULT_MODEL: Final = "gpt-5.6-sol"
DEFAULT_REASONING_EFFORT: Final = "high"
SUPPORTED_CONFIGS: Final = frozenset({
    (DEFAULT_MODEL, DEFAULT_REASONING_EFFORT),
    ("gpt-5.6-luna", "medium"),
    ("gpt-5.6-luna", "low"),
    ("gpt-5.6-terra", "medium"),
})


@dataclass(frozen=True, slots=True)
class StageModelConfig:
    stage: str
    model: str
    reasoning_effort: str

    def as_json(self) -> JSONMap:
        return {
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
        }


@dataclass(frozen=True, slots=True)
class ModelConfigSnapshot:
    preset_id: str
    revision: int
    stages: tuple[StageModelConfig, ...]
    digest: str

    def setting_for(self, stage: str) -> StageModelConfig | None:
        return next((value for value in self.stages if value.stage == stage), None)

    def as_json(self) -> JSONMap:
        return {
            "preset_id": self.preset_id,
            "revision": self.revision,
            "stages": {value.stage: value.as_json() for value in self.stages},
            "digest": self.digest,
        }


def default_stage_settings() -> tuple[StageModelConfig, ...]:
    return (
        StageModelConfig("topic-selector", "gpt-5.6-luna", "medium"),
        StageModelConfig("researcher", "gpt-5.6-terra", "medium"),
        StageModelConfig("writer", "gpt-5.6-terra", "medium"),
        StageModelConfig("image-maker", "gpt-5.6-terra", "medium"),
        StageModelConfig("content-assembler", "gpt-5.6-luna", "low"),
    )


def model_config_snapshot(
    preset_id: str,
    revision: int,
    stages: tuple[StageModelConfig, ...],
) -> ModelConfigSnapshot:
    _validate_stages(stages)
    material = {
        "preset_id": preset_id,
        "revision": revision,
        "stages": {value.stage: value.as_json() for value in stages},
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return ModelConfigSnapshot(
        preset_id, revision, stages, "sha256:" + hashlib.sha256(encoded).hexdigest()
    )


def validate_stage_model_config(value: StageModelConfig) -> None:
    if (value.model, value.reasoning_effort) not in SUPPORTED_CONFIGS:
        raise ContractError("unsupported model configuration")


def parse_model_config_snapshot(value: JSONValue) -> ModelConfigSnapshot:
    if not isinstance(value, dict):
        raise ContractError("model configuration snapshot is invalid")
    preset_id = value.get("preset_id")
    revision = value.get("revision")
    stages = value.get("stages")
    digest = value.get("digest")
    if (
        not isinstance(preset_id, str)
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or not isinstance(stages, dict)
        or not isinstance(digest, str)
    ):
        raise ContractError("model configuration snapshot is invalid")
    parsed: list[StageModelConfig] = []
    for stage in LLM_STAGES:
        raw = stages.get(stage)
        if not isinstance(raw, dict):
            raise ContractError("model configuration snapshot is invalid")
        model = raw.get("model")
        effort = raw.get("reasoning_effort")
        if not isinstance(model, str) or not isinstance(effort, str):
            raise ContractError("model configuration snapshot is invalid")
        parsed.append(StageModelConfig(stage, model, effort))
    snapshot = model_config_snapshot(preset_id, revision, tuple(parsed))
    if snapshot.digest != digest:
        raise ContractError("model configuration snapshot digest mismatch")
    return snapshot


def _validate_stages(stages: tuple[StageModelConfig, ...]) -> None:
    if tuple(value.stage for value in stages) != LLM_STAGES:
        raise ContractError("model preset stages are invalid")
    for value in stages:
        validate_stage_model_config(value)


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_REASONING_EFFORT",
    "LLM_STAGES",
    "ModelConfigSnapshot",
    "StageModelConfig",
    "default_stage_settings",
    "model_config_snapshot",
    "parse_model_config_snapshot",
    "validate_stage_model_config",
]

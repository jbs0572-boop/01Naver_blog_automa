from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tools.topic_feedback_scoring_models import AuxiliarySignal


@dataclass(frozen=True, slots=True)
class FeedbackEvidence:
    digest: str
    data_as_of: str
    signals: tuple[AuxiliarySignal, ...]
    signal_paths: tuple[str, ...]
    manifest_path: str


@dataclass(frozen=True, slots=True)
class FeedbackEvidenceRequest:
    root: Path
    path: Path
    expected_digest: str
    evaluation_as_of: str
    enabled_sources: tuple[str, ...]


__all__ = ["FeedbackEvidence", "FeedbackEvidenceRequest"]

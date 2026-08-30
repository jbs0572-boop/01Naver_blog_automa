from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ManifestBuildInput:
    root: Path
    keyword: str
    run_id: str
    topic_id: str
    created_at: str


__all__ = ["ManifestBuildInput"]

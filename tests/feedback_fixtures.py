from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol, override

from tools.contract_types import JSONMap, JSONValue

FIXTURE_SOURCE_IDS: Final[tuple[str, ...]] = (
    "naver-datalab",
    "naver-search-ads",
    "naver-blog-stats",
    "blackkiwi-manual",
    "creator-advisor-link",
)
FIXTURE_FILENAMES: Final[dict[str, str]] = {
    "naver-datalab": "datalab.json",
    "naver-search-ads": "search-ads.json",
    "naver-blog-stats": "blog-stats.json",
    "blackkiwi-manual": "blackkiwi-manual.json",
    "creator-advisor-link": "creator-advisor-link.json",
}
DEFAULT_FIXTURE_ROOT: Final[Path] = Path(__file__).with_name("fixtures") / "feedback"


@dataclass(frozen=True, slots=True)
class FixtureRequest:
    source_id: str


@dataclass(frozen=True, slots=True)
class FixtureReplay:
    source_id: str
    payload: JSONMap


@dataclass(slots=True)
class UnknownFixtureSourceError(Exception):
    source_id: str

    @override
    def __str__(self) -> str:
        return "unknown frozen fixture source"


@dataclass(slots=True)
class InvalidFixturePayloadError(Exception):
    fixture_name: str

    @override
    def __str__(self) -> str:
        return "frozen fixture payload is invalid"


class FeedbackFixtureTransport(Protocol):
    def fetch(self, request: FixtureRequest) -> FixtureReplay: ...


@dataclass(slots=True)
class FrozenFeedbackTransport:
    root: Path = DEFAULT_FIXTURE_ROOT
    _requests: list[FixtureRequest] = field(default_factory=list, init=False)

    @property
    def call_count(self) -> int:
        return len(self._requests)

    @property
    def requests(self) -> tuple[FixtureRequest, ...]:
        return tuple(self._requests)

    def fetch(self, request: FixtureRequest) -> FixtureReplay:
        fixture_name = FIXTURE_FILENAMES.get(request.source_id)
        if fixture_name is None:
            raise UnknownFixtureSourceError(request.source_id)
        payload = _read_fixture(self.root / fixture_name, fixture_name)
        source_id = payload.get("source_id")
        if source_id != request.source_id:
            raise InvalidFixturePayloadError(fixture_name)
        self._requests.append(request)
        return FixtureReplay(request.source_id, payload)


def _read_fixture(path: Path, fixture_name: str) -> JSONMap:
    try:
        raw_value: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InvalidFixturePayloadError(fixture_name) from error
    match raw_value:
        case dict() as payload:
            return payload
        case _:
            raise InvalidFixturePayloadError(fixture_name)

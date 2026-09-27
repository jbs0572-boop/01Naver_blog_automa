from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AssetMetadata:
    artifact_role: str
    original_sha256: str
    order: int


@dataclass(frozen=True, slots=True)
class TextBlock:
    content: str


@dataclass(frozen=True, slots=True)
class HeadingBlock:
    level: int
    content: str


@dataclass(frozen=True, slots=True)
class ListBlock:
    items: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TableCell:
    key: str
    value: str

    @property
    def content(self) -> str:
        return f"{self.key}: {self.value}"


@dataclass(frozen=True, slots=True)
class TableBlock:
    title: str
    rows: tuple[tuple[TableCell, ...], ...]


@dataclass(frozen=True, slots=True)
class ImageBlock:
    filename: str
    alt: str
    representative: bool
    caption: str


@dataclass(frozen=True, slots=True)
class BlankBlock:
    pass


type ParsedBlock = (
    TextBlock | HeadingBlock | ListBlock | TableBlock | ImageBlock | BlankBlock
)


@dataclass(frozen=True, slots=True)
class ParsedNotionCopy:
    title: str
    blocks: tuple[ParsedBlock, ...]

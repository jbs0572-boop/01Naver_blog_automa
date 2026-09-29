from __future__ import annotations

import json
import re
from pathlib import Path


def image_tag(filename: str, alt: str, representative: bool = False) -> str:
    attrs = f"file={json.dumps(filename)} alt={json.dumps(alt, ensure_ascii=False)}"
    return f"[IMAGE {attrs} representative={str(representative).lower()}]\n[ALT]{alt}[/ALT]"


def image_mapping(source: str) -> tuple[dict[str, str], str, str]:
    slots: dict[str, str] = {}
    thumbnail: tuple[str, str] | None = None
    for line in source.splitlines():
        cells = [part.strip().strip("`") for part in line.strip("|").split("|")]
        if len(cells) >= 4 and re.fullmatch(r"VS-\d+", cells[1]):
            if cells[1] in slots:
                raise ValueError("duplicate visual slot")
            slots[cells[1]] = cells[3]
        elif len(cells) >= 4 and cells[0] == "[THUMBNAIL]":
            if thumbnail is not None:
                raise ValueError("duplicate thumbnail")
            thumbnail = cells[2], cells[3]
    if not slots or thumbnail is None:
        raise ValueError("missing image mapping or thumbnail")
    for filename in (*slots.values(), thumbnail[0]):
        if Path(filename).name != filename or filename in {".", "..", ""}:
            raise ValueError("unsafe image filename")
    return slots, *thumbnail


def table(chunk: str) -> str:
    rows = [
        tuple(cell.strip() for cell in line.strip("|").split("|"))
        for line in chunk.splitlines()
    ]
    if len(rows) < 3 or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in rows[1]):
        raise ValueError("unsupported table separator")
    if any(len(row) != len(rows[0]) for row in rows):
        raise ValueError("ragged table")
    if any(not cell or ";" in cell or "=" in cell for row in rows for cell in row):
        raise ValueError("ambiguous table cell")
    body = [
        "[ROW]"
        + "; ".join(f"{key}={value}" for key, value in zip(rows[0], row, strict=True))
        + "[/ROW]"
        for row in rows[2:]
    ]
    return "\n".join(['[TABLE title="본문 표"]', *body, "[/TABLE]"])


def render(draft: str, image_map: str, keyword: str) -> dict[str, str]:
    if not draft.startswith("# ") or Path(keyword).name != keyword:
        raise ValueError("title or safe keyword required")
    slots, thumbnail, thumb_alt = image_mapping(image_map)
    title, _, body = draft.partition("\n")
    tags = [f"[TITLE]{title[2:]}[/TITLE]", image_tag(thumbnail, thumb_alt, True)]
    replacements: dict[str, str] = {}
    seen: set[str] = set()
    for chunk in re.split(r"\n\s*\n", body.strip()):
        if chunk == "---":
            tags.append("[BLANK]")
        elif chunk.startswith("[IMAGE:"):
            if not chunk.endswith("]"):
                raise ValueError("unclosed image marker")
            fields = dict(
                part.strip().split("=", 1)
                for part in chunk[7:-1].split(";")
                if "=" in part
            )
            slot, alt = fields.get("visual_slot_id", ""), fields.get("alt", "")
            if slot not in slots or slot in seen or not alt:
                raise ValueError("missing, repeated or unmapped visual slot")
            seen.add(slot)
            replacements[chunk] = f"![{alt}](../assets/{keyword}/{slots[slot]})"
            tags.append(image_tag(slots[slot], alt))
        elif heading := re.fullmatch(r"(#{2,3}) (.+)", chunk):
            tags.append(f"[HEADING level={len(heading[1])}]{heading[2]}[/HEADING]")
        elif chunk.startswith("|"):
            tags.append(table(chunk))
        elif re.match(r"(?:- |\d+\. )", chunk):
            items: list[str] = []
            for line in chunk.splitlines():
                item = re.fullmatch(r"(?:- |\d+\. )(.+)", line)
                if item is None:
                    raise ValueError("unsupported list continuation")
                items.append(f"[ITEM]{item[1]}[/ITEM]")
            tags.append("\n".join(["[LIST]", *items, "[/LIST]"]))
        else:
            if chunk.startswith(("# ", "####", "```", "[IMAGE")):
                raise ValueError("unsupported Markdown block")
            tags.append(f"[TEXT]{chunk}[/TEXT]")
    if seen != set(slots):
        raise ValueError("unused visual slot")
    final = draft
    for marker, replacement in replacements.items():
        final = final.replace(marker, replacement)
    bracket = "\n".join(tags) + "\n"
    source_heading = "[HEADING level=2]정보 출처[/HEADING]"
    before, separator, sources = bracket.partition(source_heading)
    public = (
        before
        + separator
        + re.sub(
            r"(?m)^(\[ITEM\]\[[^\]\n]+\]\([^\)\n]+\)) — .*\[/ITEM\]$",
            r"\1[/ITEM]",
            sources,
        )
    )
    return {
        f"{keyword}.md": final,
        **{
            f"{keyword}-naver-{suffix}.md": bracket if suffix == "copy" else public
            for suffix in ("layout", "copy", "input")
        },
    }


def render_files(root: Path, keyword: str) -> None:
    outputs = render(
        (root / "drafts" / f"{keyword}.md").read_text(),
        (root / "assets" / keyword / "image-map.md").read_text(),
        keyword,
    )
    output_dir = root / "final"
    output_dir.mkdir(exist_ok=False)
    for name, content in outputs.items():
        _ = (output_dir / name).write_text(content, encoding="utf-8")

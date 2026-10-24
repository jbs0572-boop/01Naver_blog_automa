"""Focused valid/invalid input tests for manifest parsing and ordering."""

from datetime import timedelta

import pytest

from tools.contract_types import ContractError
from tools.manifest_parsing import (
    as_map, aware_datetime, ensure_canonical_layout,
    integer_field, load_json_map, text_field,
)


def canonical_entries():
    return (
        ("final_markdown", "final/topic.md", 1),
        ("naver_layout", "final/topic-naver-layout.md", 2),
        ("naver_copy", "final/topic-naver-copy.md", 3),
        ("image_map", "assets/topic/image-map.md", 4),
        ("body_image", "assets/topic/part-1.png", 5),
        ("thumbnail", "assets/topic/thumbnail.png", 6),
    )


def test_map_requires_object_and_preserves_values():
    mapping = {"one": 1}
    assert as_map(mapping, "test") is mapping
    with pytest.raises(ContractError, match="JSON object required"):
        as_map([1], "test")


def test_load_json_map_rejects_missing_corrupt_and_nonobject(tmp_path):
    path = tmp_path / "metadata.json"
    with pytest.raises(ContractError, match="could not load manifest"):
        load_json_map(path)
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ContractError, match="could not load manifest"):
        load_json_map(path)
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ContractError, match="JSON object required"):
        load_json_map(path)
    path.write_text('{"ok": 1}', encoding="utf-8")
    assert load_json_map(path) == {"ok": 1}


@pytest.mark.parametrize("value", [None, False, True, "", 2, [], {}])
def test_text_field_rejects_empty_and_nonstrings(value):
    with pytest.raises(ContractError, match="missing or invalid string field"):
        text_field({"field": value}, "field")


def test_text_field_preserves_nonempty_unicode():
    assert text_field({"field": "한글"}, "field") == "한글"


@pytest.mark.parametrize("value", [None, False, True, "5", 1.0, [], {}])
def test_integer_field_does_not_accept_booleans_or_coercions(value):
    with pytest.raises(ContractError, match="missing or invalid integer field"):
        integer_field({"count": value}, "count")


def test_integer_field_accepts_signed_integer():
    assert integer_field({"count": -4}, "count") == -4


@pytest.mark.parametrize("stamp", ["not-a-date", "2026-13-01T12:00:00+09:00", "2026-10-24T02:00:00"])
def test_datetime_rejects_invalid_or_naive_values(stamp):
    with pytest.raises(ContractError, match="must (be ISO-8601|include a timezone)"):
        aware_datetime(stamp, "created_at")


def test_datetime_preserves_aware_offset():
    parsed = aware_datetime("2026-10-24T02:00:00+09:00", "created_at")
    assert parsed.utcoffset() == timedelta(hours=9)


def test_canonical_layout_accepts_expected_manifest():
    ensure_canonical_layout(canonical_entries())


@pytest.mark.parametrize("changes, message", [
    ({0: ("body_image", "final/topic.md", 1)}, "roles"),
    ({4: ("extra", "assets/topic/part-1.png", 5)}, "body images"),
    ({3: ("image_map", "assets/topic/image-map.md", 99)}, "orders"),
    ({0: ("final_markdown", "drafts/topic.md", 1)}, "final markdown path"),
    ({0: ("final_markdown", "final/topic.txt", 1)}, "final markdown path"),
    ({1: ("naver_layout", "final/other-naver-layout.md", 2)}, "final artifact paths"),
    ({4: ("body_image", "assets/other/part-1.png", 5)}, "image path"),
    ({5: ("thumbnail", "assets/topic/thumbnail.webp", 6)}, "thumbnail path"),
])
def test_canonical_layout_rejects_each_contract_violation(changes, message):
    entries = list(canonical_entries())
    for index, replacement in changes.items():
        entries[index] = replacement
    with pytest.raises(ContractError, match=message):
        ensure_canonical_layout(tuple(entries))


def test_canonical_layout_rejects_empty_or_missing_thumbnail():
    with pytest.raises(ContractError, match="roles"):
        ensure_canonical_layout(())
    with pytest.raises(ContractError, match="roles"):
        ensure_canonical_layout(canonical_entries()[:-1])


def test_canonical_layout_accepts_no_body_images_and_jpeg_thumbnail():
    entries = list(canonical_entries())
    entries.pop(4)
    entries[-1] = ("thumbnail", "assets/topic/thumbnail.jpeg", 5)
    ensure_canonical_layout(tuple(entries))

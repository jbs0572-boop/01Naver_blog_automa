from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.naver_smarteditor import (
    build_prepare_script,
    build_save_script,
    expected_signature,
    image_files,
    layout_digest,
    plan_payload,
    table_matrix,
    visible_text,
)
from tools.notion_content_models import TableBlock, TableCell
from tools.notion_copy_parser import parse_naver_copy_text


def _map_list(value: JSONValue) -> list[JSONMap]:
    assert isinstance(value, list)
    result: list[JSONMap] = []
    for item in value:
        assert isinstance(item, dict)
        result.append(item)
    return result


CANONICAL = """[TITLE]
테스트 제목
[/TITLE]
[IMAGE file="thumbnail.png" alt="대표" representative=true]
[TEXT]
첫 **문단**과 [공식](https://example.test/source)
[/TEXT]
[HEADING level=2] 소제목 [/HEADING]
[LIST]
[ITEM] 하나 [/ITEM]
[ITEM] 둘 [/ITEM]
[/LIST]
[TABLE title="비교"]
[ROW] 구분=일반; 기준=6000; 혜택=6% [/ROW]
[ROW] 구분=우대; 기준=3600; 혜택=12% [/ROW]
[/TABLE]
[IMAGE file="image-01.png" alt="본문" representative=false]
[TEXT] 마지막 문단 [/TEXT]
"""


def test_visible_text_removes_inline_markdown_without_losing_url() -> None:
    assert visible_text("첫 **문단**과 [공식](https://example.test/source)") == (
        "첫 문단과 공식 (https://example.test/source)"
    )


def test_table_matrix_adds_header_row() -> None:
    block = TableBlock(
        "비교",
        (
            (
                TableCell("구분", "일반"),
                TableCell("기준", "6000"),
                TableCell("혜택", "6%"),
            ),
            (
                TableCell("구분", "우대"),
                TableCell("기준", "3600"),
                TableCell("혜택", "12%"),
            ),
        ),
    )

    assert table_matrix(block) == (
        ("구분", "기준", "혜택"),
        ("일반", "6000", "6%"),
        ("우대", "3600", "12%"),
    )


def test_table_matrix_preserves_two_column_tables() -> None:
    block = TableBlock(
        "상품 확인",
        (
            (TableCell("구분", "상품명"), TableCell("공식 확인", "후드립T")),
            (TableCell("구분", "상품번호"), TableCell("공식 확인", "488298")),
        ),
    )

    assert table_matrix(block) == (
        ("구분", "공식 확인"),
        ("상품명", "후드립T"),
        ("상품번호", "488298"),
    )


def test_signature_preserves_smarteditor_block_order() -> None:
    document = parse_naver_copy_text(CANONICAL)

    signature = expected_signature(document)

    assert signature["title"] == "테스트 제목"
    blocks = _map_list(signature["blocks"])
    assert [block["kind"] for block in blocks] == [
        "image",
        "text",
        "heading",
        "list",
        "table",
        "image",
        "text",
    ]
    assert blocks[4]["rows"] == [
        ["구분", "기준", "혜택"],
        ["일반", "6000", "6%"],
        ["우대", "3600", "12%"],
    ]
    assert layout_digest(signature).startswith("sha256:")


def test_image_files_requires_every_declared_asset(tmp_path: Path) -> None:
    document = parse_naver_copy_text(CANONICAL)
    _ = (tmp_path / "thumbnail.png").write_bytes(b"thumbnail")

    with pytest.raises(ContractError, match="image is missing"):
        _ = image_files(document, tmp_path)


def test_plan_payload_uses_staged_image_paths(tmp_path: Path) -> None:
    document = parse_naver_copy_text(CANONICAL)
    staged = {
        "thumbnail.png": tmp_path / "thumbnail.png",
        "image-01.png": tmp_path / "image-01.png",
    }

    payload = plan_payload(document, staged)

    blocks = _map_list(payload["blocks"])
    image_steps = [block for block in blocks if block["kind"] == "image"]
    assert image_steps[0]["path"] == str(tmp_path / "thumbnail.png")
    assert image_steps[0]["representative"] is True
    assert image_steps[1]["representative"] is False


def test_scripts_are_bound_to_semantic_smarteditor_actions(tmp_path: Path) -> None:
    document = parse_naver_copy_text(CANONICAL)
    staged = {
        "thumbnail.png": tmp_path / "thumbnail.png",
        "image-01.png": tmp_path / "image-01.png",
    }
    signature = expected_signature(document)
    config: JSONMap = {
        "writeUrl": "https://blog.naver.com/example/postwrite",
        "authLocator": "button.save",
        "saveLocator": "button.save",
        "titleLocator": ".se-documentTitle .se-text-paragraph",
        "bodyLocator": ".se-component.se-text .se-text-paragraph",
        "draftListLocator": "button.save-count",
    }
    prepare_payload: JSONMap = {
        "config": config,
        "plan": plan_payload(document, staged),
        "expected": signature,
        "evidencePath": str(tmp_path / "prepared.png"),
    }
    save_payload: JSONMap = {
        "config": config,
        "title": document.title,
        "targetId": "target-1",
        "expected": signature,
        "evidencePath": str(tmp_path / "saved.png"),
    }
    prepare = build_prepare_script(prepare_payload)
    save = build_save_script(save_payload)

    assert "openTab" in prepare
    assert "const page = await openTab(payload.config.writeUrl);" in prepare
    assert "tabsBefore" not in prepare
    assert "reusable" not in prepare
    assert "attachBrowserTab" not in prepare
    assert "const targetIdsBefore = new Set" in prepare
    assert "const tabsAfter = await listBrowserTabs();" in prepare
    assert "!targetIdsBefore.has(tab.targetId)" in prepare
    assert "tab.url === payload.config.writeUrl" in prepare
    assert "newPostwriteTabs.length !== 1" in prepare
    assert "tab.active" not in prepare
    assert "page.url()" not in prepare
    assert "path.join(pwd, 'uploads')" in prepare
    assert "fs.copyFile" in prepare
    assert "const titleBox = await title.boundingBox()" in prepare
    assert "await title.click()" in prepare
    assert "await page.mouse.click(titleBox.x + 30" in prepare
    assert "await page.keyboard.insertText(payload.plan.title)" in prepare
    assert "setInputFiles" in prepare
    assert "se-toolbar-option-text-format-sectionTitle-button" in prepare
    assert "se-toolbar-option-list-bullet-button" in prepare
    assert "const waitForNormalizedText" in prepare
    assert "markup: element.innerHTML" in prepare
    assert "stableMatches" in prepare
    assert "const focusAtEnd" in prepare
    assert "const paragraphIsEmpty" in prepare
    assert "const pressEnterForBodyTail" in prepare
    assert "const pressEnterUntilCountGrows" in prepare
    assert "await page.keyboard.press('Enter')" in prepare
    assert "for (let enterAttempt = 0; enterAttempt < 3" in prepare
    assert "await waitForNormalizedText(title, payload.plan.title" in prepare
    assert "await focusAtEnd(paragraph)" in prepare
    assert "paragraph.press('Enter')" not in prepare
    assert "item.press('Enter')" not in prepare
    assert (
        "const activeList = listComponent.locator('ul, ol').last()" in prepare
    )
    assert "const listParagraphs = () => activeList.locator(':scope > li .se-text-paragraph')" in prepare
    assert "await waitForNormalizedText(item, block.items[index]" in prepare
    assert "const item = tailParagraph()" not in prepare
    assert "Naver list mode did not exit" in prepare
    assert "module.lastElementChild?.tagName === 'P'" in prepare
    assert "se-table-toolbar-button" in prepare
    assert prepare.count("button.se-canvas-bottom-button") == 1
    assert "Naver table requires at least 3x2 cells" in prepare
    assert "Naver table row was not added" in prepare
    assert "Naver table column was not added" in prepare
    assert "name: '삭제'" in prepare
    assert "table.getByRole" not in prepare
    assert "locator(payload.config.saveLocator).click()" not in prepare
    assert "attachBrowserTab" in save
    assert "tab.targetId === payload.targetId" in save
    assert (
        "const page = getTabByTargetId(target.targetId) ?? "
        + "await attachBrowserTab(target.targetId);"
        in save
    )
    assert "const canonicalize = value =>" in save
    assert "JSON.stringify(canonicalize(beforeSave))" in save
    assert "JSON.stringify(canonicalize(afterSave))" in save
    assert "locator(payload.config.saveLocator).click()" in save
    assert "발행" not in prepare + save

    for index, script in enumerate((prepare, save)):
        path = tmp_path / f"script-{index}.js"
        _ = path.write_text(
            f"async function test() {{\n{script}\n}}\n", encoding="utf-8"
        )
        completed = subprocess.run(
            ["node", "--check", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr

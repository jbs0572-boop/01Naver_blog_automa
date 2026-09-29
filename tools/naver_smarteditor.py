from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.notion_content_models import (
    BlankBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    ParsedNotionCopy,
    TableBlock,
    TextBlock,
)

_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_FORMAT_MARKERS = re.compile(r"(\*\*|__|`)")


def visible_text(value: str) -> str:
    linked = _LINK.sub(lambda match: f"{match.group(1)} ({match.group(2)})", value)
    plain = _FORMAT_MARKERS.sub("", linked)
    return " ".join(plain.split())


def table_matrix(block: TableBlock) -> tuple[tuple[str, ...], ...]:
    if not block.rows:
        raise ContractError("Naver table must contain at least one row")
    headers = tuple(cell.key for cell in block.rows[0])
    if not headers or len(headers) < 2:
        raise ContractError("Naver SmartEditor tables require at least two columns")
    rows: list[tuple[str, ...]] = [tuple(visible_text(value) for value in headers)]
    for row in block.rows:
        keys = tuple(cell.key for cell in row)
        if keys != headers:
            raise ContractError("Naver table row keys must be consistent")
        rows.append(tuple(visible_text(cell.value) for cell in row))
    if len(rows) < 3:
        raise ContractError("Naver SmartEditor tables require at least three rows")
    return tuple(rows)


def _json_strings(values: tuple[str, ...]) -> list[JSONValue]:
    return [value for value in values]


def _json_rows(values: tuple[tuple[str, ...], ...]) -> list[JSONValue]:
    return [_json_strings(row) for row in values]


def expected_signature(document: ParsedNotionCopy) -> JSONMap:
    blocks: list[JSONValue] = []
    for block in document.blocks:
        entry: JSONMap
        match block:
            case TextBlock(content=content):
                text = visible_text(content)
                if text:
                    entry = {"kind": "text", "text": text}
                    blocks.append(entry)
            case HeadingBlock(content=content):
                entry = {"kind": "heading", "text": visible_text(content)}
                blocks.append(entry)
            case ListBlock(items=items):
                visible_items = tuple(visible_text(item) for item in items)
                entry = {"kind": "list", "items": _json_strings(visible_items)}
                blocks.append(entry)
            case TableBlock():
                entry = {"kind": "table", "rows": _json_rows(table_matrix(block))}
                blocks.append(entry)
            case ImageBlock(filename=filename, representative=representative):
                entry = {
                    "kind": "image",
                    "filename": filename,
                    "representative": representative,
                }
                blocks.append(entry)
                if block.caption:
                    caption: JSONMap = {
                        "kind": "text",
                        "text": visible_text(block.caption),
                    }
                    blocks.append(caption)
            case BlankBlock():
                continue
    result: JSONMap = {"title": visible_text(document.title), "blocks": blocks}
    return result


def layout_digest(signature: JSONMap) -> str:
    encoded = json.dumps(
        signature, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def image_files(document: ParsedNotionCopy, asset_dir: Path) -> dict[str, Path]:
    resolved: dict[str, Path] = {}
    for block in document.blocks:
        if not isinstance(block, ImageBlock):
            continue
        if Path(block.filename).name != block.filename:
            raise ContractError("Naver image filename must be a basename")
        candidate = asset_dir / block.filename
        if not candidate.is_file():
            raise ContractError(f"Naver image is missing: {candidate}")
        resolved[block.filename] = candidate
    if not resolved:
        raise ContractError("Naver document requires at least one image")
    return resolved


def plan_payload(
    document: ParsedNotionCopy,
    uploaded_images: dict[str, Path],
) -> JSONMap:
    blocks: list[JSONValue] = []
    for block in document.blocks:
        entry: JSONMap
        match block:
            case TextBlock(content=content):
                entry = {"kind": "text", "text": visible_text(content)}
                blocks.append(entry)
            case HeadingBlock(content=content):
                entry = {"kind": "heading", "text": visible_text(content)}
                blocks.append(entry)
            case ListBlock(items=items):
                visible_items = tuple(visible_text(item) for item in items)
                entry = {"kind": "list", "items": _json_strings(visible_items)}
                blocks.append(entry)
            case TableBlock():
                entry = {"kind": "table", "rows": _json_rows(table_matrix(block))}
                blocks.append(entry)
            case ImageBlock(filename=filename, representative=representative):
                staged = uploaded_images.get(filename)
                if staged is None:
                    raise ContractError(f"Naver staged image is missing: {filename}")
                entry = {
                    "kind": "image",
                    "filename": filename,
                    "path": str(staged),
                    "representative": representative,
                }
                blocks.append(entry)
                if block.caption:
                    caption: JSONMap = {
                        "kind": "text",
                        "text": visible_text(block.caption),
                    }
                    blocks.append(caption)
            case BlankBlock():
                entry = {"kind": "blank"}
                blocks.append(entry)
    result: JSONMap = {"title": visible_text(document.title), "blocks": blocks}
    return result


def _payload_json(value: JSONMap) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_prepare_script(payload: JSONMap) -> str:
    template = r"""
const payload = __PAYLOAD__;
const normalize = value => String(value ?? '').replace(/\u200b/g, '').replace(/\s+/g, ' ').trim();
const waitForNormalizedText = async (locator, expected, message, recoverFromRerender = false) => {
  const wanted = normalize(expected);
  let activeLocator = locator;
  let previousMarkup = null;
  let stableMatches = 0;
  for (let attempt = 0; attempt < 400; attempt += 1) {
    let state = await activeLocator.evaluate(element => ({ text: element.textContent, markup: element.innerHTML }));
    if (recoverFromRerender && normalize(state.text) !== wanted) {
      const paragraphs = root.locator('.se-component.se-text .se-text-paragraph');
      let replacement = null;
      for (let index = 0; index < await paragraphs.count(); index += 1) {
        const candidate = paragraphs.nth(index);
        const candidateState = await candidate.evaluate(element => ({ text: element.textContent, markup: element.innerHTML }));
        if (normalize(candidateState.text) === wanted) {
          replacement = candidate;
          state = candidateState;
          break;
        }
      }
      if (replacement) activeLocator = replacement;
    }
    if (normalize(state.text) === wanted) {
      stableMatches = state.markup === previousMarkup ? stableMatches + 1 : 0;
      previousMarkup = state.markup;
      if (stableMatches >= 6) return;
    } else {
      previousMarkup = null;
      stableMatches = 0;
    }
    await sleep(50);
  }
  const actual = normalize(await activeLocator.textContent());
  throw new Error(`${message}: expected=${wanted} actual=${actual}`);
};
const focusAtEnd = async locator => {
  await locator.evaluate(element => {
    const range = element.ownerDocument.createRange();
    range.selectNodeContents(element);
    range.collapse(false);
    const selection = element.ownerDocument.getSelection();
    if (!selection) throw new Error('Naver editor selection is unavailable');
    selection.removeAllRanges();
    selection.addRange(range);
    element.focus();
  });
};
const paragraphIsEmpty = async locator => await locator.evaluate(element => {
  const copy = element.cloneNode(true);
  for (const placeholder of copy.querySelectorAll('.se-placeholder')) placeholder.remove();
  return String(copy.textContent ?? '').replace(/\u200b/g, '').trim().length === 0;
});
const sessionUploadDir = path.join(pwd, 'uploads');
const sessionEvidencePath = path.join(pwd, 'prepared.png');
await fs.mkdir(sessionUploadDir, { recursive: true });
for (const block of payload.plan.blocks) {
  if (block.kind !== 'image') continue;
  if (path.basename(block.filename) !== block.filename) throw new Error('Naver image filename is invalid');
  const sessionPath = path.join(sessionUploadDir, block.filename);
  await fs.copyFile(block.path, sessionPath);
  block.path = sessionPath;
}
const targetIdsBefore = new Set((await listBrowserTabs()).map(tab => tab.targetId));
const page = await openTab(payload.config.writeUrl);
await page.bringToFront();
const editor = page.frames().find(frame => frame.name() === 'mainFrame') ?? page;
const recovery = editor.getByText('작성 중인 글이 있습니다.', { exact: true });
if (await recovery.count() && await recovery.first().isVisible()) {
  // Dashboard is the sole supervised entry point for this browser session.
  if (!payload.discardRecovery) throw new Error('Naver unsaved recovery prompt requires operator review');
  await editor.getByRole('button', { name: '취소', exact: true }).last().click();
}
await editor.locator(payload.config.authLocator).waitFor({ state: 'visible', timeout: 30000 });
const root = editor.locator('article.se-components-wrap');
await root.waitFor({ state: 'visible', timeout: 30000 });
const meaningfulBefore = await root.locator(':scope > .se-component').evaluateAll(elements => elements.filter(element => {
  if (element.classList.contains('se-documentTitle')) return false;
  if (element.classList.contains('se-text')) {
    return Array.from(element.querySelectorAll('.se-text-paragraph')).some(paragraph => Array.from(paragraph.querySelectorAll('span:not(.se-placeholder)')).some(span => (span.textContent ?? '').replace(/\u200b/g, '').trim().length > 0));
  }
  return element.classList.contains('se-image') || element.classList.contains('se-table') || element.classList.contains('se-sectionTitle');
}).length);
if (meaningfulBefore !== 0) throw new Error('Naver new document is not blank');
const modeButton = editor.locator('button.se-util-button.__mode-button');
if (await modeButton.count()) {
  for (let attempt = 0; attempt < 3 && !(await modeButton.getAttribute('class') ?? '').includes('se-util-button-device-desktop'); attempt += 1) await modeButton.click();
  if (!(await modeButton.getAttribute('class') ?? '').includes('se-util-button-device-desktop')) throw new Error('Naver desktop editing mode is unavailable');
}
const title = editor.locator(payload.config.titleLocator).first();
const currentTitle = normalize(await title.textContent());
if (currentTitle !== normalize(payload.plan.title)) {
  if (currentTitle !== '' && currentTitle !== '제목') throw new Error('Naver recovered title does not match this run');
  const titleBox = await title.boundingBox();
  if (!titleBox) throw new Error('Naver title position is unavailable');
  await title.click();
  await page.mouse.click(titleBox.x + 30, titleBox.y + titleBox.height / 2);
  await page.keyboard.insertText(payload.plan.title);
}
await waitForNormalizedText(title, payload.plan.title, 'Naver title did not settle');
const tailParagraph = () => editor.locator('.se-component.se-text .se-text-paragraph').last();
const bodyTailIsReady = async () => {
  const components = root.locator(':scope > .se-component');
  if (!(await components.count()) || await components.last().getAttribute('data-a11y-title') !== '본문') return false;
  const candidate = tailParagraph();
  return await candidate.count() > 0 && await paragraphIsEmpty(candidate) && await candidate.evaluate(element => element.closest('li') === null);
};
const waitForBodyTail = async (message, attempts = 200) => {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (await bodyTailIsReady()) return tailParagraph();
    await sleep(50);
  }
  throw new Error(message);
};
const pressEnterForBodyTail = async (paragraph, message) => {
  for (let enterAttempt = 0; enterAttempt < 3; enterAttempt += 1) {
    await focusAtEnd(paragraph);
    await page.keyboard.press('Enter');
    for (let attempt = 0; attempt < 20; attempt += 1) {
      if (await bodyTailIsReady()) return tailParagraph();
      await sleep(50);
    }
  }
  throw new Error(message);
};
const pressEnterUntilCountGrows = async (target, countLocator, message) => {
  const before = await countLocator.count();
  for (let enterAttempt = 0; enterAttempt < 3; enterAttempt += 1) {
    await focusAtEnd(target);
    await page.keyboard.press('Enter');
    for (let attempt = 0; attempt < 20; attempt += 1) {
      if (await countLocator.count() > before) return;
      await sleep(50);
    }
  }
  throw new Error(message);
};
const ensureTextTail = async () => {
  const components = root.locator(':scope > .se-component');
  const lastType = await components.last().getAttribute('data-a11y-title');
  if (lastType !== '본문') await editor.locator('button.se-canvas-bottom-button').click();
  return await waitForBodyTail('Naver body paragraph was not created');
};
for (const block of payload.plan.blocks) {
  if (block.kind === 'blank') {
    const paragraph = await ensureTextTail();
    if (!(await paragraphIsEmpty(paragraph))) throw new Error('Naver blank block requires an empty body paragraph');
    await pressEnterUntilCountGrows(paragraph, editor.locator('.se-component.se-text .se-text-paragraph'), 'Naver blank paragraph was not created');
    continue;
  }
  if (block.kind === 'text') {
    const paragraph = await ensureTextTail();
    await paragraph.click();
    await paragraph.pressSequentially(block.text);
    await waitForNormalizedText(paragraph, block.text, 'Naver text did not settle', true);
    await pressEnterForBodyTail(paragraph, 'Naver text tail was not created');
    continue;
  }
  if (block.kind === 'heading') {
    const paragraph = await ensureTextTail();
    await paragraph.click();
    await editor.locator('button.se-text-format-toolbar-button').click();
    await editor.locator('button.se-toolbar-option-text-format-sectionTitle-button').click();
    const heading = editor.locator('.se-component.se-sectionTitle .se-text-paragraph').last();
    await heading.click();
    await heading.pressSequentially(block.text);
    await waitForNormalizedText(heading, block.text, 'Naver heading did not settle');
    await pressEnterForBodyTail(heading, 'Naver heading tail was not created');
    continue;
  }
  if (block.kind === 'list') {
    const paragraph = await ensureTextTail();
    if (!(await paragraphIsEmpty(paragraph))) throw new Error('Naver list requires an empty body paragraph');
    const listComponent = root.locator(':scope > .se-component.se-text').last();
    const beforeLists = await listComponent.locator('ul, ol').count();
    await paragraph.click();
    await editor.locator('button.se-list-bullet-toolbar-button').click();
    await editor.locator('button.se-toolbar-option-list-bullet-button').click();
    for (let attempt = 0; attempt < 200 && await listComponent.locator('ul, ol').count() <= beforeLists; attempt += 1) await sleep(50);
    if (await listComponent.locator('ul, ol').count() <= beforeLists) throw new Error('Naver list was not created');
    const activeList = listComponent.locator('ul, ol').last();
    const listParagraphs = () => activeList.locator(':scope > li .se-text-paragraph');
    for (let index = 0; index < block.items.length; index += 1) {
      const item = listParagraphs().last();
      await item.pressSequentially(block.items[index]);
      await waitForNormalizedText(item, block.items[index], 'Naver list item did not settle');
      if (index + 1 < block.items.length) {
        await pressEnterUntilCountGrows(item, listParagraphs(), 'Naver list item was not created');
      }
    }
    const lastItem = listParagraphs().last();
    await pressEnterUntilCountGrows(lastItem, listParagraphs(), 'Naver list exit item was not created');
    const emptyItem = listParagraphs().last();
    if (!(await paragraphIsEmpty(emptyItem))) throw new Error('Naver list exit item was not empty');
    const listModeExited = async () => await listComponent.locator('.se-module-text').last().evaluate(module => module.lastElementChild?.tagName === 'P');
    for (let enterAttempt = 0; enterAttempt < 3 && !(await listModeExited()); enterAttempt += 1) {
      await focusAtEnd(emptyItem);
      await page.keyboard.press('Enter');
      for (let attempt = 0; attempt < 20 && !(await listModeExited()); attempt += 1) await sleep(50);
    }
    if (!(await listModeExited())) throw new Error('Naver list mode did not exit');
    continue;
  }
  if (block.kind === 'image') {
    const paragraph = await ensureTextTail();
    await paragraph.click();
    const before = await editor.locator('.se-component.se-image').count();
    await editor.locator('button.se-image-toolbar-button').click();
    const input = editor.locator('input[type=file]').last();
    await input.setInputFiles(block.path);
    const image = editor.locator('.se-component.se-image').nth(before);
    await editor.locator('.se-component.se-image img').nth(before).waitFor({ state: 'visible', timeout: 30000 });
    await image.click();
    continue;
  }
  if (block.kind === 'table') {
    const rows = block.rows;
    const columns = rows[0].length;
    if (rows.length < 3 || columns < 2) throw new Error('Naver table requires at least 3x2 cells');
    if (rows.some(row => row.length !== columns)) throw new Error('Naver table rows are inconsistent');
    const paragraph = await ensureTextTail();
    await paragraph.click();
    const before = await editor.locator('.se-component.se-table').count();
    await editor.locator('button.se-table-toolbar-button').click();
    const table = editor.locator('.se-component.se-table').nth(before);
    await table.waitFor({ state: 'visible', timeout: 30000 });
    await table.click();
    const waitForGrowth = async (locator, previous, message) => {
      for (let attempt = 0; attempt < 20; attempt += 1) {
        if (await locator.count() > previous) return;
        await sleep(50);
      }
      throw new Error(message);
    };
    while (await table.locator('tr').count() < rows.length) {
      const current = await table.locator('tr').count();
      await table.locator('button.se-cell-add-button').last().click();
      await waitForGrowth(table.locator('tr'), current, 'Naver table row was not added');
    }
    while (await table.locator('tr').first().locator('td').count() > columns) {
      const current = await table.locator('tr').first().locator('td').count();
      await editor.getByRole('button', { name: `${current - 1}열 선택`, exact: true }).last().click();
      await editor.getByRole('button', { name: '삭제', exact: true }).last().click();
    }
    while (await table.locator('tr').first().locator('td').count() < columns) {
      const current = await table.locator('tr').first().locator('td').count();
      await table.locator('button.se-cell-add-button').nth(current - 1).click();
      await waitForGrowth(table.locator('tr').first().locator('td'), current, 'Naver table column was not added');
    }
    if (await table.locator('tr').count() !== rows.length || await table.locator('tr').first().locator('td').count() !== columns) throw new Error('Naver table size mismatch');
    const cells = table.locator('td .se-text-paragraph');
    for (let row = 0; row < rows.length; row += 1) {
      for (let column = 0; column < columns; column += 1) {
        const cell = cells.nth(row * columns + column);
        await cell.click();
        await cell.pressSequentially(rows[row][column]);
        await waitForNormalizedText(cell, rows[row][column], 'Naver table cell did not settle');
      }
    }
    continue;
  }
  throw new Error(`Unsupported Naver block kind: ${block.kind}`);
}
if (await modeButton.count()) {
  for (let attempt = 0; attempt < 3 && !(await modeButton.getAttribute('class') ?? '').includes('se-util-button-device-mobile'); attempt += 1) await modeButton.click();
  if (!(await modeButton.getAttribute('class') ?? '').includes('se-util-button-device-mobile')) throw new Error('Naver mobile verification mode is unavailable');
}
const inspectDocument = async () => {
  const documentTitle = normalize(await editor.locator(payload.config.titleLocator).first().textContent());
  const blocks = await root.locator(':scope > .se-component').evaluateAll(elements => {
    const clean = value => String(value ?? '').replace(/\u200b/g, '').replace(/\s+/g, ' ').trim();
    const output = [];
    for (const element of elements) {
      if (element.classList.contains('se-documentTitle')) continue;
      if (element.classList.contains('se-sectionTitle')) {
        const text = clean(element.querySelector('.se-text-paragraph')?.textContent);
        if (text) output.push({ kind: 'heading', text });
        continue;
      }
      if (element.classList.contains('se-image')) {
        const image = element.querySelector('img');
        const source = image?.getAttribute('alt') || image?.getAttribute('src') || '';
        const filename = source.split('/').pop().split('?')[0];
        const representative = Boolean(element.querySelector('button.se-set-rep-image-button.se-is-selected'));
        output.push({ kind: 'image', filename, representative });
        continue;
      }
      if (element.classList.contains('se-table')) {
        const rows = Array.from(element.querySelectorAll('tr')).map(row => Array.from(row.querySelectorAll('td')).map(cell => clean(cell.querySelector('.se-text-paragraph')?.textContent)));
        output.push({ kind: 'table', rows });
        continue;
      }
      if (element.classList.contains('se-text')) {
        for (const module of element.querySelectorAll('.se-module-text')) {
          for (const child of module.children) {
            if (child.tagName === 'UL' || child.tagName === 'OL') {
              const items = Array.from(child.querySelectorAll(':scope > li')).map(item => clean(item.textContent)).filter(Boolean);
              if (items.length) output.push({ kind: 'list', items });
            } else if (child.tagName === 'P') {
              const text = clean(child.textContent);
              if (text) output.push({ kind: 'text', text });
            }
          }
        }
      }
    }
    return output;
  });
  return { title: documentTitle, blocks };
};
const actual = await inspectDocument();
if (JSON.stringify(actual) !== JSON.stringify(payload.expected)) throw new Error(`Naver layout mismatch: ${JSON.stringify(actual)}`);
await page.screenshot({ path: sessionEvidencePath, fullPage: true, type: 'png' });
await fs.copyFile(sessionEvidencePath, payload.evidencePath);
const tabsAfter = await listBrowserTabs();
const newPostwriteTabs = tabsAfter.filter(tab => !targetIdsBefore.has(tab.targetId) && tab.url === payload.config.writeUrl);
if (newPostwriteTabs.length !== 1) throw new Error('Naver prepared tab target was not uniquely created');
const attached = newPostwriteTabs[0];
console.log('__ASIDE_RESULT__' + JSON.stringify({ target_id: attached.targetId, signature: actual, mode: 'mobile', evidence_path: payload.evidencePath }));
"""
    return template.replace("__PAYLOAD__", _payload_json(payload))


def build_save_script(payload: JSONMap) -> str:
    template = r"""
const payload = __PAYLOAD__;
const normalize = value => String(value ?? '').replace(/\u200b/g, '').replace(/\s+/g, ' ').trim();
const canonicalize = value => Array.isArray(value) ? value.map(canonicalize) : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalize(value[key])])) : value;
const sessionEvidencePath = path.join(pwd, 'saved.png');
const tabsNow = await listBrowserTabs();
let target = tabsNow.find(tab => tab.targetId === payload.targetId);
if (!target) throw new Error('Prepared Naver tab is no longer open');
const page = getTabByTargetId(target.targetId) ?? await attachBrowserTab(target.targetId);
const editor = page.frames().find(frame => frame.name() === 'mainFrame') ?? page;
const root = editor.locator('article.se-components-wrap');
const inspectDocument = async () => {
  const documentTitle = normalize(await editor.locator(payload.config.titleLocator).first().textContent());
  const blocks = await root.locator(':scope > .se-component').evaluateAll(elements => {
    const clean = value => String(value ?? '').replace(/\u200b/g, '').replace(/\s+/g, ' ').trim();
    const output = [];
    for (const element of elements) {
      if (element.classList.contains('se-documentTitle')) continue;
      if (element.classList.contains('se-sectionTitle')) {
        const text = clean(element.querySelector('.se-text-paragraph')?.textContent);
        if (text) output.push({ kind: 'heading', text });
      } else if (element.classList.contains('se-image')) {
        const image = element.querySelector('img');
        const source = image?.getAttribute('alt') || image?.getAttribute('src') || '';
        output.push({ kind: 'image', filename: source.split('/').pop().split('?')[0], representative: Boolean(element.querySelector('button.se-set-rep-image-button.se-is-selected')) });
      } else if (element.classList.contains('se-table')) {
        output.push({ kind: 'table', rows: Array.from(element.querySelectorAll('tr')).map(row => Array.from(row.querySelectorAll('td')).map(cell => clean(cell.querySelector('.se-text-paragraph')?.textContent))) });
      } else if (element.classList.contains('se-text')) {
        for (const module of element.querySelectorAll('.se-module-text')) for (const child of module.children) {
          if (child.tagName === 'UL' || child.tagName === 'OL') {
            const items = Array.from(child.querySelectorAll(':scope > li')).map(item => clean(item.textContent)).filter(Boolean);
            if (items.length) output.push({ kind: 'list', items });
          } else if (child.tagName === 'P') {
            const text = clean(child.textContent);
            if (text) output.push({ kind: 'text', text });
          }
        }
      }
    }
    return output;
  });
  return { title: documentTitle, blocks };
};
const beforeSave = await inspectDocument();
if (JSON.stringify(canonicalize(beforeSave)) !== JSON.stringify(canonicalize(payload.expected))) throw new Error('Prepared Naver layout changed after confirmation');
await editor.locator(payload.config.saveLocator).click();
await editor.getByText('임시저장이 완료되었습니다.', { exact: true }).waitFor({ state: 'visible', timeout: 30000 });
const afterSave = await inspectDocument();
if (JSON.stringify(canonicalize(afterSave)) !== JSON.stringify(canonicalize(payload.expected))) throw new Error('Saved Naver draft layout round-trip mismatch');
await page.screenshot({ path: sessionEvidencePath, fullPage: true, type: 'png' });
await fs.copyFile(sessionEvidencePath, payload.evidencePath);
console.log('__ASIDE_RESULT__' + JSON.stringify({ draft_status: 'saved', naver_title: payload.title, signature: afterSave, evidence_path: payload.evidencePath }));
"""
    return template.replace("__PAYLOAD__", _payload_json(payload))


def require_json_map(value: JSONValue) -> JSONMap:
    if not isinstance(value, dict):
        raise ContractError("Aside Browser returned a non-object result")
    return value


__all__ = [
    "build_prepare_script",
    "build_save_script",
    "expected_signature",
    "image_files",
    "layout_digest",
    "plan_payload",
    "require_json_map",
    "table_matrix",
    "visible_text",
]

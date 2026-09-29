import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const dashboardMutationHeaders = () => ({
  "Content-Type": "application/json",
  "X-Dashboard-CSRF": "test-token",
});

test("security adds the page token to mutation headers", async () => {
  const source = await readFile(new URL("../dashboard/security.js", import.meta.url), "utf8");
  const document = {
    querySelector: (selector) => selector === 'meta[name="dashboard-csrf-token"]'
      ? {getAttribute: () => "page-token"}
      : null,
  };
  const context = vm.createContext({document, window: {}});
  vm.runInContext(source, context);
  const headers = context.window.dashboardMutationHeaders();
  assert.equal(headers["Content-Type"], "application/json");
  assert.equal(headers["X-Dashboard-CSRF"], "page-token");
});

test("progress waits for saved draft and retains unknown usage", async () => {
  const source = await readFile(new URL("../dashboard/progress.js", import.meta.url), "utf8");
  const context = vm.createContext({window: {dashboardMutationHeaders, }});
  vm.runInContext(source, context);
  const stages = ["topic-selector", "researcher", "writer", "image-maker", "content-assembler", "notion-rider", "naver-rider"].map(name => ({name, status: "passed"}));
  const metrics = context.window.RunMetrics;
  assert.equal(metrics.describe({stages, status: "awaiting_user_confirmation"}).percent, 86);
  assert.equal(metrics.describe({stages, status: "draft_saved"}).percent, 100);
  assert.equal(metrics.describe({stages: [], status: "running"}).percent, 0);
  assert.equal(metrics.format(null), "집계 전");
  assert.equal(metrics.format(0), "0");
  assert.equal(metrics.formatDuration(null), "미기록");
  assert.equal(metrics.formatDuration(2500), "2.5초");
  assert.equal(metrics.attemptSummary({total_attempts: 2, last_duration_ms: 1000, duration_ms: 3500}), "총 2회 · 최근 1초 · 누적 3.5초");
});

test("progress counts validated stages and ignores skipped stages", async () => {
  const source = await readFile(new URL("../dashboard/progress.js", import.meta.url), "utf8");
  const context = vm.createContext({window: {dashboardMutationHeaders, }});
  vm.runInContext(source, context);
  const metrics = context.window.RunMetrics;
  const stages = [
    {name: "topic-selector", status: "validated"},
    {name: "researcher", status: "passed"},
    {name: "writer", status: "passed"},
    {name: "image-maker", status: "passed"},
    {name: "content-assembler", status: "failed"},
    {name: "notion-rider", status: "skipped"},
    {name: "naver-rider", status: "skipped"},
  ];

  const description = metrics.describe({stages, status: "failed"});
  assert.equal(description.completed, 4);
  assert.equal(description.percent, 57);
  assert.equal(description.next, "최종 검수에서 멈췄어요");
});

test("progress does not report full completion before draft_saved", async () => {
  const source = await readFile(new URL("../dashboard/progress.js", import.meta.url), "utf8");
  const context = vm.createContext({window: {dashboardMutationHeaders, }});
  vm.runInContext(source, context);
  const stages = ["topic-selector", "researcher", "writer", "image-maker", "content-assembler", "notion-rider", "naver-rider"]
    .map(name => ({name, status: "passed"}));

  assert.equal(context.window.RunMetrics.describe({stages, status: "awaiting_user_confirmation"}).percent, 86);
  assert.equal(context.window.RunMetrics.describe({stages, status: "draft_saved"}).percent, 100);
});

class FakeElement {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.dataset = {};
    this.value = "";
    this.textContent = "";
    this.className = "";
    this.disabled = false;
    this.required = false;
    this.hidden = false;
  }

  addEventListener(type, handler) { this.listeners[type] = handler; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  click() { return this.listeners.click?.({preventDefault() {}}); }
  submit() { return this.listeners.submit?.({preventDefault() {}}); }
}

function batch({topicSource = "auto_selected", children = null, childCount = 1} = {}) {
  return {
    batch_id: "BATCH-01", topic_source: topicSource, as_of_date: "2026-09-07", status: "completed",
    submitted_at: "2026-09-07T00:00:00+00:00", updated_at: "2026-09-07T00:00:01+00:00",
    snapshot: {capture_id: "CAPTURE-01", path: "metadata/creator-advisor/2026-09-07/CAPTURE-01.json", sha256: "sha256:snapshot"},
    children: children ?? Array.from({length: childCount}, (_, index) => ({
      child_id: `CHILD-0${index + 1}`, slot: index + 1, task_id: `BATCH-01-${index + 1}`, run_id: `RUN-0${index + 1}`,
      requested_keyword: null, resolved_keyword: `주제 ${index + 1}`, status: "completed", result_status: "local-only",
      submitted_at: "2026-09-07T00:00:00+00:00", updated_at: "2026-09-07T00:00:01+00:00",
      message: "외부 저장 대기", error: null, retryable: false, selection_context: {}, confirmation_preview: null,
      next_action: {kind: "external", nonce: `nonce-${index + 1}`},
    })),
  };
}

function findByClass(element, className) {
  if (element.className.split(" ").includes(className)) return element;
  for (const child of element.children) {
    const found = findByClass(child, className);
    if (found) return found;
  }
  return null;
}

function findButtons(element) {
  return [element, ...element.children.flatMap(findButtons)].filter((node) => node.tagName === "BUTTON");
}

async function manualHarness(respond) {
  const source = await readFile(new URL("../dashboard/manual-run.js", import.meta.url), "utf8");
  const elements = new Map();
  for (const selector of ["#manual-run-form", "#manual-topic-source", "#manual-keyword-field", "#manual-keyword", "#manual-as-of", "#manual-submit", "#manual-status", "#manual-batch", "#manual-batch-summary", "#manual-batch-list"]) {
    elements.set(selector, new FakeElement(selector === "#manual-submit" ? "button" : "div"));
  }
  elements.get("#manual-topic-source").value = "auto_selected";
  const requests = [];
  const timers = [];
  const stored = new Map();
  let uuidSequence = 0;
  const sessionStorage = {
    getItem: (key) => stored.get(key) ?? null,
    setItem: (key, value) => stored.set(key, String(value)),
    removeItem: (key) => stored.delete(key),
  };
  const documentListeners = {};
  const document = {hidden: false, createElement: (tagName) => new FakeElement(tagName), querySelector: (selector) => elements.get(selector) ?? null, addEventListener: (type, handler) => { documentListeners[type] = handler; }};
  const context = vm.createContext({
    document,
    fetch: async (url, options = {}) => { requests.push([url, options]); return respond(url, options, requests); },
    window: {dashboardMutationHeaders, confirm: () => true, crypto: {randomUUID: () => `11111111-1111-4111-8111-${String(++uuidSequence).padStart(12, "0")}`}, sessionStorage, setTimeout: (handler, delay) => { timers.push({handler, delay}); return timers.length; }, clearTimeout() {}},
  });
  vm.runInContext(source, context);
  return {context, document, documentListeners, elements, requests, timers, stored};
}

test("auto form posts unchanged payload and renders one slot", async () => {
  const view = batch();
  const harness = await manualHarness(async (url) => ({ok: true, status: 202, json: async () => url === "/api/manual-runs?limit=1" ? {batches: []} : view}));
  harness.elements.get("#manual-as-of").value = "2026-09-07";
  await harness.context.window.ManualRunDashboard.start();
  await harness.elements.get("#manual-run-form").submit();
  assert.deepEqual(JSON.parse(harness.requests[1][1].body), {auto_topic: true, as_of_date: "2026-09-07", request_nonce: "11111111-1111-4111-8111-000000000001"});
  assert.equal(harness.requests[1][1].headers["Content-Type"], "application/json");
  assert.equal(harness.elements.get("#manual-batch-list").children.length, 1);
  assert.match(harness.elements.get("#manual-batch-summary").textContent, /한 번의 스냅샷/);
});

test("user form renders one slot", async () => {
  const view = batch({topicSource: "user_defined"});
  view.children[0].requested_keyword = "사용자 주제";
  view.children[0].resolved_keyword = "사용자 주제";
  const harness = await manualHarness(async (url) => ({ok: true, status: 202, json: async () => url === "/api/manual-runs?limit=1" ? {batches: []} : view}));
  harness.elements.get("#manual-topic-source").value = "user_defined";
  harness.elements.get("#manual-keyword").value = "사용자 주제";
  harness.elements.get("#manual-as-of").value = "2026-09-07";
  await harness.context.window.ManualRunDashboard.start();
  await harness.elements.get("#manual-run-form").submit();
  assert.deepEqual(JSON.parse(harness.requests[1][1].body), {keyword: "사용자 주제", as_of_date: "2026-09-07", request_nonce: "11111111-1111-4111-8111-000000000001"});
  assert.equal(harness.elements.get("#manual-batch-list").children.length, 1);
});

test("failed submission keeps its nonce until the request content changes", async () => {
  let posts = 0;
  const harness = await manualHarness(async (url, options) => {
    if (url === "/api/manual-runs?limit=1") return {ok: true, status: 200, json: async () => ({batches: []})};
    posts += 1;
    return {ok: false, status: 503, json: async () => ({error: `offline-${posts}`})};
  });
  harness.elements.get("#manual-as-of").value = "2026-09-07";
  await harness.context.window.ManualRunDashboard.start();
  await harness.elements.get("#manual-run-form").submit();
  await harness.elements.get("#manual-run-form").submit();
  const first = JSON.parse(harness.requests[1][1].body).request_nonce;
  const replay = JSON.parse(harness.requests[2][1].body).request_nonce;
  assert.equal(replay, first);
  assert.equal(harness.stored.size, 1);

  harness.elements.get("#manual-as-of").value = "2026-09-08";
  await harness.elements.get("#manual-run-form").submit();
  const changed = JSON.parse(harness.requests[3][1].body).request_nonce;
  assert.notEqual(changed, first);
});

test("late model settings populate schedule rows without losing pinned presets", async () => {
  const source = await readFile(new URL("../dashboard/model-settings.js", import.meta.url), "utf8");
  const elements = new Map();
  for (const selector of ["#model-preset", "#manual-preset", "#schedule-preset", "#model-stage-settings", "#model-settings-status", "#model-preset-form"]) elements.set(selector, new FakeElement(selector.includes("preset") ? "select" : "div"));
  const scheduleRow = new FakeElement(); scheduleRow.className = "schedule-time"; scheduleRow.dataset.presetId = "alternate";
  const entryPreset = new FakeElement("select"); entryPreset.className = "schedule-entry-preset"; entryPreset.closest = () => scheduleRow;
  const stages = {"topic-selector": {model: "gpt-5.6-luna", reasoning_effort: "low"}, researcher: {model: "gpt-5.6-terra", reasoning_effort: "medium"}, writer: {model: "gpt-5.6-terra", reasoning_effort: "medium"}, "image-maker": {model: "gpt-5.6-terra", reasoning_effort: "medium"}, "content-assembler": {model: "gpt-5.6-luna", reasoning_effort: "low"}};
  const data = {revision: 1, active_preset_id: "default", presets: [{id: "default", name: "기본", stages}, {id: "alternate", name: "대체", stages}]};
  const document = {createElement: (tag) => new FakeElement(tag), querySelector: (selector) => elements.get(selector) ?? null, querySelectorAll: (selector) => selector === ".schedule-entry-preset" ? [entryPreset] : []};
  const context = vm.createContext({document, fetch: async () => ({ok: true, json: async () => data}), window: {dashboardMutationHeaders, }});

  vm.runInContext(source, context);
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(entryPreset.value, "alternate");
  assert.deepEqual(entryPreset.children.map((option) => option.value), ["default", "alternate"]);
});

test("schedule rows keep each pinned preset when schedule data resolves before model settings", async () => {
  const modelSource = await readFile(new URL("../dashboard/model-settings.js", import.meta.url), "utf8");
  const scheduleSource = await readFile(new URL("../dashboard/schedule.js", import.meta.url), "utf8");
  class ScheduleElement {
    constructor(tagName = "div") { this.tagName = tagName.toUpperCase(); this.children = []; this.listeners = {}; this.attributes = {}; this.dataset = {}; this.value = ""; this.checked = true; this.disabled = false; this.hidden = false; this.className = ""; this.textContent = ""; }
    addEventListener(type, handler) { this.listeners[type] = handler; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = [...children]; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    removeAttribute(name) { delete this.attributes[name]; }
    cloneNode() { const clone = new ScheduleElement(this.tagName); clone.value = this.value; clone.className = this.className; clone.children = this.children.map(child => child.cloneNode?.() ?? child); return clone; }
    remove() { this.removed = true; }
    set innerHTML(value) {
      this._innerHTML = value;
      if (value.includes("<strong>")) {
        const strong = new ScheduleElement("strong");
        const step = new ScheduleElement("span"); step.className = "schedule-step";
        const bar = new ScheduleElement("progress");
        const small = new ScheduleElement("small");
        this.children = [strong, step, bar, small];
      }
    }
    get innerHTML() { return this._innerHTML || ""; }
    querySelector(selector) {
      const matches = (node) => selector.startsWith(".") ? String(node.className || "").split(" ").includes(selector.slice(1)) : selector === "input[type=\"time\"]" ? node.tagName === "INPUT" && node.type === "time" : selector === "input[type=\"checkbox\"]" ? node.tagName === "INPUT" && node.type === "checkbox" : node.tagName.toLowerCase() === selector;
      for (const child of this.children) { if (matches(child)) return child; const found = child.querySelector?.(selector); if (found) return found; }
      return null;
    }
    querySelectorAll(selector) { const found = this.querySelector(selector); return found ? [found] : []; }
  }
  const elements = new Map();
  for (const selector of ["#model-preset", "#manual-preset", "#schedule-preset", "#model-stage-settings", "#model-settings-status", "#model-preset-form", "#schedule-form", "#schedule-times", "#schedule-status", "#schedule-history", "#schedule-add", "#schedule-start", "#schedule-stop"]) elements.set(selector, new ScheduleElement(selector.includes("preset") ? "select" : "div"));
  elements.get("#schedule-preset").hidden = true;
  const requests = [];
  const resolvers = new Map();
  const document = {
    createElement: tag => new ScheduleElement(tag),
    createTextNode: text => ({textContent: text}),
    querySelector: selector => elements.get(selector) ?? null,
    querySelectorAll: selector => selector === ".schedule-entry-preset" ? elements.get("#schedule-times").children.flatMap(row => row.querySelectorAll(selector)) : [],
    addEventListener() {},
    hidden: false,
  };
  const stages = {"topic-selector": {model: "gpt-5.6-luna", reasoning_effort: "low"}, researcher: {model: "gpt-5.6-terra", reasoning_effort: "medium"}, writer: {model: "gpt-5.6-terra", reasoning_effort: "medium"}, "image-maker": {model: "gpt-5.6-terra", reasoning_effort: "medium"}, "content-assembler": {model: "gpt-5.6-luna", reasoning_effort: "low"}};
  const modelData = {revision: 4, active_preset_id: "default", presets: [{id: "default", name: "기본", stages}, {id: "alternate", name: "대체", stages}]};
  const scheduleData = {enabled: true, times: ["08:00", "12:00"], entries: [{entry_id: "ENTRY-08", time: "08:00", enabled: true, preset_id: "alternate"}, {entry_id: "ENTRY-12", time: "12:00", enabled: true, preset_id: "default"}], history: []};
  const scheduleStatus = {executions: [], history: []};
  const fetch = url => { requests.push(url); if (url === "/api/model-settings" || url === "/api/schedule") return new Promise(resolve => resolvers.set(url, resolve)); return Promise.resolve({ok: true, json: async () => scheduleStatus}); };
  const context = vm.createContext({document, fetch, window: {dashboardMutationHeaders, crypto: {randomUUID: () => "ENTRY-NEW"}, setInterval() {}}, Intl});
  vm.runInContext(modelSource, context);
  vm.runInContext(scheduleSource, context);
  assert.deepEqual(requests.slice(0, 2), ["/api/model-settings", "/api/schedule"]);

  resolvers.get("/api/schedule")({ok: true, json: async () => scheduleData});
  await new Promise(resolve => setImmediate(resolve));
  const times = elements.get("#schedule-times");
  assert.deepEqual(times.children.map(row => row.dataset.presetId), ["alternate", "default"]);

  resolvers.get("/api/model-settings")({ok: true, json: async () => modelData});
  await new Promise(resolve => setImmediate(resolve));
  const rows = times.children;
  assert.deepEqual(rows.map(row => row.querySelector(".schedule-entry-preset").value), ["alternate", "default"]);
  assert.deepEqual(rows.map(row => row.querySelector(".schedule-entry-preset").children.map(option => option.value)), [["default", "alternate"], ["default", "alternate"]]);
});

test("completed submission replaces request status with the settled batch message", async () => {
  const view = batch();
  const harness = await manualHarness(async (url) => ({ok: true, status: url === "/api/manual-run" ? 202 : 200, json: async () => url === "/api/manual-runs?limit=1" ? {batches: []} : view}));
  harness.elements.get("#manual-as-of").value = "2026-09-07";
  await harness.context.window.ManualRunDashboard.start();
  await harness.elements.get("#manual-run-form").submit();
  assert.equal(harness.elements.get("#manual-status").textContent, "외부 저장 대기");
  assert.equal(harness.elements.get("#manual-status").className, "manual-status success");
});

test("running submission updates status when polling reaches a settled batch", async () => {
  const accepted = batch();
  accepted.status = "running";
  const settled = batch();
  const harness = await manualHarness(async (url, options) => {
    if (url === "/api/manual-runs?limit=1") return {ok: true, status: 200, json: async () => ({batches: []})};
    if (options.method === "POST") return {ok: true, status: 202, json: async () => accepted};
    return {ok: true, status: 200, json: async () => settled};
  });
  harness.elements.get("#manual-as-of").value = "2026-09-07";
  await harness.context.window.ManualRunDashboard.start();
  await harness.elements.get("#manual-run-form").submit();
  assert.equal(harness.elements.get("#manual-status").textContent, "실행 요청 중입니다.");
  await harness.timers[0].handler();
  assert.equal(harness.elements.get("#manual-status").textContent, "외부 저장 대기");
  assert.equal(harness.elements.get("#manual-status").className, "manual-status success");
});

test("batch polling restores latest persisted batch", async () => {
  const view = batch({childCount: 3});
  const harness = await manualHarness(async (url) => ({ok: true, status: 200, json: async () => url === "/api/manual-runs?limit=1" ? {batches: [view]} : view}));
  await harness.context.window.ManualRunDashboard.start();
  assert.equal(harness.requests[0][0], "/api/manual-runs?limit=1");
  assert.equal(harness.elements.get("#manual-batch-list").children.length, 3);
  assert.equal(harness.elements.get("#manual-batch").hidden, false);
});

test("child external posts only its nonce", async () => {
  const view = batch();
  const harness = await manualHarness(async (url) => ({ok: true, status: 200, json: async () => url === "/api/manual-runs?limit=1" ? {batches: [view]} : view}));
  await harness.context.window.ManualRunDashboard.start();
  await findButtons(harness.elements.get("#manual-batch-list").children[0])[0].click();
  const [url, options] = harness.requests[1];
  assert.equal(url, "/api/manual-run/BATCH-01/children/CHILD-01/external");
  assert.deepEqual(JSON.parse(options.body), {nonce: "nonce-1"});
  assert.equal(options.headers["Content-Type"], "application/json");
});

test("terminal child polling replaces processing status with its settled message", async () => {
  const initial = batch({childCount: 3});
  const accepted = batch({childCount: 3});
  accepted.status = "running";
  accepted.children[0] = {...accepted.children[0], status: "queued", next_action: null, active_action: {kind: "external"}};
  const settled = batch({childCount: 3});
  settled.children[0] = {...settled.children[0], message: "외부 저장 대기 · Notion/Naver 연결이 필요합니다", next_action: {kind: "external", nonce: "fresh-nonce"}, active_action: null};
  const harness = await manualHarness(async (url, options) => {
    if (url === "/api/manual-runs?limit=1") return {ok: true, status: 200, json: async () => ({batches: [initial]})};
    if (options.method === "POST") return {ok: true, status: 202, json: async () => accepted};
    return {ok: true, status: 200, json: async () => settled};
  });

  await harness.context.window.ManualRunDashboard.start();
  await findButtons(harness.elements.get("#manual-batch-list").children[0])[0].click();
  assert.match(harness.elements.get("#manual-status").textContent, /처리 중입니다/);

  await harness.timers[0].handler();

  assert.equal(harness.elements.get("#manual-status").textContent, "외부 저장 대기 · Notion/Naver 연결이 필요합니다");
  assert.equal(findButtons(harness.elements.get("#manual-batch-list").children[0])[0].disabled, false);
  assert.equal(findButtons(harness.elements.get("#manual-batch-list").children[1])[0].disabled, false);
});

test("transient batch polling failure retries without replaying the action", async () => {
  const running = batch();
  running.status = "running";
  running.children[0] = {...running.children[0], status: "running", next_action: null};
  const settled = batch();
  let batchReads = 0;
  const harness = await manualHarness(async (url) => {
    if (url === "/api/manual-runs?limit=1") return {ok: true, status: 200, json: async () => ({batches: [running]})};
    batchReads += 1;
    if (batchReads === 1) throw new Error("offline");
    return {ok: true, status: 200, json: async () => settled};
  });

  await harness.context.window.ManualRunDashboard.start();
  assert.equal(harness.timers[0].delay, 800);
  await harness.timers[0].handler();
  assert.match(harness.elements.get("#manual-status").textContent, /자동으로 다시 확인/);
  assert.equal(harness.timers[1].delay, 3_000);
  await harness.timers[1].handler();
  assert.equal(harness.elements.get("#manual-status").textContent, "상태 연결이 복구되었습니다.");
  assert.deepEqual(harness.requests.map(([url]) => url), ["/api/manual-runs?limit=1", "/api/manual-run/BATCH-01", "/api/manual-run/BATCH-01"]);
});

test("child confirm dialog and request are scoped to selected child", async () => {
  const view = batch({childCount: 3});
  view.children[1] = {...view.children[1], result_status: "awaiting_user_confirmation", next_action: {kind: "confirm", nonce: "confirm-2"}, confirmation_preview: {action: "naver-draft-save", target_blog_id: "sola_note", title: "둘째 글", images: ["/tmp/thumbnail.png"], artifact_digest: "sha256:two"}};
  const harness = await manualHarness(async (url) => ({ok: true, status: 200, json: async () => url === "/api/manual-runs?limit=1" ? {batches: [view]} : view}));
  let confirmation = "";
  harness.context.window.confirm = (message) => { confirmation = message; return true; };
  await harness.context.window.ManualRunDashboard.start();
  await findButtons(harness.elements.get("#manual-batch-list").children[1])[0].click();
  assert.match(confirmation, /대상 블로그: sola_note/);
  assert.match(confirmation, /임시저장.*발행하지 않음/);
  assert.equal(harness.requests[1][0], "/api/manual-run/BATCH-01/children/CHILD-02/confirm");
  assert.deepEqual(JSON.parse(harness.requests[1][1].body), {nonce: "confirm-2"});
});

test("stale action 409 refreshes without replaying", async () => {
  const view = batch();
  const refreshed = batch();
  refreshed.children[0] = {...refreshed.children[0], next_action: {kind: "retry", nonce: "fresh"}};
  const harness = await manualHarness(async (url, options) => {
    if (url === "/api/manual-runs?limit=1") return {ok: true, status: 200, json: async () => ({batches: [view]})};
    if (options.method === "POST") return {ok: false, status: 409, json: async () => ({error: "stale action"})};
    return {ok: true, status: 200, json: async () => refreshed};
  });
  await harness.context.window.ManualRunDashboard.start();
  await findButtons(harness.elements.get("#manual-batch-list").children[0])[0].click();
  assert.deepEqual(harness.requests.map(([url]) => url), ["/api/manual-runs?limit=1", "/api/manual-run/BATCH-01/children/CHILD-01/external", "/api/manual-run/BATCH-01"]);
});

test("no bulk confirm control is rendered", async () => {
  const harness = await manualHarness(async () => ({ok: true, status: 200, json: async () => ({batches: [batch()]})}));
  await harness.context.window.ManualRunDashboard.start();
  assert.equal(findByClass(harness.elements.get("#manual-batch"), "manual-batch-confirm-all"), null);
  assert.doesNotMatch(harness.elements.get("#manual-batch-summary").textContent, /전체.*임시저장/);
});

test("incomplete preview blocks confirm request", async () => {
  const view = batch();
  view.children[0] = {...view.children[0], result_status: "awaiting_user_confirmation", next_action: {kind: "confirm", nonce: "bad-confirm"}, confirmation_preview: {action: "naver-draft-save", target_blog_id: "blog", title: "title", images: [], artifact_digest: "sha256:x"}};
  const harness = await manualHarness(async () => ({ok: true, status: 200, json: async () => ({batches: [view]})}));
  await harness.context.window.ManualRunDashboard.start();
  assert.equal(findButtons(harness.elements.get("#manual-batch-list").children[0]).length, 0);
  assert.match(harness.elements.get("#manual-status").textContent, /차단/);
});

test("untrusted child text stays text and cancelled confirmation sends no request", async () => {
  const view = batch();
  view.children[0] = {...view.children[0], resolved_keyword: "<img src=x onerror=alert(1)>", result_status: "awaiting_user_confirmation", next_action: {kind: "confirm", nonce: "cancelled"}, confirmation_preview: {action: "naver-draft-save", target_blog_id: "blog", title: "title", images: ["/tmp/image.png"], artifact_digest: "sha256:x"}};
  const harness = await manualHarness(async () => ({ok: true, status: 200, json: async () => ({batches: [view]})}));
  harness.context.window.confirm = () => false;
  await harness.context.window.ManualRunDashboard.start();
  const firstCard = harness.elements.get("#manual-batch-list").children[0];
  assert.equal(firstCard.children[0].textContent, "<img src=x onerror=alert(1)>");
  await findButtons(firstCard)[0].click();
  assert.equal(harness.requests.length, 1);
});

test("manual polling pauses while hidden and resumes once for an active batch", async () => {
  const running = batch(); running.status = "running";
  const harness = await manualHarness(async (url) => ({ok:true,status:200,json:async () => url === "/api/manual-runs?limit=1" ? {batches:[running]} : running}));
  await harness.context.window.ManualRunDashboard.start();
  assert.equal(harness.timers.length, 1);
  harness.document.hidden = true;
  await harness.documentListeners.visibilitychange();
  await harness.timers[0].handler();
  assert.equal(harness.requests.length, 1);
  harness.document.hidden = false;
  await harness.documentListeners.visibilitychange();
  assert.equal(harness.timers.length, 2);
  await harness.timers[1].handler();
  assert.equal(harness.requests.length, 2);
});

test("dashboard keeps Korean copy natural and mobile metadata inside the viewport", async () => {
  const styles = await readFile(new URL("../dashboard/styles.css", import.meta.url), "utf8");
  assert.match(styles, /\.lede\{[^}]*word-break:keep-all/);
  assert.match(styles, /\.empty-detail p,\.panel-empty p,\.panel-error p\{[^}]*word-break:keep-all/);
  assert.match(styles, /\.helper\{[^}]*word-break:keep-all/);
  assert.match(styles, /\.run-meta>span\{[^}]*overflow-wrap:anywhere;white-space:normal/);
});

test("job table owns list polling and fetches detail only for the selected run", async () => {
  const tasks = await readFile(new URL("../dashboard/tasks.js", import.meta.url), "utf8");
  const app = await readFile(new URL("../dashboard/app.js", import.meta.url), "utf8");
  assert.match(tasks, /fetch\(`\/api\/tasks\?\$\{params\}`/);
  assert.match(tasks, /fetch\(`\/api\/runs\/\$\{encodeURIComponent\(item\.run_id\)\}`/);
  assert.match(tasks, /request === state\.detailRequest && state\.selected === taskId/);
  assert.doesNotMatch(app, /\/api\/runs\?/);
});

test("bulk cancellation sends every selected task across pages before reloading", async () => {
  const source = await readFile(new URL("../dashboard/tasks.js", import.meta.url), "utf8");
  const elements = new Map(); const documentListeners = {}; const requests = []; let taskListRequests = 0;
  class TaskElement extends FakeElement {
    querySelectorAll() { return []; }
  }
  for (const selector of ["#task-summary", "#task-list", "#task-search", "#task-status-filter", "#task-cancel-selected", "#task-more", "#task-status-message", "#detail"]) elements.set(selector, new TaskElement());
  elements.get("#task-status-filter").value = "all";
  const taskIds = Array.from({length: 25}, (_, index) => `JOB-${index + 1}`);
  const cancelButtons = taskIds.map(taskId => {
    const button = new TaskElement("button");
    button.dataset.taskId = taskId;
    return button;
  });
  const items = taskIds.map((taskId, index) => ({
    task_id: taskId, display_id: taskId, batch_id: "BATCH-1", child_id: `CHILD-${index + 1}`,
    keyword: `주제 ${index + 1}`, effective_status: "queued",
    cancel_action: {scope: "queued_only", nonce: `nonce-${index + 1}`},
  }));
  let visibleButtons = [];
  const document = {
    hidden: false,
    querySelector: selector => elements.get(selector) || null,
    querySelectorAll: selector => selector === ".task-cancel" ? visibleButtons : [],
    addEventListener: (type, handler) => { documentListeners[type] = handler; },
  };
  const fetch = async (url, options = {}) => {
    if (options.method === "POST") requests.push([url, JSON.parse(options.body)]);
    else if (url.startsWith("/api/tasks")) taskListRequests += 1;
    const paginatedItems = url.includes("cursor=next")
      ? items.slice(20)
      : items.slice(0, 20);
    if (url.startsWith("/api/tasks")) {
      visibleButtons = url.includes("cursor=next")
        ? cancelButtons
        : cancelButtons.slice(0, 20);
    }
    return {
      ok: !(options.method === "POST" && url.includes("CHILD-3")),
      json: async () => url === "/api/schedule"
        ? {next_run: null}
        : {items: paginatedItems, next_cursor: url.startsWith("/api/tasks") && !url.includes("cursor=next") ? "next" : null, global_summary: {by_status: {queued: items.length}}, server_now: "2026-09-29T12:00:00+09:00"},
    };
  };
  const context = vm.createContext({document, URLSearchParams, Intl, fetch, window: {
    dashboardMutationHeaders, DashboardNavigation: {route: () => "tasks"},
    addEventListener() {}, setInterval() {},
  }});
  vm.runInContext(source, context);
  documentListeners.DOMContentLoaded();
  await new Promise(resolve => setImmediate(resolve));
  await elements.get("#task-more").click();
  await new Promise(resolve => setImmediate(resolve));
  context.window.DashboardTasks.state.selectedIds = new Set(taskIds);
  const requestsBeforePolling = taskListRequests;
  await context.window.DashboardTasks.load();
  assert.equal(taskListRequests, requestsBeforePolling);
  assert.equal(context.window.DashboardTasks.state.items.length, 25);
  assert.equal(context.window.DashboardTasks.state.selectedIds.size, 25);
  await elements.get("#task-cancel-selected").click();

  assert.equal(requests.length, 25);
  assert.deepEqual(requests.map(([, body]) => body.nonce), taskIds.map((_, index) => `nonce-${index + 1}`));
  assert.match(elements.get("#task-status-message").textContent, /취소 실패 1건: JOB-3/);
});

test("job polling skips identical list DOM and redraws when observed tokens change", async () => {
  const source = await readFile(new URL("../dashboard/tasks.js", import.meta.url), "utf8");
  const elements = new Map(); const documentListeners = {}; const intervals = []; let writes = 0; let tokens = 1200;
  class TaskElement extends FakeElement {
    set innerHTML(value) { this._innerHTML = value; if (this === elements.get("#task-list")) writes += 1; }
    get innerHTML() { return this._innerHTML || ""; }
    querySelectorAll() { return []; }
  }
  for (const selector of ["#task-summary", "#task-list", "#task-search", "#task-status-filter", "#task-cancel-selected", "#task-more", "#task-status-message", "#detail"]) elements.set(selector, new TaskElement());
  elements.get("#task-status-filter").value = "all";
  const document = {hidden:false,querySelector:selector=>elements.get(selector)||null,querySelectorAll:()=>[],addEventListener:(type,handler)=>{documentListeners[type]=handler;},createElement:()=>new TaskElement(),dispatchEvent(){}};
  const item = () => ({task_id:"JOB-1",display_id:"2026-09-13_001",run_id:"RUN-1",keyword:"긴 한국어 작업 제목",effective_status:"running",current_stage:"researcher",completed_stage_count:1,duration_seconds:12,usage:{total_tokens:tokens,usage_observed_at:"2026-09-13T12:00:00+09:00"}});
  const context = vm.createContext({document,URLSearchParams,Intl,CustomEvent:class{},clearTimeout,setTimeout,fetch:async url=>({ok:true,json:async()=>url === "/api/schedule" ? {next_run:null} : {items:[item()],global_summary:{by_status:{running:1}},server_now:"2026-09-13T12:00:00+09:00"}}),window:{dashboardMutationHeaders, DashboardNavigation:{route:()=>"tasks"},addEventListener(){},setInterval:(handler,delay)=>{intervals.push({handler,delay});}}});
  vm.runInContext(source, context);
  await documentListeners.DOMContentLoaded();
  await new Promise(resolve => setImmediate(resolve));
  const initialWrites = writes;
  assert.equal(initialWrites, 1);
  for (let index = 0; index < 10; index += 1) await intervals[0].handler();
  assert.equal(writes, initialWrites);
  tokens = 2400;
  await intervals[0].handler();
  assert.equal(writes, initialWrites + 1);
  assert.equal(intervals[0].delay, 10_000);
});


test("schedule retry recovers accepted work after reload, rotates terminal failures, and retains transport-uncertain nonces", async () => {
  const scheduleSource = await readFile(new URL("../dashboard/schedule.js", import.meta.url), "utf8");
  class ScheduleElement {
    constructor(tagName = "div") { this.tagName = tagName.toUpperCase(); this.children = []; this.listeners = {}; this.attributes = {}; this.dataset = {}; this.value = ""; this.checked = true; this.disabled = false; this.hidden = false; this.className = ""; this.textContent = ""; }
    addEventListener(type, handler) { this.listeners[type] = handler; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = [...children]; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    removeAttribute(name) { delete this.attributes[name]; }
    cloneNode() { const clone = new ScheduleElement(this.tagName); clone.value = this.value; clone.className = this.className; clone.children = this.children.map(child => child.cloneNode?.() ?? child); return clone; }
    remove() { this.removed = true; }
    set innerHTML(value) {
      this._innerHTML = value;
      if (value.includes("<strong>")) {
        const strong = new ScheduleElement("strong");
        const step = new ScheduleElement("span"); step.className = "schedule-step";
        const bar = new ScheduleElement("progress");
        const small = new ScheduleElement("small");
        this.children = [strong, step, bar, small];
      }
    }
    get innerHTML() { return this._innerHTML || ""; }
    querySelector(selector) {
      const matches = node => selector.startsWith(".")
        ? String(node.className || "").split(" ").includes(selector.slice(1))
        : selector === 'input[type="time"]' ? node.tagName === "INPUT" && node.type === "time"
        : selector === 'input[type="checkbox"]' ? node.tagName === "INPUT" && node.type === "checkbox"
        : node.tagName.toLowerCase() === selector;
      for (const child of this.children) {
        if (matches(child)) return child;
        const found = child.querySelector?.(selector);
        if (found) return found;
      }
      return null;
    }
    querySelectorAll(selector) { const found = this.querySelector(selector); return found ? [found] : []; }
  }
  const selectors = ["#model-preset", "#manual-preset", "#schedule-preset", "#model-stage-settings", "#model-settings-status", "#model-preset-form", "#schedule-form", "#schedule-times", "#schedule-status", "#schedule-history", "#schedule-add", "#schedule-start", "#schedule-stop"];
  const elements = new Map(selectors.map(selector => [selector, new ScheduleElement(selector.includes("preset") ? "select" : "div")]));
  elements.get("#schedule-preset").hidden = true;
  const occurrence = {at:"2026-09-28T08:00:00+09:00", entry_id:"ENTRY-08", status:"missed", occurrence_id:"OCC-08"};
  const schedule = {enabled:true, times:["08:00"], entries:[{entry_id:"ENTRY-08",time:"08:00",enabled:true}], history:[occurrence]};
  let visibleStatus = "accepted";
  const progress = {history:[{...occurrence, status:visibleStatus}], executions:[]};
  const requests = [];
  let retries = 0;
  const fetch = async (url, options = {}) => {
    if (url === "/api/schedule") return {ok:true, json:async () => schedule};
    if (url === "/api/schedule-status") {
      return {ok:true, json:async () => ({...progress, history:[{...occurrence, status:visibleStatus}]})};
    }
    if (url === "/api/schedule/retry") {
      const payload = JSON.parse(options.body);
      requests.push(payload);
      retries += 1;
      if (retries === 1) throw new Error("connection lost");
      visibleStatus = "failed";
      return {ok:true, json:async () => ({status:"failed", as_of_date:"2026-09-28", error:"launch failed"})};
    }
    throw new Error(`unexpected request: ${url}`);
  };
  const document = {
    createElement: tag => new ScheduleElement(tag),
    createTextNode: text => ({textContent:text}),
    querySelector: selector => elements.get(selector) ?? null,
    addEventListener() {},
    hidden: false,
  };
  let now = 0;
  class TestDate extends Date { static now() { now += 1; return 1_800_000_000_000 + now; } }
  const context = vm.createContext({document, fetch, Date:TestDate, Intl, window:{dashboardMutationHeaders, crypto:{randomUUID:()=>"ENTRY-NEW"}, setInterval() {}}});
  vm.runInContext(scheduleSource, context);
  const history = elements.get("#schedule-history");
  let retry;
  for (let attempt = 0; attempt < 5 && !retry; attempt += 1) {
    await new Promise(resolve => setImmediate(resolve));
    const row = history.children[0];
    retry = row?.children.find(child => child.tagName === "BUTTON");
  }
  assert.ok(retry, elements.get("#schedule-status").textContent);
  await retry.listeners.click();
  assert.equal(requests.length, 1);
  await retry.listeners.click();
  assert.equal(requests.length, 2);
  assert.equal(requests[0].nonce, requests[1].nonce);
  const failedRetry = history.children[0].children.find(child => child.tagName === "BUTTON");
  assert.ok(failedRetry);
  await failedRetry.listeners.click();
  assert.equal(requests.length, 3);
  assert.notEqual(requests[1].nonce, requests[2].nonce);
});

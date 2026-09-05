import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

class FakeElement {
  constructor() {
    this.children = [];
    this.listeners = {};
    this.value = "";
    this.innerHTML = "";
    this.className = "";
    this.textContent = "";
    this.disabled = false;
    this.required = false;
  }

  addEventListener(type, handler) {
    this.listeners[type] = handler;
  }

  append(...children) {
    this.children.push(...children);
  }

  click() {
    return this.listeners.click?.();
  }
}

test("failed external result keeps a retry action for the same task", async () => {
  const source = await readFile(new URL("../dashboard/app.js", import.meta.url), "utf8");
  const script = source.slice(0, source.indexOf('\ndocument.querySelector("#refresh")'));
  const elements = new Map();
  const document = {
    createElement: () => new FakeElement(),
    querySelector: (selector) => {
      if (!elements.has(selector)) elements.set(selector, new FakeElement());
      return elements.get(selector);
    },
  };
  const responses = [
    {task_id: "TASK-1", status: "completed", run_id: "RUN-1", result_status: "failed", retryable: true, message: "adapter unavailable"},
    {generated_at: null, summary: {total: 0, by_status: {}}, runs: []},
    {task_id: "TASK-1", status: "completed", run_id: "RUN-1", result_status: "failed", retryable: true, message: "adapter unavailable"},
    {generated_at: null, summary: {total: 0, by_status: {}}, runs: []},
    {task_id: "TASK-1", status: "completed", run_id: "RUN-1", result_status: "failed", retryable: true, message: "adapter unavailable"},
    {generated_at: null, summary: {total: 0, by_status: {}}, runs: []},
  ];
  const requests = [];
  const context = vm.createContext({
    document,
    fetch: async (url, options) => {
      requests.push([url, options]);
      const task = responses.shift();
      if (task) return {ok: true, json: async () => task};
      return {ok: true, json: async () => ({generated_at: null, summary: {total: 0, by_status: {}}, runs: []})};
    },
    setTimeout,
    window: {setTimeout},
  });
  vm.runInContext(script, context);
  await vm.runInContext("pollManualRun('TASK-1')", context);

  const status = elements.get("#manual-status");
  assert.equal(status.className, "manual-status error");
  assert.equal(status.children[1].textContent, "외부 저장 재시도");
  assert.match(status.innerHTML, /외부 저장 실패/);
  assert.equal(requests[0][0], "/api/manual-run/TASK-1");

  await status.children[1].click();
  assert.equal(status.className, "manual-status error");
  assert.equal(status.children[1].textContent, "외부 저장 재시도");
  assert.equal(requests[2][0], "/api/manual-run/TASK-1/external");

  await status.children[1].click();
  assert.equal(status.className, "manual-status error");
  assert.equal(status.children[1].textContent, "외부 저장 재시도");
  assert.equal(requests[4][0], "/api/manual-run/TASK-1/external");
});

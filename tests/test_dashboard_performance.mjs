import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

async function metrics() {
  const source = await readFile(new URL("../dashboard/navigation.js", import.meta.url), "utf8");
  const window = {addEventListener() {}};
  const document = {addEventListener() {}, readyState: "loading"};
  vm.runInNewContext(source, {window, document, location: {hash: ""}, Date, Intl});
  return window.WorkflowPerformance;
}

test("performance metrics retain the selected denominator and unknown token fields", async () => {
  const performance = await metrics();
  const snapshot = {
    generated_at: "2026-09-27T12:00:00Z",
    runs: [
      {
        status: "failed", updated_at: "2026-09-27T11:00:00Z",
        usage: {input_tokens: 0, cached_input_tokens: 0, output_tokens: 0},
        stages: [{name: "writer", status: "failed", duration_ms: 1000, attempts: [{status: "failed"}, {status: "passed"}], model: "gpt-test", reasoning_effort: "low"}],
      },
      {
        status: "passed", updated_at: "2026-09-20T11:00:00Z",
        usage: {input_tokens: 20, output_tokens: 5},
        stages: [{name: "writer", status: "passed", duration_ms: 3000, attempts: [{status: "passed"}], model: "gpt-test", reasoning_effort: "low"}],
      },
      {status: "failed", updated_at: "2026-08-01T11:00:00Z", usage: null, stages: []},
    ],
  };

  const last7Days = performance.summarize(snapshot, {days: "7", now: Date.parse("2026-09-27T12:00:00Z")});
  assert.equal(last7Days.runs, 1);
  assert.equal(last7Days.failedRuns, 1);
  assert.equal(last7Days.tokenTotals.input_tokens, 0);
  assert.equal(last7Days.tokenTotals.cached_input_tokens, 0);
  assert.equal(last7Days.reasoningOutputTokens, null);

  const last30Days = performance.summarize(snapshot, {days: "30", stage: "writer", now: Date.parse("2026-09-27T12:00:00Z")});
  assert.equal(last30Days.runs, 2);
  assert.equal(last30Days.failedRuns, 1);
  assert.equal(last30Days.usageCoverage, 100);
  assert.equal(last30Days.tokenTotals.input_tokens, 20);
  assert.equal(last30Days.tokenTotals.cached_input_tokens, null);
  assert.equal(last30Days.tokenTotals.non_cached_input_tokens, null);
  assert.equal(last30Days.stageMetrics[0].p50, 1000);
  assert.equal(last30Days.stageMetrics[0].p90, 3000);
  assert.equal(last30Days.stageMetrics[0].retries, 1);
  assert.equal(last30Days.stageMetrics[0].models["gpt-test / low"], 2);
});

test("performance metrics show an empty period without manufacturing zero runs", async () => {
  const performance = await metrics();
  const report = performance.summarize({generated_at: "2026-09-27T12:00:00Z", runs: []}, {days: "30"});
  assert.equal(report.empty, true);
  assert.equal(report.runs, 0);
  assert.equal(report.usageCoverage, null);
  assert.equal(report.tokenTotals.input_tokens, null);
});

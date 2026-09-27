(() => {
  const routes = new Set(["tasks", "schedule", "settings", "performance"]);
  const panels = {
    tasks: ["#tasks-panel"],
    schedule: [".schedule-panel"],
    settings: ["#model-settings-panel"],
    performance: ["#performance-panel"],
  };
  const tokenFields = ["input_tokens", "cached_input_tokens", "output_tokens"];
  const performanceSnapshots = new WeakMap();

  function asRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function measured(value) {
    return typeof value === "number" && Number.isFinite(value) && value >= 0;
  }

  function percentile(values, quantile) {
    if (!values.length) return null;
    const sorted = [...values].sort((left, right) => left - right);
    return sorted[Math.max(0, Math.ceil(quantile * sorted.length) - 1)];
  }

  function summarize(snapshot, {days = 30, stage = "all", now = Date.now()} = {}) {
    const allRuns = Array.isArray(asRecord(snapshot).runs) ? asRecord(snapshot).runs.map(asRecord) : [];
    const filtered = days === "all" ? allRuns : allRuns.filter((run) => {
      const updated = Date.parse(run.updated_at || "");
      return Number.isFinite(updated) && updated >= now - Number(days) * 86_400_000;
    });
    const statusCounts = {};
    for (const run of filtered) statusCounts[run.status || "unknown"] = (statusCounts[run.status || "unknown"] || 0) + 1;
    const usageRuns = filtered.filter((run) => tokenFields.some((field) => measured(asRecord(run.usage)[field])));
    const tokenTotals = {};
    for (const field of tokenFields) {
      const values = usageRuns.map((run) => asRecord(run.usage)[field]).filter(measured);
      tokenTotals[field] = values.length === usageRuns.length && values.length > 0 ? values.reduce((sum, value) => sum + value, 0) : null;
      tokenTotals[`${field}_observed_runs`] = values.length;
    }
    const cached = tokenTotals.cached_input_tokens;
    const input = tokenTotals.input_tokens;
    tokenTotals.non_cached_input_tokens = measured(input) && measured(cached) && cached <= input ? input - cached : null;
    const stageNames = [...new Set(filtered.flatMap((run) => (Array.isArray(run.stages) ? run.stages : []).map((item) => asRecord(item).name).filter((name) => typeof name === "string")))];
    const selectedNames = stage === "all" ? stageNames : stageNames.filter((name) => name === stage);
    const stageMetrics = selectedNames.map((name) => {
      const rows = filtered.flatMap((run) => (Array.isArray(run.stages) ? run.stages : []).map(asRecord).filter((row) => row.name === name));
      const durations = rows.map((row) => row.duration_ms).filter(measured);
      const attempts = rows.flatMap((row) => Array.isArray(row.attempts) ? row.attempts.map(asRecord) : []);
      const models = {};
      for (const row of rows) {
        if (typeof row.model !== "string") continue;
        const key = `${row.model} / ${row.reasoning_effort || "미기록"}`;
        models[key] = (models[key] || 0) + 1;
      }
      return {
        stage: name,
        runs: rows.length,
        durationRuns: durations.length,
        p50: percentile(durations, 0.5),
        p90: percentile(durations, 0.9),
        failedAttempts: attempts.filter((item) => item.status === "failed").length,
        attemptCount: attempts.length,
        retries: rows.reduce((sum, row) => sum + Math.max(0, (Array.isArray(row.attempts) ? row.attempts.length : 0) - 1), 0),
        usageRuns: rows.filter((row) => measured(asRecord(row.usage).total_tokens)).length,
        totalTokens: rows.reduce((sum, row) => sum + (measured(asRecord(row.usage).total_tokens) ? asRecord(row.usage).total_tokens : 0), 0),
        models,
      };
    });
    const timestamps = allRuns.map((run) => run.updated_at).filter((value) => typeof value === "string" && Number.isFinite(Date.parse(value))).sort((left, right) => Date.parse(left) - Date.parse(right));
    return {
      generatedAt: asRecord(snapshot).generated_at || null,
      newestRunUpdatedAt: timestamps.at(-1) || null,
      availableRuns: allRuns.length,
      runs: filtered.length,
      statusCounts,
      failedRuns: (statusCounts.failed || 0) + (statusCounts.blocked || 0),
      usageRuns: usageRuns.length,
      usageCoverage: filtered.length ? usageRuns.length / filtered.length * 100 : null,
      tokenTotals,
      reasoningOutputTokens: null,
      stageMetrics,
      empty: filtered.length === 0,
    };
  }

  window.WorkflowPerformance = { summarize };

  function route() {
    const name = location.hash.replace(/^#/, "") || "tasks";
    return routes.has(name) ? name : "tasks";
  }
  function render() {
    const current = route();
    const options = document.querySelector(".manual-options");
    if (options) options.open = current === "settings";
    document.querySelectorAll("[data-route]").forEach((link) => {
      const active = link.dataset.route === current;
      link.setAttribute("aria-current", active ? "page" : "false");
    });
    const selectors = new Set(Object.values(panels).flat());
    selectors.forEach((selector) => {
      document.querySelectorAll(selector).forEach((panel) => {
        const visible = panels[current].includes(selector);
        panel.hidden = !visible;
        if (!visible) panel.querySelectorAll("button, input, select, textarea, a").forEach((control) => control.setAttribute("tabindex", "-1"));
        else panel.querySelectorAll('[tabindex="-1"]').forEach((control) => control.removeAttribute("tabindex"));
      });
    });
    if (current === "performance") {
      const panel = document.querySelector("#performance-panel");
      if (panel && panel.dataset.loaded !== "true" && panel.dataset.loading !== "true") loadPerformance(panel);
    }
  }

  function textElement(tag, text, className = "") {
    const element = document.createElement(tag);
    element.textContent = String(text);
    if (className) element.className = className;
    return element;
  }

  function formatToken(value) {
    return measured(value) ? new Intl.NumberFormat("ko-KR").format(value) : "집계 전";
  }

  function summaryCard(label, value, note, variant = "") {
    const card = document.createElement("article");
    card.className = `summary-card ${variant}`.trim();
    card.append(textElement("div", label, "label"), textElement("div", value, "value"), textElement("div", note, "note"));
    return card;
  }

  function renderPerformance(panel, snapshot) {
    const period = panel.querySelector("#performance-period").value;
    const stage = panel.querySelector("#performance-stage").value;
    const report = summarize(snapshot, {days: period, stage});
    const stats = panel.querySelector("#performance-summary");
    const usage = panel.querySelector("#performance-usage");
    const stages = panel.querySelector("#performance-stages");
    const freshness = report.newestRunUpdatedAt ? new Date(report.newestRunUpdatedAt).toLocaleString("ko-KR") : "미기록";
    stats.replaceChildren(
      summaryCard("선택 기간 실행", `${report.runs}건`, `보관된 실행 ${report.availableRuns}건`),
      summaryCard("실패·차단", `${report.failedRuns}건`, `전체 ${report.runs}건 기준`, report.failedRuns ? "blocked" : "success"),
      summaryCard("usage 관측", report.usageCoverage === null ? "집계 전" : `${report.usageCoverage.toFixed(1)}%`, `${report.usageRuns}/${report.runs}건에서 토큰 기록 확인`),
      summaryCard("최근 실행 기록", freshness, `보고서 갱신 ${report.generatedAt ? new Date(report.generatedAt).toLocaleString("ko-KR") : "미기록"}`),
    );
    const tokens = report.tokenTotals;
    usage.replaceChildren(
      summaryCard("입력 토큰", formatToken(tokens.input_tokens), `관측 ${tokens.input_tokens_observed_runs}/${report.runs}건`),
      summaryCard("캐시 입력", formatToken(tokens.cached_input_tokens), "입력 토큰의 하위 집합"),
      summaryCard("캐시 제외 입력", formatToken(tokens.non_cached_input_tokens), "입력에서 캐시 입력을 뺀 값"),
      summaryCard("출력 토큰", formatToken(tokens.output_tokens), "Codex usage 기록 합계"),
      summaryCard("추론 토큰", formatToken(report.reasoningOutputTokens), "현재 dashboard API에서 별도 제공하지 않음"),
    );
    stages.replaceChildren();
    if (report.empty) {
      stages.append(textElement("p", "선택한 기간에 실행 기록이 없습니다.", "manual-status"));
      return;
    }
    if (!report.stageMetrics.length) {
      stages.append(textElement("p", "선택한 단계의 기록이 없습니다.", "manual-status"));
      return;
    }
    for (const metric of report.stageMetrics) {
      const card = document.createElement("article");
      card.className = "summary-card";
      card.append(textElement("h3", metric.stage));
      const facts = document.createElement("dl");
      facts.className = "job-facts";
      const values = [
        ["관측 실행", `${metric.durationRuns}/${metric.runs}건`],
        ["p50 처리시간", measured(metric.p50) ? `${(metric.p50 / 1000).toFixed(1)}초` : "미기록"],
        ["p90 처리시간", measured(metric.p90) ? `${(metric.p90 / 1000).toFixed(1)}초` : "미기록"],
        ["실패 시도", `${metric.failedAttempts}회`],
        ["재시도", `${metric.retries}회`],
        ["usage 관측", `${metric.usageRuns}/${metric.runs}건`],
        ["관측 토큰", metric.usageRuns ? formatToken(metric.totalTokens) : "집계 전"],
      ["모델 / effort", Object.entries(metric.models).map(([name, count]) => `${name} · ${count}건`).join(", ") || "미기록"],
      ];
      for (const [label, value] of values) {
        const item = document.createElement("div");
        item.append(textElement("dt", label), textElement("dd", value));
        facts.append(item);
      }
      card.append(facts);
      stages.append(card);
    }
  }

  async function loadPerformance(panel) {
    panel.dataset.loading = "true";
    const status = panel.querySelector("#performance-status");
    status.textContent = "실행 기록을 읽고 있습니다.";
    try {
      const response = await fetch("/api/snapshot", {cache: "no-store"});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const snapshot = await response.json();
      const stage = panel.querySelector("#performance-stage");
      const stageNames = [...new Set((Array.isArray(snapshot?.runs) ? snapshot.runs : []).flatMap((run) => (Array.isArray(asRecord(run).stages) ? asRecord(run).stages : []).map((item) => asRecord(item).name).filter((name) => typeof name === "string")))];
      const selected = stage.value;
      stage.replaceChildren(Object.assign(textElement("option", "전체 단계"), {value: "all"}));
      for (const name of stageNames) {
        const option = textElement("option", name);
        option.value = name;
        stage.append(option);
      }
      stage.value = stageNames.includes(selected) ? selected : "all";
      performanceSnapshots.set(panel, snapshot);
      renderPerformance(panel, snapshot);
      panel.dataset.loaded = "true";
      status.textContent = "로컬 실행 로그를 읽기 전용으로 집계했습니다.";
    } catch {
      panel.querySelector("#performance-summary").replaceChildren(textElement("p", "성능 기록을 불러오지 못했습니다. 다시 읽기를 눌러주세요.", "manual-status error"));
      status.textContent = "연결 오류";
    } finally {
      panel.dataset.loading = "false";
    }
  }

  function addPerformancePanel() {
    const nav = document.querySelector(".main-nav");
    const page = document.querySelector(".page-body");
    if (!nav || !page || document.querySelector("#performance-panel")) return;
    const link = textElement("a", "성능 분석");
    link.href = "#performance";
    link.dataset.route = "performance";
    nav.append(link);
    const panel = document.createElement("section");
    panel.id = "performance-panel";
    panel.dataset.panel = "performance";
    panel.className = "detail-panel";
    const heading = document.createElement("div");
    heading.className = "manual-heading";
    heading.append(textElement("p", "WORKFLOW METRICS · READ ONLY", "overline"), textElement("h2", "생성 성능 분석"), textElement("p", "실행 기록의 시간·재시도·사용량을 분모와 coverage와 함께 확인합니다."));
    const filters = document.createElement("div");
    filters.className = "cluster";
    const periodLabel = textElement("label", "기간");
    const period = document.createElement("select");
    period.id = "performance-period";
    for (const [label, value] of [["최근 7일", "7"], ["최근 30일", "30"], ["최근 90일", "90"], ["전체", "all"]]) {
      const option = textElement("option", label);
      option.value = value;
      period.append(option);
    }
    period.value = "30";
    periodLabel.append(period);
    const stageLabel = textElement("label", "단계");
    const stage = document.createElement("select");
    stage.id = "performance-stage";
    stage.append(Object.assign(textElement("option", "전체 단계"), {value: "all"}));
    stageLabel.append(stage);
    const refresh = textElement("button", "다시 읽기", "button button-quiet");
    refresh.type = "button";
    refresh.addEventListener("click", () => loadPerformance(panel));
    filters.append(periodLabel, stageLabel, refresh);
    const status = textElement("p", "최근 30일 기준으로 불러옵니다.", "manual-status");
    status.id = "performance-status";
    const summary = document.createElement("div");
    summary.className = "summary-grid";
    summary.id = "performance-summary";
    const usageHeading = textElement("h3", "토큰 usage");
    const usage = document.createElement("div");
    usage.className = "summary-grid";
    usage.id = "performance-usage";
    const stageHeading = textElement("h3", "단계별 시간과 재시도");
    const stages = document.createElement("div");
    stages.className = "summary-grid";
    stages.id = "performance-stages";
    const definitions = textElement("p", "분모는 선택 기간의 실행 기록입니다. p50/p90은 기록된 단계 누적 시간 기준이며, 실패와 재시도도 포함합니다. 캐시 입력은 입력 합계에 다시 더하지 않습니다. 비어 있는 usage는 0이 아니라 ‘집계 전’으로 표시합니다.", "helper");
    panel.append(heading, filters, status, summary, usageHeading, usage, stageHeading, stages, definitions);
    page.append(panel);
    for (const select of [period, stage]) select.addEventListener("change", () => {
      panel.dataset.loaded = "false";
      const snapshot = performanceSnapshots.get(panel);
      if (snapshot) {
        renderPerformance(panel, snapshot);
        panel.dataset.loaded = "true";
      }
    });
  }
  window.DashboardNavigation = { route, render };
  document.addEventListener("DOMContentLoaded", () => {
    addPerformancePanel();
    const open = document.querySelector("#new-post");
    const close = document.querySelector("#manual-dialog-close");
    const dialog = document.querySelector("#manual-dialog");
    const body = document.querySelector("#manual-dialog-body");
    const formPanel = document.querySelector(".manual-options .manual-panel[aria-labelledby='manual-title']");
    open?.addEventListener("click", () => {
      location.hash = "tasks";
      if (formPanel && body && formPanel.parentElement !== body) body.append(formPanel);
      if (dialog && typeof dialog.showModal === "function") dialog.showModal();
    });
    close?.addEventListener("click", () => dialog?.close());
    dialog?.addEventListener("close", () => { const options = document.querySelector(".manual-options"); if (formPanel && options) options.append(formPanel); });
  });
  window.addEventListener("hashchange", render);
  document.addEventListener("DOMContentLoaded", render);
  if (document.readyState !== "loading") render();
})();

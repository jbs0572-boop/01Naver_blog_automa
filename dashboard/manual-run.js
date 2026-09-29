(() => {
  const pendingKey = "naver-dashboard.pending-manual-request.v1";
  const state = { batch: null, activeChildId: null, pollTimer: null, pollInFlight: false, resumePending: false, onSettled: null, pollFailed: false, pendingSubmission: false };
  const labels = { queued: "대기", running: "실행 중", completed: "완료", failed: "실패", blocked: "차단", "local-only": "임시저장 준비 중", awaiting_user_confirmation: "네이버 임시저장 진행 중", draft_saved: "임시저장 완료" };
  const $ = (selector) => document.querySelector(selector);

  function status(message, kind = "info") {
    const element = $("#manual-status");
    element.className = `manual-status ${kind}`;
    element.textContent = message;
  }

  function valueOr(value, fallback = "-") {
    return typeof value === "string" && value.trim() ? value : fallback;
  }

  function safePreview(child) {
    const preview = child.confirmation_preview;
    if (!preview || preview.action !== "naver-draft-save" || !preview.target_blog_id || !preview.title || !Array.isArray(preview.images) || !preview.images.length || !preview.artifact_digest) return null;
    return preview;
  }

  function actionLabel(kind, child) {
    if (kind === "external" && child?.result_status === "ready_for_naver") return "품질 검수 보고서 확인 후 계속";
    return { retry: "실행 재시도", external: "외부 저장 실행" }[kind] ?? null;
  }

  function setFormBusy(busy) {
    $("#manual-submit").disabled = busy;
    $("#manual-topic-source").disabled = busy;
    $("#manual-keyword").disabled = busy || $("#manual-topic-source").value !== "user_defined";
    $("#manual-as-of").disabled = busy;
    if ($("#manual-preset")) $("#manual-preset").disabled = busy;
  }

  function textElement(tagName, className, text) {
    const element = document.createElement(tagName);
    element.className = className;
    element.textContent = text;
    return element;
  }

  function childCard(batch, child) {
    const card = document.createElement("article");
    card.className = "manual-child-card";
    card.setAttribute("aria-label", `블로그 ${child.slot}번`);
    const heading = textElement("h3", "manual-child-title", valueOr(child.resolved_keyword, "선정 대기"));
    const meta = textElement("p", "manual-child-meta", `슬롯 ${valueOr(String(child.slot))} · ${valueOr(child.run_id, "실행 ID 준비 중")}`);
    const result = textElement("p", "manual-child-result", labels[child.result_status] ?? labels[child.status] ?? valueOr(child.result_status, "상태 확인 필요"));
    card.append(heading, meta, result);
    const action = child.next_action;
    const label = action && actionLabel(action.kind, child);
    if (action?.kind === "confirm") {
      if (!safePreview(child)) {
        status("임시저장 차단 · 대상 블로그·제목·이미지 정보가 없습니다.", "error");
      }
      return card;
    }
    if (!action || !label || typeof action.nonce !== "string" || !action.nonce) return card;
    const button = textElement("button", "button button-quiet manual-child-action", label);
    button.type = "button";
    button.disabled = state.activeChildId === child.child_id;
    button.addEventListener("click", () => runChildAction(batch, child));
    card.append(button);
    return card;
  }

  function render(batch) {
    state.batch = batch;
    const panel = $("#manual-batch");
    const summary = $("#manual-batch-summary");
    const list = $("#manual-batch-list");
    if (!batch || !Array.isArray(batch.children)) {
      panel.hidden = true;
      summary.textContent = "";
      list.replaceChildren();
      return;
    }
    panel.hidden = false;
    const auto = batch.topic_source === "auto_selected";
    const snapshot = batch.snapshot || {};
    summary.textContent = auto
      ? `자동 주제 선정 · 한 번의 스냅샷 ${valueOr(snapshot.capture_id, "확인 대기")}에서 독립 블로그 ${batch.children.length}건을 준비합니다.`
      : "사용자 주제 · 독립 블로그 1건을 준비합니다.";
    if (batch.children.length > 1) summary.textContent = `이전 실행 기록 (${batch.children.length}건) · 아래는 과거 슬롯입니다. 새 작업은 1건만 생성됩니다.`;
    const cards = batch.children.map((child) => childCard(batch, child));
    list.replaceChildren(...cards);
  }

  async function readJson(response) {
    const body = await response.json();
    if (!response.ok) throw Object.assign(new Error(body.error || `HTTP ${response.status}`), {status: response.status});
    return body;
  }

  async function readBatch(batchId) {
    const response = await fetch(`/api/manual-run/${encodeURIComponent(batchId)}`, {cache: "no-store"});
    const batch = await readJson(response);
    render(batch);
    return batch;
  }

  function isActive(batch) {
    return batch && (batch.status === "queued" || batch.status === "running");
  }

  function settledMessage(batch, childId) {
    const child = Array.isArray(batch?.children) ? batch.children.find((item) => item.child_id === childId) : null;
    return typeof child?.message === "string" && child.message.trim() ? child.message : "작업이 완료되었습니다.";
  }

  function batchSettledMessage(batch) {
    const child = Array.isArray(batch?.children) ? batch.children.find((item) => typeof item.message === "string" && item.message.trim()) : null;
    return child ? child.message : "작업이 완료되었습니다.";
  }

  function poll(batch, delay = 800) {
    if (document.hidden) { state.resumePending = isActive(batch); return; }
    if (state.pollTimer) window.clearTimeout(state.pollTimer);
    state.pollTimer = window.setTimeout(async () => {
      state.pollTimer = null;
      if (document.hidden) { state.resumePending = isActive(state.batch); return; }
      if (state.pollInFlight) { state.resumePending = true; return; }
      state.pollInFlight = true;
      try {
        schedulePoll(await readBatch(batch.batch_id));
      } catch (error) {
        state.pollFailed = true;
        status(`상태 연결이 끊겼습니다. 자동으로 다시 확인합니다. ${error.message}`, "error");
        poll(batch, 3_000);
      } finally {
        state.pollInFlight = false;
        if (state.resumePending && !document.hidden && isActive(state.batch) && !state.pollTimer) {
          state.resumePending = false;
          poll(state.batch, 0);
        }
      }
    }, delay);
  }

  function schedulePoll(batch) {
    if (!isActive(batch)) {
      const settledChildId = state.activeChildId;
      state.activeChildId = null;
      if (settledChildId) {
        render(batch);
        status(settledMessage(batch, settledChildId));
      } else if (state.pendingSubmission) {
        status(batchSettledMessage(batch), "success");
      } else if (state.pollFailed) {
        status("상태 연결이 복구되었습니다.", "success");
      }
      state.pendingSubmission = false;
      state.resumePending = false;
      state.pollFailed = false;
      setFormBusy(false);
      state.onSettled?.();
      return;
    }
    poll(batch);
  }

  async function restoreLatest() {
    const response = await fetch("/api/manual-runs?limit=1", {cache: "no-store"});
    const payload = await readJson(response);
    const latest = Array.isArray(payload.batches) ? payload.batches[0] : null;
    render(latest);
    if (latest) schedulePoll(latest);
  }

  async function runChildAction(batch, child) {
    const action = child.next_action;
    if (!action || !actionLabel(action.kind, child) || typeof action.nonce !== "string" || !action.nonce) return;
    state.activeChildId = child.child_id;
    render(batch);
    status(`${actionLabel(action.kind, child)} 처리 중입니다.`, "warning");
    try {
      const response = await fetch(`/api/manual-run/${encodeURIComponent(batch.batch_id)}/children/${encodeURIComponent(child.child_id)}/${action.kind}`, {
        method: "POST", headers: window.dashboardMutationHeaders(), body: JSON.stringify({nonce: action.nonce}),
      });
      if (response.status === 409) {
        state.activeChildId = null;
        await readBatch(batch.batch_id);
        status("최신 작업 상태로 갱신했습니다. 다시 확인하세요.", "warning");
        return;
      }
      const updated = await readJson(response);
      render(updated);
      schedulePoll(updated);
    } catch (error) {
      state.activeChildId = null;
      render(state.batch);
      status(`작업을 시작하지 못했습니다. ${error.message}`, "error");
    }
  }

  function toggleTopicSource() {
    const userDefined = $("#manual-topic-source").value === "user_defined";
    const keyword = $("#manual-keyword");
    $("#manual-keyword-field").hidden = !userDefined;
    keyword.disabled = !userDefined;
    keyword.required = userDefined;
    if (!userDefined) keyword.value = "";
  }

  async function submit(event) {
    event.preventDefault();
    const source = $("#manual-topic-source").value;
    const keyword = $("#manual-keyword").value.trim();
    const asOfDate = $("#manual-as-of").value;
    if (!asOfDate || (source === "user_defined" && !keyword)) {
      status("사용자 주제와 정보 기준일(KST)을 확인하세요.", "error");
      return;
    }
    const description = source === "auto_selected" ? "자동 주제 선정으로 블로그 1건을 만듭니다." : `사용자 주제: ${keyword}`;
    if (!window.confirm(`${description}\n워크플로우를 시작할까요?`)) return;
    setFormBusy(true);
    state.pendingSubmission = true;
    status("실행 요청 중입니다.", "warning");
    try {
      const preset_id = window.ModelSettings?.selected();
      const basePayload = source === "auto_selected" ? {auto_topic: true, as_of_date: asOfDate, ...(preset_id ? {preset_id} : {})} : {keyword, as_of_date: asOfDate, ...(preset_id ? {preset_id} : {})};
      let pending = null;
      try { pending = JSON.parse(window.sessionStorage.getItem(pendingKey) || "null"); } catch { pending = null; }
      if (!pending || JSON.stringify(pending.basePayload) !== JSON.stringify(basePayload) || typeof pending.request_nonce !== "string") {
        pending = {basePayload, request_nonce: window.crypto.randomUUID()};
      }
      const payload = {...basePayload, request_nonce: pending.request_nonce};
      window.sessionStorage.setItem(pendingKey, JSON.stringify(pending));
      const response = await fetch("/api/manual-run", {method: "POST", headers: window.dashboardMutationHeaders(), body: JSON.stringify(payload)});
      const batch = await readJson(response);
      window.sessionStorage.removeItem(pendingKey);
      render(batch);
      schedulePoll(batch);
      if (!isActive(batch)) status(batchSettledMessage(batch), "success");
    } catch (error) {
      setFormBusy(false);
      status(`실행 요청 실패 · ${error.message}`, "error");
    }
  }

  window.ManualRunDashboard = {
    updateMetrics(runs) {
      const list = $("#manual-batch-list");
      if (!state.batch || !window.RunMetrics) return;
      state.batch.children.forEach((child, index) => {
        const card = list.children[index];
        if (!card) return;
        card.querySelector(".run-progress")?.remove();
        const run = runs.find(item => item.run_id === child.run_id);
        if (run) card.insertAdjacentHTML("beforeend", window.RunMetrics.render(run));
      });
    },
    async start(options = {}) {
      state.onSettled = typeof options.onSettled === "function" ? options.onSettled : null;
      $("#manual-topic-source").addEventListener("change", toggleTopicSource);
      $("#manual-run-form").addEventListener("submit", submit);
      toggleTopicSource();
      try {
        await restoreLatest();
      } catch (error) {
        status(`최근 실행 상태를 읽지 못했습니다. ${error.message}`, "error");
      }
    },
  };
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      if (state.pollTimer) window.clearTimeout(state.pollTimer);
      state.pollTimer = null;
      state.resumePending = isActive(state.batch);
      return;
    }
    if (isActive(state.batch) && !state.pollInFlight && !state.pollTimer) {
      state.resumePending = false;
      poll(state.batch, 0);
    } else if (state.pollInFlight) {
      state.resumePending = true;
    }
  });
})();

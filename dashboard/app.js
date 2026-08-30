const state = { runs: [], selected: null, manualTask: null };
const labels = { passed:"통과", validated:"검증됨", running:"실행 중", pending:"대기", failed:"실패", blocked:"차단", skipped:"생략", ready_for_naver:"네이버 대기", awaiting_user_confirmation:"확인 대기", draft_saved:"임시저장", not_recorded:"미기록" };
const topicSourceLabels = { user_defined:"사용자 주제", auto_selected:"자동 주제 선정" };
const statusClass = (status) => ["passed","validated","draft_saved"].includes(status) ? "success" : ["ready_for_naver","awaiting_user_confirmation","running"].includes(status) ? "warning" : ["failed","blocked"].includes(status) ? "error" : "info";
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;", "'":"&#039;"}[char]));
const statusChip = (status) => `<span class="chip chip-${statusClass(status)}">${escapeHtml(labels[status] || status)}</span>`;
const formatDate = (value) => value ? new Date(value).toLocaleString("ko-KR", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit"}) : "-";

function renderSummary(summary) {
  const counts = summary.by_status || {};
  const attention = (counts.awaiting_user_confirmation || 0) + (counts.ready_for_naver || 0) + (counts.running || 0);
  document.querySelector("#summary").innerHTML = [["전체 실행", summary.total || 0, "읽은 로그 파일", ""], ["통과", counts.passed || 0, "계약을 통과한 실행", "success"], ["확인 필요", attention, "승인 대기 또는 진행 중", "attention"], ["실패·차단", (counts.failed || 0) + (counts.blocked || 0), "다음 단계를 멈춘 실행", "blocked"]].map(([label, value, note, klass]) => `<article class="summary-card ${klass}"><div class="label">${label}</div><div class="value">${value}</div><div class="note">${note}</div></article>`).join("");
}

function filteredRuns() {
  const query = document.querySelector("#search").value.trim().toLowerCase();
  const status = document.querySelector("#status-filter").value;
  return state.runs.filter((run) => { const searchable = [run.run_id, run.topic_id, run.keyword].join(" ").toLowerCase(); return (!query || searchable.includes(query)) && (status === "all" || run.status === status); });
}

function renderRuns() {
  const runs = filteredRuns();
  document.querySelector("#run-count").textContent = `${runs.length} runs`;
  const list = document.querySelector("#run-list");
  if (!runs.length) { list.innerHTML = `<div class="empty-row">조건에 맞는 실행이 없습니다.</div>`; return; }
  list.innerHTML = runs.map((run) => { const topicSource = run.topic_source ? `<span>${escapeHtml(topicSourceLabels[run.topic_source] || run.topic_source)}</span>` : run.historical ? `<span>과거 기록</span>` : ""; return `<button class="run-row" type="button" data-run-id="${escapeHtml(run.run_id)}" aria-current="${run.run_id === state.selected}"><span class="run-main"><span class="run-title"><strong>${escapeHtml(run.keyword)}</strong>${statusChip(run.status)}</span><span class="run-meta"><span>${escapeHtml(run.run_id)}</span>${topicSource}<span>${formatDate(run.updated_at)}</span></span><span class="stage-strip" aria-label="단계 진행 상태">${run.stages.map((stage) => `<span class="stage-segment ${statusClass(stage.status)}" title="${escapeHtml(stage.name)}: ${escapeHtml(labels[stage.status] || stage.status)}"></span>`).join("")}</span></span><span aria-hidden="true">›</span></button>`; }).join("");
  list.querySelectorAll(".run-row").forEach((button) => button.addEventListener("click", () => { state.selected = button.dataset.runId; renderRuns(); renderDetail(state.runs.find((run) => run.run_id === state.selected)); }));
}

function renderDetail(run) {
  const panel = document.querySelector("#detail");
  if (!run) { panel.innerHTML = `<div class="empty-detail"><span class="detail-mark">↗</span><h2>실행을 선택하세요</h2><p>왼쪽 목록에서 실행을 선택하면 단계별 상태와 Gate 증거가 나타납니다.</p></div>`; return; }
  const stageRows = run.stages.map((stage) => `<div class="timeline-item"><span class="timeline-dot ${statusClass(stage.status)}"></span><span class="timeline-name">${escapeHtml(stage.name)}</span><span class="timeline-status">${escapeHtml(labels[stage.status] || stage.status)}</span></div>`).join("");
  panel.innerHTML = `<div class="detail-header"><div><p class="overline">RUN DETAIL</p><h2>${escapeHtml(run.keyword)}</h2><p class="detail-id">${escapeHtml(run.run_id)} · ${escapeHtml(run.topic_id)}</p></div>${statusChip(run.status)}</div><section class="detail-section"><h3>Gate 상태</h3><div class="gate-grid"><div class="gate"><span>Q1 / 콘텐츠</span><strong>${escapeHtml(labels[run.q1] || run.q1)}</strong></div><div class="gate"><span>Q2 / Notion</span><strong>${escapeHtml(labels[run.q2] || run.q2)}</strong></div></div></section><section class="detail-section"><h3>단계 타임라인</h3><div class="timeline">${stageRows}</div></section><section class="detail-section"><h3>안전한 증거</h3><p class="evidence">로그: ${escapeHtml(run.log_path)}<br>${run.error ? `메시지: ${escapeHtml(run.error)}` : "오류 메시지 없음"}</p></section>`;
}

async function load() {
  const list = document.querySelector("#run-list");
  try {
    const response = await fetch("/api/snapshot", {cache:"no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    state.runs = data.runs || [];
    renderSummary(data.summary || {total:0, by_status:{}});
    document.querySelector("#generated-at").textContent = formatDate(data.generated_at);
    const statuses = [...new Set(state.runs.map((run) => run.status))];
    document.querySelector("#status-filter").innerHTML = `<option value="all">모든 상태</option>${statuses.map((status) => `<option value="${escapeHtml(status)}">${escapeHtml(labels[status] || status)}</option>`).join("")}`;
    renderRuns(); renderDetail(state.runs.find((run) => run.run_id === state.selected));
  } catch (error) { list.innerHTML = `<div class="error-row">실행 로그를 읽지 못했습니다. ${escapeHtml(error.message)}</div>`; document.querySelector("#generated-at").textContent = "읽기 오류"; }
}

function setManualStatus(message, kind = "info", action = null) {
  const status = document.querySelector("#manual-status");
  status.className = `manual-status ${kind}`;
  status.innerHTML = escapeHtml(message);
  if (action) {
    const button = document.createElement("button");
    button.className = "button button-quiet";
    button.type = "button";
    button.textContent = action.label;
    button.addEventListener("click", action.handler, { once: true });
    status.append(" ", button);
  }
}

function setManualBusy(busy) {
  document.querySelector("#manual-submit").disabled = busy;
  document.querySelector("#manual-topic-source").disabled = busy;
  document.querySelector("#manual-keyword").disabled = busy || document.querySelector("#manual-topic-source").value !== "user_defined";
}

function confirmationPreview(task) {
  const preview = task.confirmation_preview;
  if (!preview || preview.action !== "naver-draft-save" || !preview.target_blog_id || !preview.title || !Array.isArray(preview.images) || !preview.images.length) return null;
  return preview;
}

function showConfirmationAction(task) {
  const preview = confirmationPreview(task);
  if (!preview) {
    setManualStatus("임시저장 차단 · 대상 블로그·제목·이미지 확인 정보가 없습니다.", "error");
    return;
  }
  setManualStatus(`Notion Q2 통과 · ${preview.target_blog_id} · ${preview.title} · 이미지 ${preview.images.length}개`, "warning", {
    label: "대상 확인 후 임시저장",
    handler: () => confirmManualRun(task),
  });
}

async function pollManualRun(taskId) {
  const response = await fetch(`/api/manual-run/${encodeURIComponent(taskId)}`, {cache:"no-store"});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const task = await response.json();
  state.manualTask = task;
  if (task.status === "queued" || task.status === "running") {
    setManualStatus(`${task.status === "queued" ? "대기 중" : "파이프라인 실행 중"} · ${task.task_id}`, "warning");
    window.setTimeout(() => pollManualRun(taskId).catch((error) => setManualStatus(`상태를 읽지 못했습니다. ${error.message}`, "error")), 800);
    return;
  }
  setManualBusy(false);
  if (task.status === "failed") {
    setManualStatus(`실행 차단 · ${task.message || task.error || "원인 미상"}`, "error");
  } else if (task.result_status === "awaiting_user_confirmation") {
    showConfirmationAction(task);
  } else if (task.result_status === "local-only") {
    setManualStatus(`콘텐츠 생성 완료 · ${task.message || "외부 저장 대기"}`, "warning", {
      label: "외부 저장 실행",
      handler: () => continueExternal(task.task_id),
    });
  } else {
    setManualStatus(`실행 완료 · ${task.result_status || "상태 확인 필요"} · ${task.run_id || task.task_id}`, "success");
  }
  await load();
}

async function continueExternal(taskId) {
  setManualStatus("외부 저장 경로 확인 중입니다.", "warning");
  try {
    const response = await fetch(`/api/manual-run/${encodeURIComponent(taskId)}/external`, {method:"POST"});
    const task = await response.json();
    if (!response.ok) throw new Error(task.error || `HTTP ${response.status}`);
    state.manualTask = task;
    if (task.result_status === "awaiting_user_confirmation") {
      showConfirmationAction(task);
    } else if (task.result_status === "local-only") {
      setManualStatus(task.message || "외부 저장 대기 · 연결이 필요합니다.", "warning");
    } else {
      setManualStatus(`외부 저장 완료 · ${task.run_id || task.task_id}`, "success");
    }
    await load();
  } catch (error) {
    setManualStatus(`외부 저장 실패 · ${error.message}`, "error");
  }
}

async function confirmManualRun(waitingTask) {
  const preview = confirmationPreview(waitingTask);
  if (!preview) {
    setManualStatus("임시저장 차단 · 최종 확인 정보가 불완전합니다.", "error");
    return;
  }
  const imageNames = preview.images.map((path) => path.split("/").pop()).join(", ");
  const confirmed = window.confirm([
    "네이버 임시저장 전 최종 확인",
    "",
    `대상 블로그: ${preview.target_blog_id}`,
    `제목: ${preview.title}`,
    `이미지 ${preview.images.length}개: ${imageNames}`,
    "동작: 네이버 블로그 임시저장 (발행하지 않음)",
    "",
    "위 내용으로 임시저장할까요?",
  ].join("\n"));
  if (!confirmed) {
    showConfirmationAction(waitingTask);
    return;
  }
  setManualStatus("확인 처리 중 · 네이버 임시저장을 검증하고 있습니다.", "warning");
  try {
    const response = await fetch(`/api/manual-run/${encodeURIComponent(waitingTask.task_id)}/confirm`, {method:"POST"});
    const task = await response.json();
    if (!response.ok) throw new Error(task.error || `HTTP ${response.status}`);
    state.manualTask = task;
    setManualStatus(`네이버 임시저장 완료 · ${task.run_id || task.task_id}`, "success");
    await load();
  } catch (error) {
    setManualBusy(false);
    setManualStatus(`임시저장 실패 · ${error.message}`, "error");
  }
}

function toggleTopicSource() {
  const userDefined = document.querySelector("#manual-topic-source").value === "user_defined";
  const keyword = document.querySelector("#manual-keyword");
  keyword.disabled = !userDefined;
  keyword.required = userDefined;
  if (!userDefined) keyword.value = "";
}

async function startManualRun(event) {
  event.preventDefault();
  const topicSource = document.querySelector("#manual-topic-source").value;
  const keyword = document.querySelector("#manual-keyword").value.trim();
  if (topicSource === "user_defined" && !keyword) {
    setManualStatus("사용자 주제는 키워드를 입력해야 합니다.", "error");
    return;
  }
  const topicDescription = topicSource === "auto_selected" ? "자동 주제 선정" : `사용자 주제: ${keyword}`;
  if (!window.confirm(`${topicDescription}으로 워크플로우를 시작할까요?`)) return;
  setManualBusy(true);
  setManualStatus("실행 요청 중입니다.", "warning");
  try {
    const response = await fetch("/api/manual-run", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(topicSource === "auto_selected" ? {auto_topic:true} : {keyword}),
    });
    const task = await response.json();
    if (!response.ok) throw new Error(task.error || `HTTP ${response.status}`);
    await pollManualRun(task.task_id);
  } catch (error) {
    setManualBusy(false);
    setManualStatus(`실행 요청 실패 · ${error.message}`, "error");
  }
}

document.querySelector("#refresh").addEventListener("click", load);
document.querySelector("#search").addEventListener("input", renderRuns);
document.querySelector("#status-filter").addEventListener("change", renderRuns);
document.querySelector("#manual-topic-source").addEventListener("change", toggleTopicSource);
document.querySelector("#manual-run-form").addEventListener("submit", startManualRun);
toggleTopicSource();
load();

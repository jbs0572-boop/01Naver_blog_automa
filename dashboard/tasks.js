(() => {
  const labels = {queued:"대기",running:"실행 중",cancelling:"중단 중",awaiting_user_confirmation:"확인 필요",ready_for_naver:"확인 필요","local-only":"확인 필요",blocked:"확인 필요",failed:"실패",cancelled:"취소",draft_saved:"임시저장 완료"};
  const stages = {"topic-selector":"주제 선정",researcher:"조사",writer:"작성","image-maker":"이미지","content-assembler":"조립","notion-rider":"Notion","naver-rider":"Naver"};
  const state = {items:[],selected:null,selectedRun:null,selectedIds:new Set(),cursor:null,loading:false,error:false,listSignature:"",detailRequest:0,lastObservedAt:null,searchTimer:null};
  const $ = selector => document.querySelector(selector);
  const value = (input, fallback="—") => input == null || input === "" ? fallback : String(input);
  const esc = input => value(input, "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[char]));
  const tone = status => ["draft_saved"].includes(status) ? "success" : ["failed","cancelled"].includes(status) ? "error" : ["queued","running","cancelling"].includes(status) ? "warning" : "info";
  const time = input => input ? new Date(input).toLocaleString("ko-KR", {timeZone:"Asia/Seoul",month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit",hour12:false}) : "—";
  const elapsed = seconds => Number.isFinite(seconds) ? seconds < 60 ? `${seconds}초` : seconds < 3600 ? `${Math.floor(seconds / 60)}분` : `${Math.floor(seconds / 3600)}시간 ${Math.floor(seconds % 3600 / 60)}분` : "—";
  const usage = item => item.usage?.total_tokens == null ? "집계 전" : new Intl.NumberFormat("ko-KR").format(item.usage.total_tokens);
  function reason(item) {
    if (item.effective_status === "queued") return item.queue_position ? `앞선 Job ${item.queue_position - 1}건 실행 대기` : "실행 슬롯 대기";
    if (item.effective_status === "local-only") return "외부 저장 대기";
    if (["awaiting_user_confirmation","ready_for_naver"].includes(item.effective_status)) return "네이버 임시저장 확인 필요";
    if (item.effective_status === "blocked") return item.run?.error || "Gate 확인 필요";
    if (item.effective_status === "failed") return item.run?.error || "기록된 실패 원인 확인";
    if (item.effective_status === "cancelling") return "현재 단계 종료 후 중단";
    if (item.effective_status === "running") return "현재 단계 실행 중";
    return item.effective_status === "draft_saved" ? "발행하지 않고 저장됨" : "—";
  }
  function renderSummary(summary = {}, nextRun = null) {
    const counts = summary.by_status || {};
    const cards = [["실행 중",(counts.running || 0) + (counts.cancelling || 0),"현재 처리 중","attention"],["대기",counts.queued || 0,"실행 슬롯 대기",""],["확인 필요",(counts.blocked || 0) + (counts["local-only"] || 0) + (counts.awaiting_user_confirmation || 0) + (counts.ready_for_naver || 0),"외부 동작 또는 Gate","blocked"],["오늘 완료",summary.today_draft_saved || 0,"네이버 임시저장","success"],["다음 예약",nextRun ? time(nextRun) : "—","예정 시각 · 시작 보장 아님",""]];
    $("#task-summary").innerHTML = cards.map(([label,total,note,klass]) => `<article class="summary-card ${klass}"><div class="label">${label}</div><div class="value">${total}</div><div class="note">${note}</div></article>`).join("");
  }
  function row(item) {
    const action = item.cancel_action;
    const selectable = action?.scope === "queued_only" && item.effective_status === "queued";
    const cancel = action ? `<button class="button button-quiet task-cancel" type="button" data-task-id="${esc(item.task_id)}" data-batch-id="${esc(item.batch_id)}" data-child-id="${esc(item.child_id)}" data-nonce="${esc(action.nonce)}" data-scope="${esc(action.scope)}">${item.effective_status === "running" ? "중단 요청" : "취소"}</button>` : "";
    const check = selectable ? `<input class="task-check" type="checkbox" aria-label="${esc(item.keyword || "작업")} 선택" data-task-id="${esc(item.task_id)}" ${state.selectedIds.has(item.task_id) ? "checked" : ""}>` : "";
    const stage = item.current_stage ? `${esc(stages[item.current_stage] || item.current_stage)} ${item.completed_stage_count ?? 0}/7` : `${item.completed_stage_count ?? 0}/7`;
    return `<article class="task-row ${item.task_id === state.selected ? "is-selected" : ""}" data-task-id="${esc(item.task_id)}">${check}<button class="task-select" type="button" data-task-id="${esc(item.task_id)}" aria-current="${item.task_id === state.selected}"><span class="task-topic"><strong>${esc(item.display_id || item.run_id || item.task_id)}</strong><b>${esc(item.keyword || "주제 선정 대기")}</b><small>${esc(item.run_id || "run_id 배정 전")}</small></span><span class="task-time"><small>예정 ${time(item.scheduled_at)}</small><small>시작 ${time(item.started_at)}</small></span><span class="task-stage"><strong>${stage}</strong><progress max="7" value="${item.completed_stage_count ?? 0}" aria-label="7단계 중 ${item.completed_stage_count ?? 0}단계 완료"></progress></span><span class="task-state"><span class="task-status status-${tone(item.effective_status)}">${esc(labels[item.effective_status] || item.effective_status)}</span><small>${esc(reason(item))}</small></span><span>${elapsed(item.duration_seconds)}</span><span class="task-usage"><strong>${usage(item)}</strong><small>${item.usage?.usage_observed_at ? `관측 ${time(item.usage.usage_observed_at)}` : "사용량 관측 전"}</small></span></button>${cancel}</article>`;
  }
  function renderList() {
    const panel = $("#task-list");
    if (state.loading && !state.items.length) { panel.innerHTML = '<p class="task-empty">작업을 불러오는 중입니다.</p>'; return; }
    if (state.error && !state.items.length) { panel.innerHTML = '<p class="task-empty task-error">작업 연결이 끊겼습니다. 마지막 갱신 시각을 확인하세요.</p>'; return; }
    if (!state.items.length) { panel.innerHTML = '<p class="task-empty">조건에 맞는 작업이 없습니다.</p>'; return; }
    const signature = JSON.stringify([state.items.map(item => [item.task_id,item.display_id,item.keyword,item.scheduled_at,item.started_at,item.effective_status,item.current_stage,item.completed_stage_count,item.duration_seconds,item.usage,item.run?.error,item.cancel_action]),state.selected,[...state.selectedIds]]);
    if (signature === state.listSignature) return;
    state.listSignature = signature;
    panel.innerHTML = state.items.map(row).join("");
    panel.querySelectorAll(".task-select").forEach(button => button.addEventListener("click", () => select(button.dataset.taskId)));
    panel.querySelectorAll(".task-cancel").forEach(button => button.addEventListener("click", () => cancel(button.dataset)));
    panel.querySelectorAll(".task-check").forEach(input => input.addEventListener("change", () => { input.checked ? state.selectedIds.add(input.dataset.taskId) : state.selectedIds.delete(input.dataset.taskId); updateSelection(); state.listSignature = ""; }));
    updateSelection();
  }
  function renderDetail(run, item = null) {
    const panel = $("#detail");
    if (!run) { panel.innerHTML = `<div class="empty-detail"><span class="detail-mark" aria-hidden="true">↗</span><h2>${item ? esc(item.keyword) : "작업을 선택하세요"}</h2><p>${item ? "Job이 실행을 시작하면 단계별 상세와 내부 run_id가 표시됩니다." : "목록에서 Job을 선택하면 실행 시각, 단계, Gate, 오류와 확인된 토큰을 조회합니다."}</p></div>`; return; }
    const stageRows = (run.stages || []).map(stage => `<details class="timeline-item"><summary><span class="timeline-dot ${tone(stage.status)}"></span><span class="timeline-name">${esc(stages[stage.name] || stage.name)}</span><span class="timeline-status">${esc(stage.status)}</span></summary><div class="stage-metrics"><span>시작 ${time(stage.started_at)}</span><span>종료 ${time(stage.ended_at)}</span><span>${stage.usage?.total_tokens == null ? "토큰 집계 전" : `${new Intl.NumberFormat("ko-KR").format(stage.usage.total_tokens)} 토큰`}</span></div>${stage.message ? `<p class="evidence">${esc(stage.message)}</p>` : ""}</details>`).join("");
    panel.innerHTML = `<div class="detail-header"><div><p class="overline">JOB DETAIL</p><h2>${esc(run.keyword)}</h2><p class="detail-id">내부 run_id · ${esc(run.run_id)}</p></div><span class="task-status status-${tone(run.status)}">${esc(labels[run.status] || run.status)}</span></div><dl class="job-facts"><div><dt>실제 시작</dt><dd>${time(run.started_at)}</dd></div><div><dt>종료</dt><dd>${time(run.ended_at)}</dd></div><div><dt>사용량 관측</dt><dd>${time(run.usage?.usage_observed_at)}</dd></div></dl><section class="detail-section"><h3>Gate 상태</h3><div class="gate-grid"><div class="gate"><span>Q1 / 콘텐츠</span><strong>${esc(run.q1)}</strong></div><div class="gate"><span>Q2 / Notion</span><strong>${esc(run.q2)}</strong></div></div></section><section class="detail-section"><h3>7단계 실행</h3><div class="timeline">${stageRows}</div></section><section class="detail-section"><h3>로그와 조치</h3><p class="evidence">${run.error ? `원인: ${esc(run.error)}<br>` : ""}로그: ${esc(run.log_path)}</p></section>`;
    if (window.DashboardPreview) { const button = document.createElement("button"); button.type="button"; button.className="button button-quiet"; button.textContent="완성 글 보기"; button.addEventListener("click", async () => { const target=document.createElement("section"); target.className="detail-section"; panel.append(target); await window.DashboardPreview.open(run.run_id,target); }); panel.prepend(button); }
  }
  async function select(taskId) {
    const item = state.items.find(candidate => candidate.task_id === taskId);
    state.selected = taskId; state.listSignature = ""; renderList();
    if (!item?.run_id) { state.selectedRun = null; renderDetail(null, item); return; }
    const request = ++state.detailRequest;
    $("#detail").innerHTML = '<div class="loading-row">선택한 Job의 상세를 읽는 중입니다.</div>';
    try { const response = await fetch(`/api/runs/${encodeURIComponent(item.run_id)}`, {cache:"no-store"}); if (!response.ok) throw new Error(`HTTP ${response.status}`); const run = await response.json(); if (request === state.detailRequest && state.selected === taskId) { state.selectedRun = run; renderDetail(run,item); } }
    catch (error) { if (request === state.detailRequest) $("#detail").innerHTML = `<div class="panel-error"><h2>상세 조회 실패</h2><p>${esc(error.message)}</p></div>`; }
  }
  function updateSelection() { $("#task-cancel-selected").disabled = !state.selectedIds.size; }
  async function cancel(data) {
    const button = [...document.querySelectorAll(".task-cancel")].find(node => node.dataset.taskId === data.taskId);
    if (!button || button.disabled) return; button.disabled = true;
    try { const response = await fetch(`/api/manual-run/${encodeURIComponent(data.batchId)}/children/${encodeURIComponent(data.childId)}/cancel`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({nonce:data.nonce,scope:data.scope})}); if (!response.ok) throw new Error(`HTTP ${response.status}`); await load(); }
    catch (error) { button.disabled=false; message(`취소 결과를 확인하지 못했습니다. ${error.message}`,"error"); }
  }
  async function cancelSelected() { const targets=state.items.filter(item => state.selectedIds.has(item.task_id) && item.cancel_action?.scope === "queued_only"); state.selectedIds.clear(); updateSelection(); for (const item of targets.slice(0,3)) await cancel({taskId:item.task_id,batchId:item.batch_id,childId:item.child_id,nonce:item.cancel_action.nonce,scope:item.cancel_action.scope}); }
  function message(text, kind="info") { const node=$("#task-status-message"); node.textContent=text; node.className=`manual-status ${kind}`; }
  async function nextSchedule() { try { const response=await fetch("/api/schedule",{cache:"no-store"}); return response.ok ? (await response.json()).next_run : null; } catch { return null; } }
  async function load(append=false) {
    if (state.loading || document.hidden || (window.DashboardNavigation?.route?.() || "tasks") !== "tasks") return;
    state.loading=true;
    try { const params=new URLSearchParams({limit:"20"}); const query=$("#task-search").value.trim(); const filter=$("#task-status-filter").value; if(query) params.set("q",query); if(filter!=="all") params.set("status",filter); if(append&&state.cursor) params.set("cursor",state.cursor); const response=await fetch(`/api/tasks?${params}`,{cache:"no-store"}); if(!response.ok) throw new Error(`HTTP ${response.status}`); const data=await response.json(); state.items=append?[...state.items,...(data.items||[])]:data.items||[]; state.cursor=data.next_cursor||null; state.error=false; state.lastObservedAt=data.server_now||new Date().toISOString(); $("#task-more").hidden=!state.cursor; renderSummary(data.global_summary,await nextSchedule()); message(`마지막 조회 ${time(state.lastObservedAt)} · 목록 연결됨`,"success"); state.selectedIds=new Set([...state.selectedIds].filter(id=>state.items.some(item=>item.task_id===id))); renderList(); }
    catch(error){state.error=true;message(`연결 끊김 · 마지막 조회 ${time(state.lastObservedAt)} · ${error.message}`,"error");renderList();} finally{state.loading=false;renderList();}
  }
  window.DashboardTasks={load,select,state,status:message};
  document.addEventListener("DOMContentLoaded",()=>{$("#task-search")?.addEventListener("input",()=>{clearTimeout(state.searchTimer);state.searchTimer=setTimeout(()=>load(),300);});$("#task-status-filter")?.addEventListener("change",()=>load());$("#task-cancel-selected")?.addEventListener("click",cancelSelected);$("#task-more")?.addEventListener("click",()=>load(true));load();});
  window.addEventListener("hashchange",()=>load()); window.setInterval(()=>load(),10_000); document.addEventListener("visibilitychange",()=>{if(!document.hidden)load();});
})();

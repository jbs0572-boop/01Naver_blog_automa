(() => {
  const form = document.querySelector('#schedule-form');
  const times = document.querySelector('#schedule-times');
  const status = document.querySelector('#schedule-status');
  const preset = document.querySelector('#schedule-preset');
  let saved;
  let progressData;
  let refreshing = false;
  let historySignature = '';
  const historyPanel = document.querySelector('#schedule-history');
  async function retryOccurrence(item, button) {
    button.disabled = true;
    const nonce = button.dataset.nonce || `${item.occurrence_id}-${Date.now()}`;
    button.dataset.nonce = nonce;
    try {
      const response = await fetch('/api/schedule/retry', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({occurrence_id:item.occurrence_id, nonce})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
      status.textContent = `예약일 ${result.as_of_date} 재실행 접수 · ${result.batch_id || result.status}`;
      await refreshProgress();
      window.DashboardNotifications?.load?.();
    } catch (error) { status.textContent = `예약 재실행 실패 · ${error.message}`; }
    finally { button.disabled = false; }
  }
  function renderHistory() {
    if (!historyPanel) return;
    const history = [...(progressData?.history || [])].reverse();
    const signature = JSON.stringify(history.slice(0, 20).map(item => [item.occurrence_id, item.at, item.status, item.error]));
    if (signature === historySignature) return;
    historySignature = signature;
    historyPanel.replaceChildren();
    history.slice(0, 20).forEach((item) => {
      const row = document.createElement('div'); row.className = 'schedule-history-row';
      const label = document.createElement('span');
      const at = new Date(item.at).toLocaleString('ko-KR', {timeZone:'Asia/Seoul', dateStyle:'medium', timeStyle:'short'});
      label.textContent = `${at} · ${{missed:'미실행',failed:'접수 실패',submitted:'실행 접수',claimed:'접수 중'}[item.status] || item.status}`;
      row.append(label);
      if (['missed','failed'].includes(item.status) && item.occurrence_id) {
        const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'button button-quiet';
        retry.textContent = `이 예약 다시 실행 (${item.at.slice(0,10)})`;
        retry.addEventListener('click', () => retryOccurrence(item, retry)); row.append(retry);
      }
      historyPanel.append(row);
    });
  }
  function updateProgress() {
    for (const row of times.children) {
      const value = row.querySelector('input[type="time"]').value;
      const enabled = row.querySelector('input[type="checkbox"]').checked;
      const execution = progressData?.executions.find(item => item.entry_id === row.dataset.entryId || item.time === value);
      const child = execution?.child;
      let percent = null;
      let message = saved?.enabled ? '예약 대기' : '예약 중지';
      if (!enabled) message = '이 시간 꺼짐';
      else if (!value || !saved?.times.includes(value)) message = '저장 전';
      else if (execution) {
        message = {missed:'미실행', failed:'연결 실패', claimed:'실행 확인 중', submitted:'실행 대기'}[execution.status] || '상태 확인 중';
        if (execution.run) {
          const metrics = window.RunMetrics.describe(execution.run);
          percent = metrics.percent; message = metrics.next;
        } else if (child) {
          message = {queued:'실행 대기',running:'작업 진행 중',failed:'실행 실패',completed:'실행 종료'}[child.status] || message;
          if (child.result_status === 'local-only') message = '외부 저장 대기';
          if (child.result_status === 'awaiting_user_confirmation') message = '임시저장 확인 대기';
          if (child.result_status === 'draft_saved') { percent = 100; message = '임시저장 완료'; }
        }
      }
      const box = row.querySelector('.schedule-progress');
      box.querySelector('strong').textContent = percent === null ? '—' : `${percent}%`;
      box.querySelector('.schedule-step').textContent = message;
      const bar = box.querySelector('progress');
      bar.hidden = percent === null; bar.value = percent ?? 0;
      box.querySelector('small').textContent = execution ? `${execution.at.slice(5,10).replace('-', '/')} 실행` : '실행 전';
    }
  }
  async function refreshProgress() {
    if (refreshing || document.hidden) return;
    refreshing = true;
    try {
      const response = await fetch('/api/schedule-status', {cache:'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      progressData = await response.json();
      updateProgress();
      renderHistory();
    } catch {
      for (const label of times.querySelectorAll('.schedule-step')) label.textContent = '연결 확인 필요';
    } finally { refreshing = false; }
  }
  function addTime(entry = '') {
    const value = typeof entry === 'string' ? entry : entry.time || '';
    const row = document.createElement('div');
    row.className = 'schedule-time';
    row.dataset.entryId = typeof entry === 'object' && entry.entry_id ? entry.entry_id : window.crypto.randomUUID();
    row.dataset.presetId = typeof entry === 'object' && entry.preset_id ? entry.preset_id : preset.value;
    const label = document.createElement('span');
    label.className = 'schedule-time-label';
    label.textContent = '작성 시작 시간';
    const input = document.createElement('input');
    input.type = 'time'; input.required = true; input.value = value;
    input.setAttribute('aria-label', '작성 시작 시간');
    input.addEventListener('input', updateProgress);
    const enabled = document.createElement('input');
    enabled.type = 'checkbox'; enabled.checked = typeof entry === 'object' ? entry.enabled !== false : true;
    enabled.className = 'schedule-enabled'; enabled.setAttribute('aria-label', `${value || '새'} 예약 켜기`);
    enabled.addEventListener('change', updateProgress);
    const remove = document.createElement('button');
    remove.type = 'button'; remove.className = 'button button-quiet';
    remove.textContent = '삭제'; remove.setAttribute('aria-label', `${value || '새'} 예약 시간 삭제`);
    remove.addEventListener('click', () => row.remove());
    const info = document.createElement('div');
    info.className = 'schedule-progress'; info.setAttribute('role', 'status');
    info.innerHTML = '<strong>—</strong><span class="schedule-step">예약 대기</span><progress max="100" value="0" aria-label="예약 작업 진행률" hidden></progress><small>실행 전</small>';
    const entryPreset = preset.cloneNode(true);
    entryPreset.removeAttribute('id');
    entryPreset.removeAttribute('hidden'); entryPreset.removeAttribute('aria-hidden'); entryPreset.removeAttribute('tabindex');
    entryPreset.className = 'schedule-entry-preset';
    entryPreset.setAttribute('aria-label', `${value || '새'} 예약 모델 프리셋`);
    entryPreset.value = row.dataset.presetId;
    entryPreset.addEventListener('change', () => { row.dataset.presetId = entryPreset.value; });
    const presetLabel = document.createElement('span');
    presetLabel.className = 'schedule-preset-label'; presetLabel.textContent = '모델 프리셋';
    const enabledLabel = document.createElement('label');
    enabledLabel.className = 'schedule-toggle'; enabledLabel.append(enabled, document.createTextNode('이 시간 켜기'));
    row.append(label, presetLabel, input, entryPreset, enabledLabel, remove, info); times.append(row);
  }
  function render(data) {
    saved = data;
    times.replaceChildren(); (data.entries || data.times || []).forEach(addTime);
    document.querySelector('#schedule-stop').disabled = false;
    document.querySelector('#schedule-stop').textContent = data.enabled ? '예약 중지' : '예약 재개';
    document.querySelector('#schedule-start').textContent = '변경 저장';
    const next = data.next_run ? new Intl.DateTimeFormat('ko-KR', {timeZone:'Asia/Seoul', month:'long', day:'numeric', hour:'2-digit', minute:'2-digit', hour12:false}).format(new Date(data.next_run)) : '';
    status.textContent = data.enabled ? `새 예약 접수 켜짐 · 매일 ${data.times.join(' · ')} · 다음 접수 ${next} KST` : '예약 중지됨 · 새 예약 접수에만 적용되며 이미 접수된 Job은 계속됩니다.';
    const last = data.history.at(-1);
    if (last) status.textContent += ` · 최근 예약: ${{submitted:'실행 요청 완료',missed:'미실행 (서버 꺼짐 또는 지연)',failed:'연결 실패',claimed:'실행 확인 필요'}[last.status] || last.status}`;
    updateProgress(); refreshProgress();
  }
  async function save(enabled) {
    const buttons = [...form.querySelectorAll('button')];
    buttons.forEach(button => { button.disabled = true; });
    try {
      const entries = [...times.children].map(row => ({entry_id: row.dataset.entryId, time: row.querySelector('input[type="time"]').value, enabled: row.querySelector('input[type="checkbox"]').checked, preset_id: row.querySelector('.schedule-entry-preset').value}));
      const payload = {enabled, entries};
      const response = await fetch('/api/schedule', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error);
      render(data);
    } catch (error) { status.textContent = `예약 저장 실패 · ${error.message}`; }
    finally { buttons.forEach(button => { button.disabled = false; }); }
  }
  form.addEventListener('submit', event => { event.preventDefault(); save(true); });
  document.querySelector('#schedule-add').addEventListener('click', () => { if (times.children.length < 12) addTime(); });
  document.querySelector('#schedule-stop').addEventListener('click', () => save(!saved?.enabled));
  fetch('/api/schedule', {cache:'no-store'}).then(async response => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  }).catch(error => { status.textContent = `예약을 불러오지 못했습니다 · ${error.message}`; });
  window.setInterval(refreshProgress, 5000);
  document.addEventListener('visibilitychange', refreshProgress);
})();

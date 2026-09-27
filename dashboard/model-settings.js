(() => {
  const labels = {"topic-selector":"주제 선정", researcher:"자료 조사", writer:"글 작성", "image-maker":"이미지 준비", "content-assembler":"최종 조립/검수"};
  let settings;
  const selectors = () => [...['#model-preset', '#manual-preset', '#schedule-preset'].map(value => document.querySelector(value)).filter(Boolean), ...document.querySelectorAll('.schedule-entry-preset')];
  function selected() { return document.querySelector('#model-preset')?.value || settings?.active_preset_id || 'default'; }
  function render(data) {
    settings = data;
    selectors().forEach(select => { const prior = select.closest?.('.schedule-time')?.dataset.presetId || select.value || data.active_preset_id; select.replaceChildren(...data.presets.map(preset => { const option = document.createElement('option'); option.value = preset.id; option.textContent = preset.name; return option; })); select.value = data.presets.some(item => item.id === prior) ? prior : data.active_preset_id; });
    const preset = data.presets.find(item => item.id === selected()) || data.presets[0];
    const rows = Object.entries(preset.stages).map(([stage, value]) => { const row = document.createElement('p'); row.className = 'manual-child-meta'; row.textContent = `${labels[stage]} · ${value.model} · 추론 ${value.reasoning_effort}`; return row; });
    const deterministic = document.createElement('p'); deterministic.className = 'manual-child-meta'; deterministic.textContent = 'Notion 저장 · 네이버 입력 · 자동화 로직 (LLM 호출 없음)';
    document.querySelector('#model-stage-settings').replaceChildren(...rows, deterministic);
    document.querySelector('#model-settings-status').textContent = `revision ${data.revision} · 실행 접수 시 설정 사본을 고정합니다.`;
  }
  async function load() { const response = await fetch('/api/model-settings', {cache:'no-store'}); const data = await response.json(); if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`); render(data); }
  async function save(event) {
    event.preventDefault(); const name = document.querySelector('#model-preset-name').value.trim(); if (!name || !settings) return;
    const source = settings.presets.find(item => item.id === selected()); const id = `preset-${Date.now()}`;
    const response = await fetch('/api/model-settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({revision:settings.revision, active_preset_id:id, presets:[...settings.presets, {id, name, stages:source.stages}]})});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`); document.querySelector('#model-preset-name').value = ''; render(data);
  }
  window.ModelSettings = {selected, load};
  document.querySelector('#model-preset')?.addEventListener('change', event => { selectors().forEach(select => { select.value = event.target.value; }); render(settings); });
  document.querySelector('#model-preset-form')?.addEventListener('submit', event => save(event).catch(error => { document.querySelector('#model-settings-status').textContent = `저장 실패 · ${error.message}`; }));
  load().catch(error => { document.querySelector('#model-settings-status').textContent = `설정 조회 실패 · ${error.message}`; });
})();

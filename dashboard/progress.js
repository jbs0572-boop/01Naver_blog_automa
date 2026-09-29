(() => {
  const names = {"topic-selector":"주제 선정", researcher:"자료 조사", writer:"글 작성", "image-maker":"이미지 준비", "content-assembler":"최종 검수", "notion-rider":"Notion 저장", "naver-rider":"네이버 임시저장"};
  const format = (value) => Number.isFinite(value) ? value.toLocaleString("ko-KR") : "집계 전";
  const formatDuration = (value) => {
    if (!Number.isFinite(value)) return "미기록";
    if (value < 1000) return `${Math.round(value)}ms`;
    const seconds = Math.round(value / 100) / 10;
    return seconds < 60 ? `${seconds}초` : `${Math.floor(seconds / 60)}분 ${Math.round(seconds % 60)}초`;
  };
  const completedStatuses = ["passed", "validated"];
  const activeStatuses = ["running", "failed", "blocked"];
  const attemptSummary = (stage) => stage.total_attempts == null
    ? "시도 미기록"
    : `총 ${stage.total_attempts}회 · 최근 ${formatDuration(stage.last_duration_ms)} · 누적 ${formatDuration(stage.duration_ms)}`;
  function describe(run) {
    const stages = run.stages || [];
    const completed = run.status === "draft_saved" ? 7 : stages.filter(stage => completedStatuses.includes(stage.status) && stage.name !== "naver-rider").length;
    const percent = Math.round(completed / 7 * 100);
    const current = stages.find(stage => activeStatuses.includes(stage.status)) || stages.find(stage => !completedStatuses.includes(stage.status) && stage.status !== "skipped");
    const step = names[current?.name] || "결과 확인";
    const next = run.status === "draft_saved" ? "임시저장 완료" : run.status === "awaiting_user_confirmation" ? "네이버 임시저장 진행 중" : run.status === "local-only" ? "네이버 저장 준비 중" : ["failed","blocked"].includes(run.status) ? `${step}에서 멈췄어요` : `${step} 단계`;
    return {completed, percent, next};
  }
  function render(run) {
    const {completed, percent, next} = describe(run);
    return `<div class="run-progress"><div class="progress-heading"><span>${next}</span><strong>${percent}%</strong></div><progress max="100" value="${percent}" aria-label="완료 단계 기준 진행률">${percent}%</progress><div class="progress-caption"><span>7단계 중 ${completed}단계 완료</span><span>토큰 ${format(run.usage?.total_tokens)}</span></div></div>`;
  }
  window.RunMetrics = {describe, render, format, formatDuration, attemptSummary, names};
})();

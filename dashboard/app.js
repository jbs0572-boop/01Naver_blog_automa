(() => {
  const $ = selector => document.querySelector(selector);
  function connection(kind, label) {
    const indicator = $("#connection-indicator");
    const status = $("#connection-status");
    if (indicator) indicator.className = `pulse ${kind}`;
    if (status) status.textContent = label;
  }
  async function refresh() {
    connection("warning", "작업 갱신 중");
    try {
      await window.DashboardTasks?.load?.();
      $("#generated-at").textContent = new Date().toLocaleTimeString("ko-KR", {hour:"2-digit", minute:"2-digit"});
      connection(window.DashboardTasks?.state?.error ? "error" : "success", window.DashboardTasks?.state?.error ? "작업 연결 끊김" : "작업 연결됨");
    } catch {
      connection("error", "작업 연결 끊김");
    }
  }
  $("#refresh")?.addEventListener("click", refresh);
  if (window.ManualRunDashboard) window.ManualRunDashboard.start({onSettled: () => window.DashboardTasks?.load?.()});
})();

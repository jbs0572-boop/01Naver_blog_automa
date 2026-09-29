(() => {
  const status = document.querySelector("#health-status");
  const detail = document.querySelector("#health-detail");
  const button = document.querySelector("#health-check");
  let currentStatus = "unknown";
  function render(data) {
    currentStatus = data.status;
    const labels = {unknown: "아직 점검하지 않음", checking: "읽기 전용 점검 중", ready: "연결 준비됨", failed: "확인 필요"};
    status.textContent = labels[data.status] || data.status;
    detail.textContent = `${data.next_action || ""}${data.checked_at ? ` · ${new Date(data.checked_at).toLocaleString("ko-KR")}` : ""}`;
    button.disabled = data.status === "checking";
  }
  async function load() {
    try {
      const response = await fetch("/api/health", {cache: "no-store"});
      if (response.ok) render(await response.json());
    } catch (error) {
      if (currentStatus !== "checking") throw error;
      detail.textContent = "상태를 다시 확인하고 있습니다.";
    }
    if (currentStatus === "checking") window.setTimeout(load, 500);
  }
  button.addEventListener("click", async () => {
    const response = await fetch("/api/health/check", {method: "POST", headers: window.dashboardMutationHeaders(), body: "{}"});
    render(await response.json());
    window.setTimeout(load, 500);
  });
  load();
})();

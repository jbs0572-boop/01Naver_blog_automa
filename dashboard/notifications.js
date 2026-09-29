(() => {
  const count = document.querySelector("#notification-count");
  const list = document.querySelector("#notification-list");
  async function markRead(id) {
    await fetch(`/api/notifications/${encodeURIComponent(id)}/read`, {method: "POST", headers: window.dashboardMutationHeaders(), body: "{}"});
    await load();
  }
  async function load() {
    const response = await fetch("/api/notifications", {cache: "no-store"});
    if (!response.ok) return;
    const data = await response.json();
    count.textContent = String(data.unread_count || 0);
    list.replaceChildren();
    if (!data.items.length) {
      const empty = document.createElement("p"); empty.className = "helper"; empty.textContent = "새 알림이 없습니다."; list.append(empty); return;
    }
    data.items.forEach((item) => {
      const row = document.createElement("button");
      row.type = "button"; row.className = `notification-item${item.read_at ? " is-read" : ""}`;
      row.textContent = `${item.message} · ${item.source_id}`;
      row.addEventListener("click", () => markRead(item.id));
      list.append(row);
    });
  }
  window.DashboardNotifications = {load};
  load();
})();

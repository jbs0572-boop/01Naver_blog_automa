(() => {
  const token = document.querySelector('meta[name="dashboard-csrf-token"]')?.getAttribute("content") ?? "";
  window.dashboardMutationHeaders = () => ({
    "Content-Type": "application/json",
    "X-Dashboard-CSRF": token,
  });
})();

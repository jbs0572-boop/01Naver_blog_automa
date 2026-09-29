(() => {
  function text(tag, className, content) {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = content;
    return node;
  }

  function renderBlock(block, runId) {
    if (block.type === "text") return text("p", "preview-text", block.content);
    if (block.type === "heading") return text(block.level === 3 ? "h4" : "h3", "preview-heading", block.content);
    if (block.type === "blank") return document.createElement("hr");
    if (block.type === "list") {
      const list = document.createElement("ul");
      block.items.forEach((item) => list.append(text("li", "", item)));
      return list;
    }
    if (block.type === "table") {
      const wrapper = document.createElement("div");
      wrapper.className = "preview-table-wrap";
      const table = document.createElement("table");
      block.rows.forEach((row) => {
        const tr = document.createElement("tr");
        row.forEach((cell) => tr.append(text("td", "", `${cell.key}: ${cell.value}`)));
        table.append(tr);
      });
      wrapper.append(table);
      return wrapper;
    }
    if (block.type === "image") {
      const figure = document.createElement("figure");
      const image = document.createElement("img");
      image.src = `/api/runs/${encodeURIComponent(runId)}/assets/${encodeURIComponent(block.asset_id)}`;
      image.alt = block.alt;
      figure.append(image);
      if (block.caption) figure.append(text("figcaption", "", block.caption));
      return figure;
    }
    return text("p", "preview-error", "지원하지 않는 블록입니다.");
  }

  async function open(runId, container) {
    container.replaceChildren(text("p", "helper", "검증된 완성 글을 불러오는 중입니다."));
    const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/preview`, {cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    if (data.status !== "ready") {
      container.replaceChildren(text("p", "helper", data.reason));
      return;
    }
    const controls = document.createElement("div");
    controls.className = "preview-controls";
    const width = document.createElement("button");
    width.className = "button button-quiet";
    width.type = "button";
    width.textContent = "모바일 폭 미리보기";
    const article = document.createElement("article");
    article.className = "preview-document";
    article.append(text("h3", "preview-title", data.title));
    data.blocks.forEach((block) => article.append(renderBlock(block, runId)));
    width.addEventListener("click", () => article.classList.toggle("is-mobile"));
    controls.append(width);
    if (data.notion_link?.status === "ready") {
      const notion = document.createElement("a");
      notion.className = "button button-quiet";
      notion.href = data.notion_link.url;
      notion.target = "_blank";
      notion.rel = "noopener noreferrer";
      notion.textContent = "Notion에서 열기";
      controls.append(notion);
    } else controls.append(text("span", "helper", data.notion_link?.reason || "Notion 링크 확인 불가"));
    container.replaceChildren(controls, article);
  }

  window.DashboardPreview = {open};
})();

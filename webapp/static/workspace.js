// Workspace navigation renders server data as text; answer rules stay in first_run.js.
const workspace = (() => {
  const el = (id) => document.getElementById(id);
  const panels = ["description-panel", "progress-panel", "asked", "reports-panel", "library-panel"];
  let current = null;
  let generation = 0;
  const show = (id) => {
    panels.forEach((name) => { el(name).hidden = name !== id; });
    for (const [button, panel] of [["description-nav", "description-panel"], ["questions-nav", "asked"], ["reports-nav", "reports-panel"]]) {
      el(button).setAttribute("aria-current", panel === id ? "page" : "false");
    }
  };
  const node = (tag, text, className) => {
    const item = document.createElement(tag);
    item.textContent = text;
    if (className) item.className = className;
    return item;
  };
  const labels = {"running": "In progress", "awaiting-answers": "Questions ready", "completed": "Report ready", "failed": "Stopped"};
  const message = (text) => { el("workspace-message").textContent = text; };
  const metadata = (data) => {
    el("description").readOnly = true;
    el("analysis-name").readOnly = true;
    el("frameworks").disabled = true;
    el("go").hidden = true;
    el("load").hidden = true;
    el("ask").disabled = true;
    current = data;
    el("analysis-header").hidden = false;
    el("entry-heading").hidden = true;
    el("analysis-title").textContent = data.name;
    el("breadcrumb-title").textContent = data.name;
    el("analysis-state").textContent = labels[data.status] || data.status;
    el("analysis-frameworks").textContent = data.frameworks.join(" · ");
    el("questions-nav").disabled = data.status !== "awaiting-answers";
    el("report-list").replaceChildren();
    data.reports.forEach((report, index) => {
      const link = node("a", "Report " + (index + 1), "analysis-row");
      link.href = "/report/" + encodeURIComponent(report.run);
      el("report-list").append(link);
    });
    if (!data.reports.length) el("report-list").append(node("p", "Your reports will appear here after the analysis finishes.", "muted"));
  };
  const fetchRun = async (id) => {
    const response = await fetch("/workspace/runs/" + encodeURIComponent(id));
    const data = await response.json();
    if (!response.ok) throw new Error(data.message);
    return data;
  };
  const library = async () => {
    ++generation;
    show("library-panel");
    el("analysis-header").hidden = true;
    el("breadcrumb-title").textContent = "Analyses";
    history.replaceState(null, "", "/?view=analyses");
    try {
      const response = await fetch("/workspace/runs");
      if (!response.ok) throw new Error("Could not load analyses. Try again.");
      const data = await response.json();
      const render = () => {
        el("analysis-list").replaceChildren();
        const found = data.filter((run) => run.name.toLocaleLowerCase().includes(el("analysis-search").value.toLocaleLowerCase()));
        found.forEach((run) => {
          const link = node("a", "", "analysis-row");
          link.href = "/?run=" + encodeURIComponent(run.run);
          link.append(node("strong", run.name), node("span", labels[run.status], "badge"));
          el("analysis-list").append(link);
        });
        if (!found.length) el("analysis-list").append(node("p", data.length ? "No analyses match your search." : "Start with a system description. Your analyses will appear here.", "muted"));
      };
      el("analysis-search").oninput = render;
      render();
    } catch (error) { message(error.message); }
  };
  // Reopening polls snapshots, never competes with the original SSE consumer.
  const open = async (id) => {
    const ticket = ++generation;
    try {
      const data = await fetchRun(id);
      if (ticket !== generation) return;
      metadata(data);
      history.replaceState(null, "", "/?run=" + encodeURIComponent(data.run));
      el("description").value = data.description;
      el("analysis-name").value = data.name;
      for (const checkbox of boxes) {
        const selected = data.selection.find((item) => item.name === checkbox.value);
        checkbox.checked = Boolean(selected);
        if (selected) optionsOf(checkbox).forEach((control) => {
          control.value = JSON.stringify(selected.options[control.dataset.option]);
        });
        sync(checkbox);
      }
      if (data.questions) showQuestions(data.questions);
      else if (data.status === "running") {
        show("progress-panel");
        el("status").hidden = false;
        el("status-text").textContent = "The analysis is running. This page updates automatically.";
        setTimeout(() => { if (ticket === generation) open(data.run); }, 1500);
      } else {
        show("reports-panel");
        if (data.status === "failed") message("The analysis stopped. Open an earlier paused run to retry, or start a new analysis.");
      }
    } catch (error) { message(error.message); }
  };
  el("library-nav").addEventListener("click", library);
  el("description-nav").addEventListener("click", () => show("description-panel"));
  el("questions-nav").addEventListener("click", () => show("asked"));
  el("reports-nav").addEventListener("click", () => show("reports-panel"));
  const track = async (id) => {
    history.replaceState(null, "", "/?run=" + encodeURIComponent(id));
    const ticket = generation;
    try {
      const data = await fetchRun(id);
      if (ticket === generation) metadata(data);
    } catch (error) { message(error.message); }
  };
  const grouped = () => {
    show("asked");
    if (current) metadata({...current, status: "awaiting-answers"});
    indexGroups();
    // Native labels generated by the answer renderer are preserved.
    el("questions").querySelectorAll("input,select").forEach((input) => {
      if (!input.labels?.length && !input.hasAttribute("aria-label")) {
        const row = input.closest("tr,p,label");
        const subject = row?.querySelector("b")?.textContent.trim() || "Answer";
        const cell = input.closest("td");
        const column = cell && cell.closest("table")?.querySelectorAll("tr:first-child th")[cell.cellIndex]?.textContent;
        input.setAttribute("aria-label", column ? subject + ": " + column : subject);
      }
    });
  };
  const indexGroups = () => {
    el("question-index").replaceChildren();
    const groups = [...el("questions").children].filter((item) => item.tagName === "DETAILS");
    let visible = 0;
    groups.forEach((group, index) => {
      // Read the renderer's row visibility; do not repeat its dependency rules.
      group.hidden = ![...group.children].some((row) =>
        (row.tagName === "P" || row.tagName === "TABLE") && !row.hidden);
      if (group.hidden) return;
      visible += 1;
      group.id = "question-group-" + index;
      const link = node("a", group.querySelector(".group-heading")?.textContent || group.querySelector("summary").textContent);
      link.href = "#" + group.id;
      link.addEventListener("click", () => {
        group.open = true;
        group.querySelector("summary").focus();
        el("question-index").querySelectorAll("a").forEach((item) => item.removeAttribute("aria-current"));
        link.setAttribute("aria-current", "true");
      });
      el("question-index").append(link);
    });
    el("question-progress").textContent = visible ? visible + " question groups" : "Review your answers";
  };
  el("questions").addEventListener("change", indexGroups);
  queueMicrotask(() => {
    const params = new URLSearchParams(location.search);
    if (params.get("run")) open(params.get("run"));
    else if (params.get("view") === "analyses") library();
    fetch("/workspace/runs").then((response) => response.ok ? response.json() : []).then((runs) => {
      runs.slice(0, 5).forEach((run) => {
        const link = node("a", run.name);
        link.href = "/?run=" + encodeURIComponent(run.run);
        el("recent-analyses").append(link);
      });
    }).catch(() => message("Recent analyses are unavailable. You can still start a new analysis."));
  });
  return {show, track, grouped};
})();

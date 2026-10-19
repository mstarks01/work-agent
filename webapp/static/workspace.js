// Workspace navigation renders server data as text; answer rules stay in first_run.js.
const workspace = (() => {
  const el = (id) => document.getElementById(id);
  const panels = ["description-panel", "progress-panel", "asked", "reports-panel", "library-panel"];
  let current = null;
  let generation = 0;
  let progressRun = null;
  let completedSteps = [];
  // A running analysis shows its progress under the tab of what it produces
  // next: Questions where it will stop to ask, Reports where it runs to a report.
  const progressTab = () => current?.pauses ? "questions-nav" : "reports-nav";
  const mark = (id) => {
    const tabs = {"description-panel": "description-nav", "asked": "questions-nav", "reports-panel": "reports-nav", "progress-panel": progressTab()};
    for (const button of ["description-nav", "questions-nav", "reports-nav"]) {
      el(button).setAttribute("aria-current", tabs[id] === button ? "page" : "false");
    }
  };
  const show = (id) => {
    panels.forEach((name) => { el(name).hidden = name !== id; });
    mark(id);
  };
  const running = () => current?.status === "running";
  const node = (tag, text, className) => {
    const item = document.createElement(tag);
    item.textContent = text;
    if (className) item.className = className;
    return item;
  };
  const labels = {"running": "In progress", "awaiting-answers": "Questions ready", "completed": "Report ready", "failed": "Stopped", "unreadable": "Cannot be read"};
  const message = (text) => { el("workspace-message").textContent = text; };
  const metadata = (data) => {
    el("description").readOnly = true;
    el("analysis-name").readOnly = true;
    el("frameworks").disabled = true;
    el("go").hidden = true;
    el("load").hidden = true;
    el("upload").hidden = true;
    el("transcript-note").hidden = true;
    el("transcript-file").disabled = true;
    el("ask").disabled = true;
    current = data;
    el("analysis-header").hidden = false;
    el("new-analysis-title").hidden = true;
    el("analysis-title").textContent = data.name;
    el("breadcrumb-title").textContent = data.name;
    el("analysis-state").textContent = labels[data.status] || data.status;
    el("analysis-frameworks").textContent = data.frameworks.join(" · ");
    el("questions-nav").disabled = !(data.status === "awaiting-answers" || (data.status === "running" && data.pauses));
    if (!el("progress-panel").hidden) mark("progress-panel");
    el("report-list").replaceChildren();
    data.reports.forEach((report) => {
      const link = node("a", "", "analysis-row");
      const generated = node("time", "Generated " + new Date(report.generated_at).toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"}));
      generated.dateTime = report.generated_at;
      link.append(node("strong", report.name), generated);
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
      const reportView = new URLSearchParams(location.search).get("view") === "reports";
      history.replaceState(null, "", "/?run=" + encodeURIComponent(data.run) + (reportView ? "&view=reports" : ""));
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
      // The snapshot lists only the reports the app serves.
      const served = data.reports.some((report) => report.run === data.run);
      if (data.questions) showQuestions(data.questions);
      else if (data.status === "running") {
        show("progress-panel");
        processing(data.completed_steps, data.pauses, undefined, data.run);
        el("status").hidden = false;
        setTimeout(() => { if (ticket === generation) open(data.run); }, 1500);
      } else if (served && new URLSearchParams(location.search).get("view") !== "reports") {
        location.replace("/report/" + encodeURIComponent(data.run));
      } else {
        show("reports-panel");
        if (data.status === "failed") message("The analysis stopped. Open an earlier paused run to retry, or start a new analysis.");
        if (data.status === "completed" && !served) message("The report of this analysis is withheld.");
      }
    } catch (error) { message(error.message); }
  };
  el("library-nav").addEventListener("click", library);
  el("description-nav").addEventListener("click", () => show("description-panel"));
  el("questions-nav").addEventListener("click", () => show(running() ? "progress-panel" : "asked"));
  el("reports-nav").addEventListener("click", () => {
    if (!running() || current.pauses) return show("reports-panel");
    // A run toward a report shows its progress above the earlier reports.
    show("progress-panel");
    el("reports-panel").hidden = false;
  });
  const track = async (id) => {
    history.replaceState(null, "", "/?run=" + encodeURIComponent(id));
    const ticket = generation;
    try {
      const data = await fetchRun(id);
      if (ticket === generation) metadata(data);
    } catch (error) { message(error.message); }
  };
  // Each quarter marks a completed stage, not a share of elapsed time.
  const completedStage = (step, pauses) => {
    if (step === "extract") return 1;
    if (["validate", "repair", "revalidate", "capabilities", "stated", "read", "assert"].includes(step)) return 2;
    if (pauses && step === "catalog") return 3;
    if (pauses && step === "pause") return 4;
    if (!pauses && step === "prepare") return 2;
    if (!pauses && (step.startsWith("join_") || step.startsWith("merge_")
        || step.startsWith("critic_") || step.startsWith("critic_failed_")
        || step.startsWith("router_") || step.startsWith("recritic_")
        || step.startsWith("rereview_"))) return 3;
    if (!pauses && step === "assemble") return 4;
    return 0;
  };
  const stageAfter = (step, pauses) => {
    if (!step) return pauses ? "Reading your description and identifying the parts of your system…"
      : "Preparing the analysis…";
    if (step === "extract" || step === "repair") return "Checking the system map for gaps or conflicting details…";
    if (step === "validate" || step === "revalidate") return "Noting the security measures you’ve already described…";
    if (step === "capabilities" || step === "stated" || step === "read") {
      return "Identifying details that would make the analysis more precise…";
    }
    if (step === "assert") return "Choosing the relevant checks for your system…";
    if (step === "catalog" || step === "pause") return "Generating questions…";
    if (step === "prepare" || step.startsWith("analyze_")) {
      return "Examining your system against the selected frameworks…";
    }
    if (step.startsWith("join_") || step.startsWith("merge_") || step.startsWith("critic_")
        || step.startsWith("critic_failed_")
        || step.startsWith("router_") || step.startsWith("recritic_")
        || step.startsWith("rereview_")) {
      return "Bringing the results together and checking the findings…";
    }
    if (step === "assemble") return "Putting your report together…";
    return "Continuing the analysis…";
  };
  const processing = (steps, pauses, initialStage, runId) => {
    if (runId && progressRun !== runId) {
      progressRun = runId;
      completedSteps = [];
      el("analysis-progress").value = 0;
    }
    if (steps?.length === 1 && !completedSteps.includes(steps[0])) {
      completedSteps.push(steps[0]);
    } else if (steps?.length > completedSteps.length) {
      completedSteps = steps;
    }
    const latest = completedSteps.at(-1);
    const progress = el("analysis-progress");
    progress.value = Math.max(progress.value, ...completedSteps.map((step) => completedStage(step, pauses)));
    progress.setAttribute("aria-valuetext", `${progress.value} of 4 stages complete`);
    const status = latest ? stageAfter(latest, pauses) : initialStage || stageAfter(null, pauses);
    el("status-text").textContent = `${progress.value} of 4 stages complete · ${status}`;
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
    const groups = [...el("list").children].filter((item) => item.classList.contains("question-group"));
    groups.forEach((group, index) => {
      // Read the renderer's row visibility; do not repeat its dependency rules.
      // A box's rows sit in it or under its "Answer each one separately".
      const each = group.querySelector(".each-one");
      group.hidden = ![...group.children, ...(each ? each.children : [])].some((row) =>
        (row.tagName === "P" || row.tagName === "TABLE") && !row.hidden);
      if (group.hidden) return;
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
  };
  el("questions").addEventListener("change", indexGroups);
  // Capture the entry route before live progress changes it to ?run=.
  const params = new URLSearchParams(location.search);
  queueMicrotask(() => {
    if (params.get("run")) open(params.get("run"));
    else if (params.get("view") === "analyses") library();
    fetch("/workspace/runs").then((response) => response.ok ? response.json() : []).then((runs) => {
      runs.slice(0, 5).forEach((run) => {
        const link = node("a", run.name);
        link.href = "/?run=" + encodeURIComponent(run.run);
        el("recent-analyses").append(link);
      });
      // Each earlier analysis whose answers a new one can carry (ADR 0079).
      const reuse = document.getElementById("answers-from");
      if (reuse && runs.length) {
        runs.forEach((run) => {
          const option = node("option", run.name);
          option.value = run.run;
          reuse.append(option);
        });
        el("answers-from-field").hidden = false;
      }
    }).catch(() => message("Recent analyses are unavailable. You can still start a new analysis."));
  });
  return {show, track, grouped, processing};
})();

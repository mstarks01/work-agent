// Workspace navigation renders server data as text; answer rules stay in first_run.js.
const workspace = (() => {
  const el = (id) => document.getElementById(id);
  const panels = ["description-panel", "progress-panel", "asked", "reports-panel", "library-panel"];
  let current = null;
  let generation = 0;
  let progressRun = null;
  let completedSteps = [];
  let stepStatus = null;
  let stepStarted = 0;
  let elapsedTimer = null;
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
    el("description-remaining").hidden = true;
    el("analysis-title").textContent = data.name;
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
  // Ask once with the native dialog; resolves true on Delete.
  const confirmDelete = (text) => new Promise((resolve) => {
    const dialog = el("confirm-delete");
    el("confirm-delete-text").textContent = text;
    const done = (answer) => { dialog.onclose = null; dialog.close(); resolve(answer); };
    el("confirm-delete-go").onclick = () => done(true);
    el("confirm-delete-cancel").onclick = () => done(false);
    dialog.onclose = () => resolve(false);
    dialog.showModal();
  });
  // One DELETE per analysis; a refusal does not stop the others.
  const deleteRuns = async (runs) => {
    const reports = runs.reduce((total, run) => total + run.reports.length, 0);
    const text = runs.length === 1
      ? "Delete “" + runs[0].name + "” and its " + counted(reports, "report", "reports") + "? You cannot undo this."
      : "Delete " + counted(runs.length, "analysis", "analyses") + " and their " + counted(reports, "report", "reports") + "? You cannot undo this.";
    if (!await confirmDelete(text)) return;
    const refused = [];
    for (const run of runs) {
      const response = await fetch("/workspace/runs/" + encodeURIComponent(run.run), {method: "DELETE"});
      if (!response.ok) refused.push(run.name + ": " + ((await response.json().catch(() => ({}))).message || "Could not delete it."));
    }
    await library();
    message(refused.length ? "Not deleted. " + refused.join(" ") : "Deleted " + counted(runs.length, "analysis", "analyses") + ".");
  };
  const rename = (run, cell) => {
    const input = document.createElement("input");
    input.value = run.title || run.name;
    input.maxLength = el("analysis-name").maxLength;
    input.setAttribute("aria-label", "New name for " + run.name);
    let settled = false;
    const save = async (keep) => {
      if (settled) return;
      settled = true;
      if (keep && input.value !== (run.title || run.name)) {
        const response = await fetch("/workspace/runs/" + encodeURIComponent(run.run) + "/title", {
          method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({title: input.value}),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) { await library(); message(data.message || "Could not rename it."); return; }
      }
      await library();
    };
    input.onkeydown = (event) => {
      if (event.key === "Enter") save(true);
      else if (event.key === "Escape") save(false);
    };
    input.onblur = () => save(true);
    cell.replaceChildren(input);
    input.focus();
    input.select();
  };
  const library = async () => {
    ++generation;
    show("library-panel");
    el("analysis-header").hidden = true;
    history.replaceState(null, "", "/?view=analyses");
    try {
      const response = await fetch("/workspace/runs");
      if (!response.ok) throw new Error("Could not load analyses. Try again.");
      const data = await response.json();
      let sortBy = "date";
      let descending = true;
      const selected = new Set();
      const fill = (select, values, label) => {
        const chosen = select.value;
        select.replaceChildren(select.options[0], ...values.map((value) => {
          const option = node("option", label(value));
          option.value = value;
          return option;
        }));
        select.value = values.includes(chosen) ? chosen : "";
      };
      fill(el("analysis-status"), Object.keys(labels), (status) => labels[status]);
      fill(el("analysis-framework"), [...document.querySelectorAll("input[name=framework]")].map((box) => box.value), (name) => name);
      let shown = [];
      const render = () => {
        el("analysis-rows").replaceChildren();
        const text = el("analysis-search").value.toLocaleLowerCase();
        const status = el("analysis-status").value;
        const framework = el("analysis-framework").value;
        shown = data.filter((run) =>
          [run.name, ...run.frameworks].some((value) => value.toLocaleLowerCase().includes(text))
          && (!status || run.status === status)
          && (!framework || run.frameworks.includes(framework)));
        shown.sort((left, right) => {
          const order = sortBy === "name"
            ? left.name.localeCompare(right.name, undefined, { sensitivity: "base", numeric: true })
            : new Date(left.created_at) - new Date(right.created_at);
          return (descending ? -order : order) || left.name.localeCompare(right.name);
        });
        shown.forEach((run) => {
          const link = node("a", run.name);
          link.href = "/?run=" + encodeURIComponent(run.run);
          const created = node("time", new Date(run.created_at).toLocaleDateString(undefined, {dateStyle: "medium"}));
          created.dateTime = run.created_at;
          const row = node("tr", "");
          const selectCell = node("td", "");
          const nameCell = node("td", "", "name-cell");
          const dateCell = node("td", "");
          const statusCell = node("td", "");
          const actionCell = node("td", "");
          const box = document.createElement("input");
          box.type = "checkbox";
          box.checked = selected.has(run.run);
          box.setAttribute("aria-label", "Select " + run.name);
          box.onchange = () => { box.checked ? selected.add(run.run) : selected.delete(run.run); update(); };
          const actions = node("div", "", "row-actions");
          const renameButton = node("button", "Rename");
          renameButton.type = "button";
          renameButton.setAttribute("aria-label", "Rename " + run.name);
          renameButton.onclick = () => rename(run, nameCell);
          const deleteButton = node("button", "Delete", "danger");
          deleteButton.type = "button";
          deleteButton.setAttribute("aria-label", "Delete " + run.name);
          deleteButton.onclick = () => deleteRuns([run]);
          actions.append(renameButton, deleteButton);
          selectCell.append(box);
          nameCell.append(link);
          dateCell.append(created);
          statusCell.append(node("span", labels[run.status] || run.status, "badge"));
          actionCell.append(actions);
          row.append(selectCell, nameCell, dateCell, statusCell, actionCell);
          el("analysis-rows").append(row);
        });
        el("analysis-list").hidden = !shown.length;
        el("analysis-empty").hidden = Boolean(shown.length);
        el("analysis-empty").textContent = data.length
          ? "No analyses match your filters."
          : "Start with a system description. Your analyses will appear here.";
        for (const key of ["name", "date"]) {
          el("sort-" + key).setAttribute("aria-sort", sortBy === key ? (descending ? "descending" : "ascending") : "none");
        }
        update();
      };
      // Only rows the filters show count as selected.
      const update = () => {
        const chosen = shown.filter((run) => selected.has(run.run));
        el("delete-selected").disabled = !chosen.length;
        el("delete-selected").textContent = chosen.length ? "Delete selected (" + chosen.length + ")" : "Delete selected";
        el("select-shown").checked = Boolean(shown.length) && chosen.length === shown.length;
        el("select-shown").indeterminate = Boolean(chosen.length) && chosen.length < shown.length;
      };
      el("select-shown").onchange = () => {
        shown.forEach((run) => { el("select-shown").checked ? selected.add(run.run) : selected.delete(run.run); });
        render();
      };
      el("delete-selected").onclick = () => deleteRuns(shown.filter((run) => selected.has(run.run)));
      el("library-panel").querySelectorAll("[data-sort]").forEach((button) => {
        button.onclick = () => {
          const key = button.dataset.sort;
          descending = sortBy === key ? !descending : key === "date";
          sortBy = key;
          render();
        };
      });
      el("analysis-search").oninput = render;
      el("analysis-status").onchange = render;
      el("analysis-framework").onchange = render;
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
      // A report's tabs open its analysis at its description or its report list.
      const view = ["description", "reports"].find((name) => name === new URLSearchParams(location.search).get("view"));
      history.replaceState(null, "", "/?run=" + encodeURIComponent(data.run) + (view ? "&view=" + view : ""));
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
        el("status").hidden = false;
        processing(data.completed_steps, data.pauses, undefined, data.run);
        setTimeout(() => { if (ticket === generation) open(data.run); }, 1500);
      } else if (view === "description") {
        show("description-panel");
      } else if (served && !view) {
        location.replace("/report/" + encodeURIComponent(data.run));
      } else {
        show("reports-panel");
        if (data.status === "failed") message("The analysis stopped. Open an earlier paused run to retry, or start a new analysis.");
        if (data.status === "completed" && !served) message("The report of this analysis is withheld.");
      }
    } catch (error) { message(error.message); }
  };
  // The link opens the list in place; library() writes the same URL.
  el("library-nav").addEventListener("click", (event) => {
    event.preventDefault();
    library();
  });
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
  // A one-second count shows that a long step still runs. It sits outside
  // the status region, so a screen reader hears only a change of step.
  const elapsed = () => {
    if (el("status").hidden) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
      return;
    }
    const seconds = Math.floor((Date.now() - stepStarted) / 1000);
    const clock = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    el("status-elapsed").textContent = `Still working on this step · ${clock}. Some steps take a few minutes.`;
  };
  const processing = (steps, pauses, initialStage, runId) => {
    if (runId && progressRun !== runId) {
      progressRun = runId;
      completedSteps = [];
      stepStatus = null;
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
    if (status !== stepStatus) {
      stepStatus = status;
      stepStarted = Date.now();
    }
    elapsedTimer ??= setInterval(elapsed, 1000);
    elapsed();
  };
  const grouped = () => {
    show("asked");
    if (current) metadata({...current, status: "awaiting-answers"});
    indexQuestions();
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
  // The index beside the list, built by question_list.js. A click marks the
  // link as the current one.
  const indexQuestions = () => indexGroups(el("question-index"), el("list"), (box, link) => {
    el("question-index").querySelectorAll("a").forEach((item) => item.removeAttribute("aria-current"));
    link.setAttribute("aria-current", "true");
  });
  el("questions").addEventListener("change", indexQuestions);
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

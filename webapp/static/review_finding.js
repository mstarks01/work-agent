  // Every value below is submitter prose or model output, so it reaches the
  // page as a text node and never as markup. There is no escape helper here
  // deliberately: with no innerHTML path there is nothing to forget to call.
  let current = null;

  const el = id => document.getElementById(id);

  function fill(item) {
    current = item;
    if (item.done) {
      el("card").style.display = "none";
      el("done").classList.add("open");
      el("left").textContent = "";
      return;
    }
    el("left").textContent = item.remaining + " left";
    // The question is the package's, not this page's: a threat and a
    // requirement are not ruled on in the same words.
    el("heading").textContent = item.question.heading;
    el("ask").textContent = item.question.ask;
    el("up").textContent = item.question.yes;
    el("down").textContent = item.question.no;
    el("case").textContent = item.case + " / " + item.lane;
    el("why").textContent = "You are being asked because " + item.why + ".";
    el("source").textContent = item.source;
    el("title").textContent = item.title;
    el("description").textContent = item.description;
    el("elements").textContent = "Cited: " + item.element_ids.join(", ");

    const quotes = el("quotes");
    quotes.replaceChildren();
    item.quotes.forEach(text => {
      const div = document.createElement("div");
      div.className = "quote";
      div.textContent = '"' + text + '"';
      quotes.appendChild(div);
    });

    const list = el("rlist");
    list.replaceChildren();
    ["substance", "style"].forEach(kind => {
      const rows = item.reasons.filter(r => r.kind === kind);
      if (!rows.length) return;
      const group = document.createElement("div");
      group.className = "rgroup " + kind;
      const head = document.createElement("h3");
      head.textContent = kind === "substance" ? "It is wrong" : "It is badly written";
      group.appendChild(head);
      rows.forEach(reason => {
        const button = document.createElement("button");
        button.textContent = reason.gloss;
        button.addEventListener("click", () => send("down", reason.code));
        group.appendChild(button);
      });
      list.appendChild(group);
    });
    el("reasons").classList.remove("open");
  }

  function load() {
    fetch("/api/next").then(r => r.json()).then(fill);
  }

  function send(verdict, reason) {
    if (!current || current.done) return;
    fetch("/api/vote", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Review-Token": TOKEN},
      body: JSON.stringify({
        fingerprint: current.fingerprint,
        verdict: verdict,
        reason: reason || null,
        note: ""
      })
    }).then(response => {
      if (!response.ok) { alert("That vote was refused."); return; }
      load();
    });
  }

  el("up").addEventListener("click", () => send("up", null));
  el("unsure").addEventListener("click", () => send("unsure", null));
  el("evidence").addEventListener("click", () => send("needs-evidence", null));
  el("down").addEventListener("click", () => el("reasons").classList.add("open"));
  el("cancel").addEventListener("click", () => el("reasons").classList.remove("open"));
  load();

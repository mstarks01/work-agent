  // Every string in R is untrusted: model-authored from the submitter's own
  // prose, or supplied by the caller. None of it is escaped for HTML.
  //
  // So nothing on this page interpolates a value into innerHTML. Text goes in
  // as textContent and structure is built as DOM nodes — which is why there is
  // no escape helper here to forget to call. A forgotten `textContent` shows
  // junk text and runs no script.
  //
  // `append` is the primitive that makes this cheap: it takes nodes and
  // strings, and a string always becomes a text node, never markup.
  const R = JSON.parse(document.getElementById("report").textContent);
  // The units each framework answers for, joined server-side: this page knows
  // no package, so which claim rules on which unit and what a unit says are
  // both handed to it. Same escape, same discipline — every string below is
  // still untrusted and still goes in as text.
  const UNITS = JSON.parse(document.getElementById("units").textContent);
  // The open facts each framework's conditional findings rest on, grouped and
  // ordered server-side. Each finding is placed under one fact; the page only
  // renders what it is handed.
  const OPEN_FACTS = JSON.parse(document.getElementById("open_facts").textContent);
  // The principals no element stands for yet, ranked server-side by how many
  // stated facts an answer would place. The page only renders what it is handed.
  const LINK_QUESTIONS = JSON.parse(document.getElementById("link_questions").textContent);
  // The open facts the conditional findings rest on, ranked server-side so
  // with the most-cited fact first. The page only renders them.
  const FACT_QUESTIONS = JSON.parse(document.getElementById("fact_questions").textContent);
  // The answer that says the submitter does not know. The service writes
  // nothing for it, so the fact stays open.
  const DONT_KNOW = "unknown";
  // The answers a facet takes, as the service lists them in FACET_ANSWERS.
  const FACET_CHOICES = [
    ["yes", "yes"], ["no", "no"], ["not applicable", "not applicable"],
    ["I don't know", DONT_KNOW],
  ];
  // How many of the reviewer's open facts used the fixed list of questions,
  // and how many it wrote in its own words, counted server-side.
  const FALLBACK = JSON.parse(document.getElementById("question_fallback").textContent);
  // True for a report the follow-up wrote: it asks nothing more (ADR 0054).
  const FINAL = JSON.parse(document.getElementById("final").textContent);
  // The run this report's follow-up started, where one holds it: the report
  // then asks nothing, and its follow-up's report is the next one to read.
  const RESUMED_BY = JSON.parse(document.getElementById("resumed_by").textContent);
  // A final report's answers, the corrections kept beside it, and the
  // findings they reach, built server-side (ADR 0054). Empty otherwise.
  const CORRECTIONS = JSON.parse(document.getElementById("corrections").textContent);
  const CORRECTED = new Set(CORRECTIONS.findings || []);
  // A first report's answers and link answers, built server-side, so its
  // follow-up can take a new answer to each. Empty otherwise.
  const EARLIER = JSON.parse(document.getElementById("earlier").textContent);
  const EARLIER_FACTS = EARLIER.answers || [];
  const EARLIER_LINKS = EARLIER.links || [];
  // The answers a follow-up question starts from: a facet answer with facets
  // left out, which the question asks again. Keyed by the fact's key.
  const RETAINED = new Map((EARLIER.retained || []).map(a => [JSON.stringify(a.key), a]));
  // Where each finding's facts came from, built server-side: the label of the
  // owner's answers Source, the attributes the owner answered, and why each
  // conditional finding's facts are still open.
  const PROVENANCE = JSON.parse(document.getElementById("provenance").textContent);
  // A display label for each element ID, a flow's as its two endpoints, and
  // for each evidence reference a lane may cite, such as an unstated attribute
  // or a boundary crossing. Built server-side by
  // analysis_service.open_facts.reference_labels, so the page parses no
  // reference.
  const NAMES = JSON.parse(document.getElementById("names").textContent);
  // The field each framework stamps a finding's lane into, or null where it
  // stamps none: the service's own table, so a framework added later needs
  // no edit here.
  const LANES = JSON.parse(document.getElementById("lanes").textContent);
  const laneOf = (framework, claim) => LANES[framework] ? claim[LANES[framework]] : null;
  // How each finding moved since the report a follow-up's answers came from,
  // matched server-side by analysis_service.report_changes. Empty for any
  // other report.
  const CHANGES = JSON.parse(document.getElementById("changes").textContent).findings || [];
  const CHANGED = new Map(CHANGES.filter(c => c.change !== "gone")
    .map(c => [`${c.framework}/${c.claim_id}`, c]));
  const ANSWERED_ATTRIBUTES = new Set(
    (PROVENANCE.answered_attributes || []).map(([element, attribute]) => `${element}>${attribute}`));
  const CONDITIONS = PROVENANCE.conditions || {};
  // Why an open fact is still open, as the owner reads it.
  const WHY_OPEN = {
    unknown: "nobody knew: an answer said \"I don't know\"",
    partial: "you answered part of it, and the parts left open keep it conditional",
    skipped: "skipped before the analysis",
    unanswered: "shown before the analysis and left blank",
    answered: "you answered it, and the analysis still did not find it settled",
    open: "not asked yet",
  };
  // The same reasons, as the "What remains open" summary counts them.
  const OPEN_SUMMARY = {
    unknown: (n) => `${n} wait on a fact nobody knew`,
    partial: (n) => `${n} on a fact you answered in part`,
    skipped: (n) => `${n} on a fact skipped before the analysis`,
    unanswered: (n) => `${n} on a fact left blank before the analysis`,
    answered: (n) => `${n} on a fact you answered that the analysis did not find settled`,
    open: (n) => `${n} on a fact nobody was asked`,
  };
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const code = (text) => el("code", null, text);
  const option = (label, value) => Object.assign(el("option", null, label), { value });
  // An answer as one line of text.
  const said = a => a.facets
    ? Object.entries(a.facets).map(([facet, value]) => `${facet}: ${value}`).join("; ")
    : (a.value === DONT_KNOW ? "I don't know" : a.value);
  // A link question's choices: each element it may be, then "None of these".
  const linkOptions = (select, ids) => {
    ids.forEach(id => select.append(option(NAMES[id] ? `${NAMES[id]} (${id})` : id, id)));
    select.append(option("None of these", "none"));
  };
  // One answer's editor, as the follow-up and a correction both show it.
  // `nodes` follow the label; `read` is the answer as the service takes it,
  // or null; `known` is whether it says more than "I don't know". `prefill`
  // is an answer to start from, and `changed` runs on every edit. With
  // `reopen` false, a known value offers no "I don't know", because the
  // follow-up refuses to reopen a settled fact or a settled facet.
  const offersDontKnow = (before, reopen) => reopen || !before || before === DONT_KNOW;
  let suggestLists = 0;
  const editorFor = (q, prefill, changed, reopen = true) => {
    if (q.form === "facets") {
      // One list per facet. Only a facet answered otherwise than `prefill`
      // is sent: the service keeps the earlier answer to a facet left out,
      // so a facet answered before offers no blank.
      const list = el("ul");
      const selects = q.facets.map(facet => {
        const before = (prefill && prefill.facets && prefill.facets[facet.id]) || "";
        const select = el("select");
        if (!before) select.append(option("(leave unanswered)", ""));
        FACET_CHOICES
          .filter(([, value]) => value !== DONT_KNOW || offersDontKnow(before, reopen))
          .forEach(([label, value]) => select.append(option(label, value)));
        select.dataset.key = JSON.stringify(q.key);
        select.dataset.facet = facet.id;
        select.dataset.before = before;
        select.value = before;
        select.addEventListener("change", changed);
        const item = el("li", null, `${facet.question} `);
        item.append(select);
        list.append(item);
        return select;
      });
      const given = () => Object.fromEntries(selects
        .filter(s => s.value && s.value !== s.dataset.before)
        .map(s => [s.dataset.facet, s.value]));
      return {
        nodes: [list],
        read: () => Object.keys(given()).length ? { key: q.key, facets: given() } : null,
        // The critic names the kind, not a facet, so a finding waiting on it
        // is covered only once every facet says more than "I don't know".
        // A facet kept from `prefill` counts, as the service merges it.
        known: () => selects.every(s => s.value && s.value !== DONT_KNOW),
      };
    }
    const value = (prefill && prefill.value) || "";
    const dontKnowOffered = offersDontKnow(value, reopen);
    let input;
    // "unknown" is the answer that says you do not know: the fact stays
    // open, and it covers no finding.
    let nodes;
    // `read` is the answer the row sends, or "" for none.
    let read = () => input.value.trim();
    if (q.choices.length) {
      input = el("select");
      input.append(option("(leave unanswered)", ""));
      q.choices.forEach(choice => input.append(option(NAMES[choice] ? `${NAMES[choice]} (${choice})` : choice, choice)));
      if (dontKnowOffered) input.append(option("I don't know", DONT_KNOW));
      input.value = value;
      input.addEventListener("change", changed);
      nodes = [input];
    } else if (q.form === "control") {
      // A control: say there is none, say you do not know, or name the
      // mechanism. The suggestions are a start; the text is the answer.
      input = el("input");
      input.type = "text";
      input.maxLength = q.max_length;
      input.placeholder = "type it, or pick a common one";
      const list = el("datalist");
      list.id = `suggest-${suggestLists++}`;
      q.suggestions.forEach(s => list.append(option(s, s)));
      input.setAttribute("list", list.id);
      const state = el("select");
      state.append(option("(leave unanswered)", ""), option("There is none", "none"));
      if (dontKnowOffered) state.append(option("I don't know", DONT_KNOW));
      state.append(option("A mechanism, in my own words:", "mechanism"));
      // The state is the answer: blank sends nothing, and the text is read
      // only under "mechanism". Typing a mechanism chooses it.
      const fixed = value === "none" || value === DONT_KNOW;
      state.value = fixed || !value ? value : "mechanism";
      input.value = fixed ? "" : value;
      input.disabled = fixed;
      state.addEventListener("change", () => {
        input.disabled = state.value === "none" || state.value === DONT_KNOW;
        changed();
      });
      input.addEventListener("input", () => {
        if (input.value.trim()) state.value = "mechanism";
        changed();
      });
      read = () => (state.value === "mechanism" ? input.value.trim() : state.value);
      nodes = [state, " ", input, list];
    } else {
      input = el("input");
      input.type = "text";
      input.maxLength = q.max_length;
      input.placeholder = "(leave unanswered)";
      const box = el("input");
      box.type = "checkbox";
      input.value = value;
      box.checked = input.disabled = value === DONT_KNOW;
      box.addEventListener("change", () => {
        input.value = box.checked ? DONT_KNOW : "";
        input.disabled = box.checked;
        changed();
      });
      input.addEventListener("input", changed);
      const dontKnow = el("label");
      dontKnow.append(box, " I don't know");
      nodes = dontKnowOffered ? [input, " ", dontKnow] : [input];
    }
    input.dataset.key = JSON.stringify(q.key);
    return {
      nodes,
      read: () => read() ? { key: q.key, value: read() } : null,
      known: () => Boolean(read()) && read() !== DONT_KNOW,
    };
  };
  // A model writes an identifier the way the prompt hands it over: in
  // backticks. A span that is an element's ID or an evidence reference shows
  // its label, and the identifier stays on hover, so a description reads
  // "Storefront API" rather than `process:storefront-api`. Every other span
  // becomes a `code` element, in the face the element table below shows
  // identifiers in.
  //
  // A pair of backticks around a non-empty span is the entire grammar. No
  // other Markdown is read, and an unpaired backtick stays a backtick: this
  // renders one sentence of prose, and half an emphasis rule reads worse than
  // the raw character does.
  //
  // Still no string that becomes markup — the pieces are text nodes and `code`
  // elements, appended. A quote never comes through here. Its text is the
  // submitter's own words, and a backtick among them is one of those words.
  const CODE_SPAN = /`([^`\n]+)`/g;
  // A labelled identifier as its label, the identifier on hover; any other as code.
  const ref = (id) => Object.hasOwn(NAMES, id)
    ? Object.assign(el("span", "ref", NAMES[id]), { title: id })
    : code(id);
  const prose = (text) => {
    const line = String(text);
    const frag = document.createDocumentFragment();
    let end = 0;
    for (const span of line.matchAll(CODE_SPAN)) {
      frag.append(line.slice(end, span.index), ref(span[1]));
      end = span.index + span[0].length;
    }
    frag.append(line.slice(end));
    return frag;
  };
  // `el`, for the fields a model wrote rather than the ones this page words.
  const proseEl = (tag, cls, text) => {
    const n = el(tag, cls);
    n.append(prose(text));
    return n;
  };
  const lbl = (text) => el("span", "lbl", text);
  const cell = (...kids) => { const n = el("td"); n.append(...kids); return n; };

  // What a scope state means to a reader, and the class that colours it. The
  // wording is the point: `not-raised` must not read as `applicable`, because
  // the only fact is that no lane filed on the unit (#659). Each says what
  // happened, and none of them says "satisfied".
  const SCOPE_STATE = {
    "not-raised": ["No claim filed",
      "The lane ran and raised nothing on this unit. That is not a finding that it is met, and not a ruling that it applies."],
    "not-applicable": ["Does not apply",
      "Ruled out for a system of this shape."],
    "undecidable": ["Never decided",
      "The input never said whether this framework applies at all, so no lane ran."],
    "needs-other-evidence": ["Needs other evidence",
      "The unit applies and this input cannot settle it. Supplying the evidence named below can."],
  };

  // The same for a claim's verdict, in this service's terms rather than the
  // schema's. No verdict here reports a pass, and the wording says so.
  const VERDICT_STATE = {
    "confirmed": ["Gap", "Applies, and the input does not show it satisfied."],
    "needs-info": ["Needs info", "Applies, and the input does not settle it."],
    "rejected": ["Rejected", "The critic ruled this draft out. The reason says which check ended it."],
  };

  // A rejection for `evidence` is the one rejection that rules on the unit a
  // draft names: for a framework that answers in units it means the unit does
  // not apply, and the reason says why. That is an answer about the
  // requirement, so it belongs on the requirement's own row and not in the
  // list of drafts that argued badly. Only a block with unit rows reads it so:
  // for a framework whose claims are an open set, an `evidence` rejection is a
  // draft that failed on its own substance, and it stays dismissed. A
  // rejection with no cause, from a report read back, is a ruling too, as
  // `RuledClaim.rules_on_unit` reads it.
  const answersInUnits = (block) => (UNITS[block.framework] || []).length > 0;
  const rulesOut = (block, c) =>
    answersInUnits(block) && c.verdict.status === "rejected"
    && (c.verdict.rejected_because ?? "evidence") === "evidence";

  // Each kind named in its own words, so they read as different *kinds* of
  // justification rather than formattings of one. The two attribute kinds
  // carry identical fields, and so do the two assertion kinds, so this line is
  // the only place a reader can tell "nobody said" from "somebody said".
  const GROUND_KIND = {
    "quote": "Quoted from the submission",
    "unknown-attribute": "Unstated in the submission",
    "absent-attribute": "Stated absent in the submission",
    "derived-fact": "Derived from the model",
    "absent-element": "Named nowhere in the submission",
    "assertion": "Stated as a fact in the submission",
    "unknown-assertion": "Left open by the submission",
  };
  // Past this, a quote is clamped to three lines behind a toggle. Short quotes
  // are the common case and get no affordance.
  const CLAMP_OVER = 220;

  // Every mark sits in the block whose claims it points at, so these are
  // built per block rather than once for the page. Claim IDs are unique only
  // *within* a block — two frameworks may legitimately compose the same string
  // for unrelated things — so a page-wide map would collide the moment a report
  // carries two frameworks.
  function marksOf(block) {
    // "<claim id>#<grounds index>" for every quote the service looked for in
    // its named source and could not find.
    const unverified = new Map(
      (block.unverified_grounds || []).map(u => [`${u.claim_id}#${u.index}`, u])
    );
    // Quotes the service rewrote to the source's own nearest span. The ground
    // shows the submitter's words; this carries what the agent wrote.
    const repaired = new Map(
      (block.repaired_quotes || []).map(r => [`${r.claim_id}#${r.index}`, r])
    );
    // Every element ID a description cites that the embedded model does not
    // contain, gathered under the claim that cites it.
    // Element IDs a claim named structurally that the model does not contain.
    // Dropped from the claim and listed here, the way a prose mention is.
    const references = new Map();
    (block.unresolved_references || []).forEach(m => {
      if (!references.has(m.claim_id)) references.set(m.claim_id, []);
      references.get(m.claim_id).push(m.element_id);
    });
    const mentions = new Map();
    (block.unresolved_mentions || []).forEach(m => {
      if (!mentions.has(m.claim_id)) mentions.set(m.claim_id, []);
      mentions.get(m.claim_id).push(m.mention);
    });
    // Every evidence reference a claim cited that its job's catalog did not
    // hold. Unlike an unverified quote there is nothing to render in the
    // grounds list — no ground was ever built from these — so the note is the
    // only trace a reader gets that the agent reached for a fact that does not
    // exist.
    const composed = new Map();
    (block.unresolved_evidence || []).forEach(m => {
      if (!composed.has(m.claim_id)) composed.set(m.claim_id, []);
      composed.get(m.claim_id).push(m.reference);
    });
    // Claims offering no countermeasure and carrying no unknown that would
    // excuse offering none. A framework that recommends nothing declares no
    // such mark, so this is empty for it rather than absent — the same shape
    // either way.
    const unmitigated = new Set((block.missing_mitigations || []).map(m => m.claim_id));
    // Claims the service dropped because they named an identifier this
    // framework does not have -- an ASVS requirement number the standard does
    // not publish. Unlike every mark above, these do NOT key to a surviving
    // claim: the claim is gone, which is why they render as a block-level note
    // rather than on a card. The title is the only trace of what was lost.
    const unknown = (block.unknown_claim_identities || []);
    // Claims the service dropped because every ground they cited was lost --
    // a quote the source does not contain, or a reference the catalog does
    // not hold. Dropped the same way, listed the same way, and the reason
    // carries the lost quote or reference so a reader can judge the loss.
    const groundless = (block.dropped_claims || []);
    // How the first critic pass failed to reconcile with its drafts, before
    // the bounded re-ask. These key to a claim that may or may not be in the
    // block -- a dropped draft is ruled on the second pass and a ruling on an
    // invented ID never had a claim -- so they render as a block-level note
    // rather than on a card, grouped by kind.
    const unreconciled = (block.unreconciled_rulings || []);
    return { unverified, repaired, references, mentions, composed, unmitigated, unknown, groundless, unreconciled };
  }

  const SEV = { critical: ["Critical","--sev-critical"], high: ["High","--sev-high"], medium: ["Medium","--sev-medium"], low: ["Low","--sev-low"] };
  // Every severity level, most severe first: the service's own order. SEV
  // holds a label and a colour for each level, and a test holds its keys to
  // the same set.
  const SEV_ORDER = JSON.parse(document.getElementById("severity_order").textContent);
  const VERDICT = { confirmed: ["Confirmed","✓"], "needs-info": ["Needs info","?"], rejected: ["Rejected","✕"] };
  // What the tally says for each kind of change. A kind the table does not
  // hold throws, so it cannot render as a verdict.
  const CHANGE_LINE = {
    new: () => "new",
    unchanged: () => "unchanged",
    changed: (c) => `${VERDICT[c.before][0]} \u2192 ${VERDICT[c.after][0]}`,
    gone: () => "no longer raised",
  };
  const svar = (lvl) => `var(${SEV[lvl][1]})`;

  // The served report can return to its analysis's named report history.
  const reportRoute = location.pathname?.match(/^\/report\/([A-Za-z0-9_-]+)$/);
  if (reportRoute) {
    const historyLink = $("analysis-reports");
    historyLink.href = "/?run=" + encodeURIComponent(reportRoute[1]) + "&view=reports";
    historyLink.hidden = false;
  }
  // header
  $("sysname").textContent = R.input.system_name;
  // The static title names no system and no framework. The report names
  // both, so the tab reads the run rather than the template.
  document.title = R.input.system_name + " — report";
  const fmt = (t) => new Date(t).toISOString().replace("T"," ").replace(".000Z"," UTC");
  $("jobmeta").append(
    `Generated ${fmt(R.job.completed_at)}`
  );
  // The envelope's disclaimer says what the *service* is. Each block carries
  // its own, saying what that framework's claims assert — a different sentence
  // the moment a report carries a framework that rules on requirement
  // applicability rather than on attacks.
  $("disclaimer").textContent = R.disclaimer;

  // "in the model", never "analysed". `elements_analyzed` is the embedded
  // model's own element count and nothing more: no stage records that an
  // element was examined, and the coverage block below counts which elements a
  // draft *cited*, which is a different fact again. No field here supports
  // the statement that every element was examined.
  const frameworks = R.analyses.map(b => b.framework).join(", ");
  $("scope").textContent =
    `${R.elements_analyzed} elements in the model, under ${frameworks}`;

  // One grounds entry. Every string here is model-authored or lifted verbatim
  // out of the submitter's own prose, so it goes in as text and never as markup.
  //
  // Grounds are the neutral half of a claim: every kind is a property of the
  // shared System Model and of the submission, not of any framework's method,
  // so this renders identically in every block.
  function groundEntry(marks, claimId, ground, index) {
    const row = el("div", "ground " + ground.kind);
    row.append(el("div", "kind", GROUND_KIND[ground.kind] || ground.kind));
    const body = el("div", "body");
    if (ground.kind === "quote") {
      const unverified = marks.unverified.get(`${claimId}#${index}`);
      // The quotation marks assert a verbatim span the service found. When it
      // did not, they come off — the claim they make is the one that failed.
      body.textContent = unverified ? ground.text : `\u201c${ground.text}\u201d`;
      row.append(body);
      if (ground.text.length > CLAMP_OVER) {
        body.classList.add("clamped");
        const more = el("button", "more", "Show full quote");
        more.type = "button";
        more.addEventListener("click", () => {
          const clamped = body.classList.toggle("clamped");
          more.textContent = clamped ? "Show full quote" : "Show less";
        });
        row.append(more);
      }
      const cite = el("div", "cite" + (unverified ? " unverified" : ""));
      // An owner's answer is an assertion the service did not check, and it
      // says so rather than reading like the description.
      const own = ground.source_label === PROVENANCE.answers_label;
      cite.append(unverified ? `\u26a0 not found in ${ground.source_label}`
        : own ? "\u2014 your answer; the service did not check it"
        : `\u2014 ${ground.source_label}`);
      row.append(cite);
      const repaired = marks.repaired.get(`${claimId}#${index}`);
      if (repaired) {
        const note = el("div", "cite unverified");
        const moved = (repaired.moved || []).length
          ? ` \u2014 changed a ${repaired.moved.join(" and a ")}`
          : "";
        const scan = repaired.scan_complete === false ? ", scan cut short" : "";
        note.append(`\u270e replaced the agent's wording (similarity ${repaired.similarity}${scan})${moved}: `);
        note.append(document.createTextNode(repaired.written));
        row.append(note);
      }
      return row;
    }
    if (ground.kind === "unknown-attribute" || ground.kind === "absent-attribute") {
      body.append(ref(ground.element_id), " \u2192 ", code(ground.attribute));
      if (ANSWERED_ATTRIBUTES.has(`${ground.element_id}>${ground.attribute}`)) {
        body.append(el("span", "cite", " (your answer; the service did not check it)"));
      }
    } else if (ground.kind === "derived-fact") {
      body.append(ref(ground.flow_id));
    } else if (ground.kind === "absent-element") {
      body.append(code(ground.term));
    } else {
      // An assertion row: its reference is a digest with no short label, so
      // ref shows the reference itself.
      body.append(ref(ground.assertion));
    }
    row.append(body);
    return row;
  }

  function groundsBlock(marks, t) {
    const block = el("div", "grounds");
    block.append(lbl("Grounds \u2014 why this was raised"));
    t.grounds.forEach((g, i) => block.append(groundEntry(marks, t.id, g, i)));
    return block;
  }

  // One claim card, built from the neutral shape and *widened* by whatever the
  // framework's own record carries.
  //
  // This is the fallback the report's design promises: a consumer that does not
  // know a framework reads an ID, the (framework, version) pair, a title, a
  // description, the elements and the grounds — and this page is that consumer
  // for every framework but the ones whose extras it happens to recognise. So
  // each extra is rendered behind a presence test rather than assumed: a claim
  // with no severity gets no severity chip and no coloured border, not a card
  // that throws reading `undefined.level`. Adding a framework needs no edit
  // here; its claims render, correctly, in the neutral shape.
  function claimCard(marks, t, rejected) {
    const card = el("div", "card" + (rejected ? " rejected" : ""));
    const head = el("div","card-head");
    head.append(proseEl("h3", null, t.title), el("span","tid", t.id));
    card.append(head);
    // A final report's owner changed an answer this finding rests on, after
    // the analysis ran; the analysis did not run again (ADR 0054).
    const moved = CHANGED.get(`${t.framework}/${t.id}`);
    if (moved && moved.change === "new") {
      card.append(el("div", "meta", "New since the earlier report."));
    } else if (moved && moved.change === "changed") {
      card.append(el("div", "meta", `Was ${VERDICT[moved.before][0]} in the earlier report.`));
    }
    if (CORRECTED.has(`${t.framework}/${t.id}`)) {
      card.append(el("div", "unknown",
        "Corrected after this report: an answer this finding rests on was " +
        "changed after the analysis ran. The analysis did not run again."));
    }
    // The lane, where the framework stamps one.
    const lane = laneOf(t.framework, t);
    if (lane) card.append(el("div","cat", lane));

    const badges = el("div","badges");
    if (t.severity) {
      card.style.borderLeftColor = svar(t.severity.level);
      const sev = el("span","chip");
      const sevSwatch = el("span","swatch");
      sevSwatch.style.background = svar(t.severity.level);
      sev.append(sevSwatch, SEV[t.severity.level][0]);
      // A property assignment, not an attribute: there is no markup context to
      // escape out of, which is why no quote handling is needed anywhere here.
      sev.title = `likelihood ${t.severity.likelihood} \u00d7 impact ${t.severity.impact} \u2192 ${t.severity.level}`;
      badges.append(sev);
    }
    const [vlabel, vglyph] = VERDICT[t.verdict.status];
    badges.append(el("span","chip verdict", `${vglyph} ${vlabel}`));
    if (t.confidence) {
      badges.append(el("span","chip confidence", `confidence: ${t.confidence}`));
    }
    card.append(badges);

    card.append(proseEl("div","desc", t.description));

    // The default view answers what a reader triages on (#561): why it
    // matters, what is missing, what it touches and what to do next. The
    // evidence behind it, and every warning about a citation that did not
    // resolve, sits under one toggle further down, so nothing is removed.
    if (t.severity) {
      const why = el("div","field");
      why.append(lbl("Why it matters"), el("br"), prose(t.severity.justification));
      card.append(why);
    }

    // The facts a needs-info finding waits on, each by what it asks and why
    // it is still open. The critic's own sentence restates them, so it moves
    // under the toggle rather than repeating above it.
    const conditional = t.verdict.status === "needs-info" && t.verdict.related_unknowns.length;
    const waits = conditional ? (CONDITIONS[`${t.framework}/${t.id}`] || []) : [];
    if (conditional) {
      const u = el("div","unknown");
      u.append(el("b", null, "Needs info."), " Missing information:");
      if (waits.length) {
        const list = el("ul");
        waits.forEach(w => list.append(el("li", null, `${w.label} — ${WHY_OPEN[w.status] || w.status}`)));
        u.append(list, el("div", null,
          "Until these are confirmed, this finding is neither confirmed nor cleared."));
      } else {
        u.append(" ");
        t.verdict.related_unknowns.forEach((r, i) => {
          if (i) u.append(", ");
          u.append(ref(r.element_id), " → ", code(r.attribute));
        });
      }
      card.append(u);
    }

    const refs = el("div","field refs");
    refs.append(lbl("Affected"), el("br"));
    t.affected_element_ids.forEach(r => refs.append(ref(r)));
    card.append(refs);

    if (t.verdict.status === "rejected") {
      const why = el("div","field");
      why.append(lbl("Reason dismissed"), el("br"), prose(t.verdict.reason));
      card.append(why);
    }

    // What to do next: settle the open facts, then the mitigations, or the
    // statement that none was proposed.
    const next = el("div","field");
    next.append(el("div","lbl","Next step"));
    if (waits.length) {
      next.append(el("div", null, FINAL
        ? "Confirm the missing information with the people who run the component, then correct an answer below or submit the description again."
        : "Confirm the missing information with the people who run the component, and answer the questions in the follow-up below."));
    }
    if (t.mitigations && t.mitigations.length) {
      const list = el("ul","mits"); t.mitigations.forEach(m => list.append(proseEl("li", null, m.summary)));
      next.append(list);
    } else if (marks.unmitigated.has(t.id)) {
      next.append(el("div", "caveat",
        "⚠ No mitigation proposed, and this finding does not rest on an unknown that would explain why."));
    }
    if (next.children.length > 1) card.append(next);

    // Everything behind the default view: the critic's reason, the grounds
    // with who justified what, and each citation the service dropped. A
    // `details` element, so the browser does the toggling, keyboard included.
    const prov = el("details","prov");
    prov.append(el("summary", null, "Show technical provenance"));
    if (conditional && t.verdict.reason) {
      const reason = el("div","field");
      reason.append(lbl("The critic's reason"), el("br"), prose(t.verdict.reason));
      prov.append(reason);
    }
    prov.append(groundsBlock(marks, t));
    const references = marks.references.get(t.id);
    if (references && references.length) {
      const note = el("div", "caveat");
      note.append("⚠ Named as affected but not in the system model, so dropped from this claim: ");
      references.forEach((m, i) => { if (i) note.append(", "); note.append(code(m)); });
      prov.append(note);
    }
    const mentions = marks.mentions.get(t.id);
    if (mentions && mentions.length) {
      const note = el("div", "caveat");
      note.append("⚠ Cited in the description but not in the system model: ");
      mentions.forEach((m, i) => { if (i) note.append(", "); note.append(code(m)); });
      prov.append(note);
    }
    // Worded as a citation failure rather than a doubt about the finding: the
    // grounds above are the ones that did resolve, and they are why this
    // claim is still here.
    const composed = marks.composed.get(t.id);
    if (composed && composed.length) {
      const note = el("div", "caveat");
      note.append("⚠ Cited evidence not in this job's catalog, and dropped: ");
      composed.forEach((r, i) => { if (i) note.append(", "); note.append(code(r)); });
      prov.append(note);
    }
    card.append(prov);
    return card;
  }

  // One framework's whole section: its heading, its disclaimer, its own counts,
  // and its two claim arrays. Nothing here reaches back into the envelope
  // except for the element count, which is a fact about the one shared model
  // and so is stated once at the top rather than N times.
  // One row per unit the framework answers for: what the standard asks, what
  // this run concluded, and why. The grouped counts above answer "how much";
  // this answers "which, and on what grounds", which no count can (#659).
  //
  // Collapsed by default through `details`, because a level-3 ASVS block
  // carries 345 of these and a reader opens the two they care about. No
  // scripted toggling: the element does it, keyboard included.
  function unitTable(block) {
    const rows = UNITS[block.framework] || [];
    if (!rows.length) return null;
    const byUnit = {};
    block.scope.forEach(e => { byUnit[e.unit] = e; });
    // Whether the unit has a subject here, apart from what the run did with it.
    const applies = {};
    (block.applicability || []).forEach(e => { applies[e.unit] = e; });
    const byId = {};
    [...block.claims, ...block.rejected_claims].forEach(c => { byId[c.id] = c; });

    const wrap = el("div", "units");
    wrap.append(el("h3", null, `Every requirement this run answered for (${rows.length})`));
    rows.forEach(row => {
      const claim = row.claim_id ? byId[row.claim_id] : null;
      const entry = byUnit[row.unit];
      const [label, meaning] = claim
        ? (rulesOut(block, claim)
            ? SCOPE_STATE["not-applicable"]
            : (VERDICT_STATE[claim.verdict.status] || ["Ruled", ""]))
        : (SCOPE_STATE[entry ? entry.state : "not-raised"] || ["Listed", ""]);

      const box = el("details", "unit");
      const head = el("summary");
      head.append(code(row.unit), el("span", "state", label));
      // The requirement's own words on the closed row, so a reader scanning
      // the list can tell which identifiers matter without opening any.
      if (row.text) head.append(el("span", "gist", row.text));
      box.append(head);

      const body = el("div", "unit-body");
      if (row.text) {
        body.append(el("div", "lbl", `What ${block.framework} ${block.framework_version} asks`));
        body.append(el("p", "req", row.text));
      }
      if (applies[row.unit]) {
        body.append(el("div", "lbl", "Does it apply here?"));
        // Plain text: the reason quotes the submitter, so no backtick is read.
        body.append(el("p", null, applies[row.unit].reason));
      }
      body.append(el("div", "lbl", "This run"));
      body.append(el("p", null, meaning));
      if (claim) {
        body.append(proseEl("p", "t", claim.title));
        body.append(proseEl("p", null, claim.description));
        if (claim.verdict.reason) {
          body.append(el("div", "lbl", "Review"));
          body.append(proseEl("p", null, claim.verdict.reason));
        }
        (claim.verdict.related_unknowns || []).forEach(u => {
          body.append(el("div", "lbl", "Still to answer"));
          body.append(proseEl("p", null,
            u.subject || `${u.attribute} on ${u.element_id}`));
        });
      } else if (entry) {
        if (entry.reason) body.append(proseEl("p", null, entry.reason));
        // A deferral is a work item, not a count: name the artifact that
        // settles it where the payload names one.
        if (entry.needs) {
          body.append(el("div", "lbl", "What would settle it"));
          body.append(el("p", null, `Evidence of kind: ${entry.needs}`));
        }
      }
      box.append(body);
      wrap.append(box);
    });
    return wrap;
  }

  function renderBlock(block) {
    const marks = marksOf(block);
    const section = el("section", "analysis");
    section.append(el("h2", null, `${block.framework} \u00b7 v${block.framework_version}`));
    section.append(el("div", "disclaimer-block", block.disclaimer));

    const tiles = el("div", "tiles");
    // The deferred count rides beside the claim counts rather than only inside
    // the scope summary: a requirement this job cannot settle is work the
    // submitter can act on, and a total that omits it reads as less to do
    // than there is (#659).
    const deferredCount =
      block.scope.filter(e => e.state === "needs-other-evidence").length;
    // Split out of the rejected count on the page: a requirement ruled out
    // is an answer, and a draft dismissed for arguing badly is not.
    const ruledOutClaims = block.rejected_claims.filter(c => rulesOut(block, c));
    const dismissed = block.rejected_claims.filter(c => !rulesOut(block, c));
    [
      [block.summary.claim_count, "Actionable claims"],
      [block.summary.needs_info_count, "Needs info"],
      ...(answersInUnits(block) ? [[ruledOutClaims.length, "Does not apply"]] : []),
      [dismissed.length, "Rejected"],
      [deferredCount, "Needs other evidence"],
    ].forEach(([n, k]) => {
      const t = el("div","tile");
      t.append(el("div","n",String(n)), el("div","k",k));
      tiles.append(t);
    });
    section.append(tiles);

    // What this framework considered and raised nothing about, grouped. The
    // entries are not listed one by one: a framework answering in its own units
    // lists every one of them — ASVS lists 70 requirements at level 1 and 345 at
    // level 3 — and a page printing all of them would bury the findings under
    // the things that were fine. The payload still carries every unit, which is
    // the rule; this is how a person reads it.
    //
    // Grouped by state, and the deferred group again by the evidence that would
    // settle it, because those two say different things. "Ruled out" is a
    // finished answer. "Needs source code" is a live one a reader can act on by
    // supplying a different kind of input, and it is worth naming what kind.
    if (block.scope.length) {
      const byState = {};
      block.scope.forEach(e => { (byState[e.state] = byState[e.state] || []).push(e); });
      const wrap = el("div", "meta");
      wrap.append(el("div", null,
        `${block.scope.length} units with no claim raised`));

      const ruledOut = (byState["not-applicable"] || []).length;
      if (ruledOut) {
        wrap.append(el("div", null, `\u00a0\u00a0${ruledOut} ruled out — does not apply`));
      }
      if (ruledOutClaims.length) {
        wrap.append(el("div", null,
          `\u00a0\u00a0${ruledOutClaims.length} ruled out by review — does not apply; each on its own row below`));
      }
      const undecided = (byState["undecidable"] || []).length;
      if (undecided) {
        wrap.append(el("div", null,
          `\u00a0\u00a0${undecided} undecidable — the input never says whether this framework applies`));
      }
      const notRaised = (byState["not-raised"] || []).length;
      if (notRaised) {
        wrap.append(el("div", null,
          `\u00a0\u00a0${notRaised} not raised — no lane filed a claim; not a verdict that they apply`));
      }
      const deferred = byState["needs-other-evidence"] || [];
      if (deferred.length) {
        const byNeed = {};
        deferred.forEach(e => { byNeed[e.needs] = (byNeed[e.needs] || 0) + 1; });
        wrap.append(el("div", null,
          `\u00a0\u00a0${deferred.length} could not be evaluated from this input:`));
        Object.keys(byNeed).sort().forEach(need => {
          wrap.append(el("div", null, `\u00a0\u00a0\u00a0\u00a0${byNeed[need]} need ${need}`));
        });
      }
      section.append(wrap);
    }

    const units = unitTable(block);
    if (units) section.append(units);

    // What the service dropped for naming something this framework does not
    // have. There is no card to hang these on, so they are listed here: a
    // reader who is told "23 requirements considered" deserves to know that a
    // 24th ruling was discarded for citing a requirement that does not exist.
    if (marks.unknown.length) {
      const note = el("div", "dropped");
      note.append(el("b", null,
        `${marks.unknown.length} ruling(s) dropped for naming an unpublished identifier`));
      const list = el("ul");
      marks.unknown.forEach(m => {
        const item = el("li");
        item.append(code(m.claim_id), " \u2014 ", prose(m.title));
        list.append(item);
      });
      note.append(list);
      section.append(note);
    }
    if (marks.groundless.length) {
      const note = el("div", "dropped");
      note.append(el("b", null,
        `${marks.groundless.length} finding(s) dropped for a fault in one entry`));
      const list = el("ul");
      marks.groundless.forEach(m => {
        const item = el("li");
        item.append(code(m.claim_id), " \u2014 ", prose(m.title),
          " (", prose(m.reason), ")");
        list.append(item);
      });
      note.append(list);
      section.append(note);
    }
    // A repaired run and a clean one read alike without this. The count per
    // kind is what a reader acts on -- one dropped draft is a slip, and forty
    // needs-info verdicts hung on attributes the model does not have is a
    // prompt that needs work -- so the kinds are grouped rather than listed
    // one sentence at a time.
    if (marks.unreconciled.length) {
      const note = el("div", "dropped");
      note.append(el("b", null,
        `${marks.unreconciled.length} problem(s) the review's first pass left for its re-ask`));
      const byKind = new Map();
      marks.unreconciled.forEach(m => {
        if (!byKind.has(m.kind)) byKind.set(m.kind, []);
        byKind.get(m.kind).push(m.claim_id);
      });
      const list = el("ul");
      byKind.forEach((ids, kind) => {
        const item = el("li");
        item.append(code(kind), ` \u2014 ${ids.length}: `, prose(ids.join(", ")));
        list.append(item);
      });
      note.append(list);
      section.append(note);
    }

    // The severity mix, only for a framework that grades harm. One that does
    // not declares no `by_severity`, and an empty bar would read as "nothing
    // was severe" rather than "severity is not this method's question".
    const by = block.summary.by_severity;
    if (by) {
      const present = SEV_ORDER.filter(l => by[l]);
      if (present.length) {
        const wrap = el("div", "mixwrap");
        const mix = el("div", "mix");
        const legend = el("div", "mixlegend");
        present.forEach(l => {
          const s = el("span");
          s.style.flex = by[l];
          s.style.background = svar(l);
          mix.append(s);
        });
        // Each band says how many of its claims are confirmed. The rubric
        // rates a claim resting on an unstated control as if the control were
        // absent, so a conditional claim reaches the top band on a fact nobody
        // stated — and "3 critical" alone reads as three settled findings.
        const confirmed = block.summary.by_severity_confirmed || {};
        present.forEach(l => {
          const item = el("div");
          const swatch = el("span", "swatch");
          swatch.style.background = svar(l);
          const open = by[l] - (confirmed[l] || 0);
          const split = open ? ` (${confirmed[l] || 0} confirmed, ${open} to answer)` : "";
          item.append(swatch, `${SEV[l][0]} \u00b7 ${by[l]}${split}`);
          legend.append(item);
        });
        wrap.append(mix, legend);
        section.append(wrap);
      }
    }

    // Severity order where the framework grades, and the block's own order
    // otherwise — which is the package's declared lane order, and is the only
    // ranking a framework that grades nothing has.
    // A conditional finding is shown once, under the open fact that would
    // settle the most findings on its own, rather than in the list below: a
    // report of many conditional findings asks the same few questions many
    // times, and grouped they read as the questions they are.
    const facts = OPEN_FACTS[block.framework] || [];
    const grouped = new Set(facts.flatMap(f => f.placed));
    const byClaimId = {};
    block.claims.forEach(c => { byClaimId[c.id] = c; });
    const claims = block.claims.filter(c => !grouped.has(c.id));
    if (claims.every(c => c.severity)) {
      claims.sort((a,b) => SEV_ORDER.indexOf(a.severity.level) - SEV_ORDER.indexOf(b.severity.level));
    }
    section.append(el("h3", null, "Claims"));
    if (claims.length) {
      claims.forEach(t => section.append(claimCard(marks, t, false)));
    } else {
      // Said rather than left blank. A framework that examined the system and
      // raised nothing is a result; an empty heading reads as a rendering fault.
      section.append(el("div", "meta", grouped.size
        ? "Every claim here is conditional; they are grouped below by what would settle them."
        : "No claims were raised under this framework."));
    }
    if (grouped.size) {
      section.append(el("h3", null, "Conditional \u2014 grouped by the open fact that would settle them"));
      section.append(el("div", "meta",
        `${grouped.size} finding(s) wait on ${facts.length} open fact(s). ` +
        "Each finding is shown once, under the fact that alone covers the most findings."));
      facts.filter(f => f.placed.length).forEach(f => {
        const box = el("details", "openfact");
        const head = el("summary");
        head.append(el("b", null, f.label),
          ` \u2014 ${f.cited_by.length} finding(s) wait on this; an answer alone covers ${f.settles.length}`);
        box.append(head);
        f.placed.forEach(id => box.append(claimCard(marks, byClaimId[id], false)));
        section.append(box);
      });
    }
    // Only where there are any. Every block carries this heading otherwise, and
    // on a report with two frameworks that is two empty sections a reader has
    // to scroll past to reach the model.
    if (dismissed.length) {
      section.append(el("h3", null, "Rejected \u2014 considered and dismissed"));
      dismissed.forEach(t => section.append(claimCard(marks, t, true)));
    }
    $("analyses").append(section);
  }

  // What the answers did, before any finding: how the follow-up's findings
  // moved since the report the answers came from. A finding the follow-up no
  // longer raises has no card, so it is listed here by title.
  if (CHANGES.length) {
    const tally = new Map();
    CHANGES.forEach(c => {
      const line = CHANGE_LINE[c.change](c);
      tally.set(line, (tally.get(line) || 0) + 1);
    });
    const box = el("div", "meta");
    box.append(`Since the report your answers came from, ${CHANGES.length} finding(s): ` +
      [...tally].map(([line, n]) => `${n} ${line}`).join("; ") + ".");
    const gone = CHANGES.filter(c => c.change === "gone");
    if (gone.length) {
      const list = el("ul");
      gone.forEach(c => list.append(proseEl("li", null, c.title)));
      box.append(list);
    }
    $("analyses").append(box);
  }
  // In the job's own selection order, which the envelope has already checked
  // against `job.frameworks`.
  // What stays open, before any finding: a conditional finding is neither
  // confirmed nor cleared, and the counts say why each is still open.
  const conditional = Object.values(CONDITIONS);
  if (conditional.length) {
    const tally = Object.fromEntries(Object.keys(WHY_OPEN).map(status => [status, 0]));
    conditional.forEach(waits => {
      new Set(waits.map(w => w.status)).forEach(status => { tally[status] += 1; });
    });
    const parts = Object.entries(OPEN_SUMMARY)
      .filter(([status]) => tally[status])
      .map(([status, line]) => line(tally[status]));
    $("analyses").append(el("div", "meta",
      `What remains open: ${conditional.length} finding(s) are conditional. Each is ` +
      "neither confirmed nor cleared until the facts it waits on are confirmed. " +
      `Of them, ${parts.join("; ")}.`));
  }
  R.analyses.forEach(renderBlock);

  // What the report asks: which element each principal is (link questions),
  // and the open facts its conditional findings rest on (fact questions).
  // Answers go to /answer/{run}, which starts a run from this report's model
  // and catalog; code writes every answer, so no model reads one. Every label
  // is untrusted and lands as text.
  if (FINAL) {
    $("links").append(el("div", "meta",
      "This report is final. Its one follow-up has run, so it asks no more " +
      "questions. The facts still open are listed under the conditional findings."));
  }
  if (RESUMED_BY) {
    const note = el("div", "meta",
      "Your answers to this report started its follow-up, so this report asks " +
      "no more questions. When the follow-up finishes, its report is ");
    const link = el("a", null, "here");
    link.href = `/report/${encodeURIComponent(RESUMED_BY)}`;
    note.append(link, ".");
    $("links").append(note);
  }
  const earlierCount = EARLIER_FACTS.length + EARLIER_LINKS.length;
  if (LINK_QUESTIONS.length || FACT_QUESTIONS.length || earlierCount) {
    // The follow-up is optional and starts closed: the report is complete
    // without it, and answering runs the analysis once more.
    const box = el("details", "followup");
    const waiting = new Set(FACT_QUESTIONS.flatMap(q => q.findings)).size;
    box.append(el("summary", null,
      `Optional follow-up: ${FACT_QUESTIONS.length + LINK_QUESTIONS.length} question(s)` +
      (waiting ? ` about facts that ${waiting} conditional finding(s) wait on` : "") +
      (earlierCount ? `, and ${earlierCount} earlier answer(s) you can change` : "") +
      ". Answer what you can, and the analysis runs once more, which takes a " +
      "few minutes. After that, the report is final."));
    $("links").append(box);
    const linkSelects = [];
    // One reader per fact question: `read` is its answer as the service takes
    // it, or null; `known` is whether that answer says more than "I don't know".
    const factAnswers = [];
    // Why a question is here: what became of it before the analysis
    // (fact_status), new from the analysis, or raised by the reviewer. The
    // list puts the most important findings' questions first, so the
    // reviewer's questions sit among the others and each one says so.
    const BEFORE = {
      skipped: " (you skipped this before the analysis)",
      unanswered: " (shown before the analysis and left blank)",
      partial: " (you answered part of this before the analysis)",
    };
    const why = q => BEFORE[q.history]
      ? BEFORE[q.history]
      : q.basis === "evidence"
        ? " (new from the analysis)"
        : " (raised by the reviewer; it can change when the analysis runs again)";
    // The findings that wait on a question, by title, so the owner sees what
    // an answer can settle.
    const titles = {};
    (R.analyses || []).forEach(b => b.claims.forEach(c => {
      titles[`${b.framework}/${c.id}`] = c.title;
    }));
    const waitingOn = q => {
      if (!q.findings.length) return "";
      const named = q.findings.slice(0, 3).map(f => titles[f] || f);
      const rest = q.findings.length - named.length;
      return el("div", "meta",
        `Waiting on it: ${named.join("; ")}` + (rest ? `; and ${rest} more` : "") +
        (q.band ? `. The most important is ${q.band}.` : ""));
    };

    if (LINK_QUESTIONS.length) {
      box.append(el("h2", null, "Which element is each of these?"));
      box.append(el("div", "meta",
        "The sources state facts about these principals but never say which element " +
        "of the model each one is, so no rule can place the facts."));
      LINK_QUESTIONS.forEach(q => {
        const row = el("p");
        const select = el("select");
        select.dataset.principal = q.principal;
        select.append(option("(leave unanswered)", ""));
        linkOptions(select, q.options);
        row.append(el("b", null, q.principal),
          ` \u2014 an answer places ${q.rows} stated fact(s) `, select);
        box.append(row);
        linkSelects.push(select);
      });
    }

    if (FACT_QUESTIONS.length) {
      box.append(el("h2", null, "What would settle the conditional findings?"));
      box.append(el("div", "meta",
        "Each question is a fact the conditional findings wait on, the most useful " +
        "first. Beside each is how many findings have every question answered once it " +
        "and every question above it is answered. A question with parts asks only " +
        "the parts listed, which may not be every fact a finding needs. Answer as far down as you like; " +
        "the line under the questions counts the findings your answers cover. The " +
        "analysis decides again whether each answer settles its finding. Choose " +
        "\"I don't know\" where nobody knows: the fact stays open."));
      if (FALLBACK.typed + FALLBACK.free_text) {
        box.append(el("div", "meta",
          `The reviewer asked ${FALLBACK.typed} open fact(s) from the fixed list of ` +
          `questions and ${FALLBACK.free_text} in its own words.`));
      }
      // A question no conditional finding waits on cannot move this report:
      // it follows the others, in a closed section of its own.
      const material = FACT_QUESTIONS.filter(q => q.findings.length);
      const aside = FACT_QUESTIONS.filter(q => !q.findings.length);
      const ordered = [...material, ...aside];
      // The first few in full; the rest one click away rather than a wall.
      const SHOWN = 10;
      const more = el("details", "openfact");
      more.append(el("summary", null, `More questions (${material.length - SHOWN})`));
      const unwaited = el("details", "openfact");
      unwaited.append(el("summary", null,
        `Questions no conditional finding waits on (${aside.length})`));
      unwaited.append(el("div", "meta",
        "Only a confirmed finding, a rejected draft, or a finding that waits on a " +
        "fact answered \"I don't know\" cites these facts. An answer cannot move a " +
        "conditional finding in this report; the next analysis reads it."));
      // The facts only the critic named come after the ones the findings' own
      // evidence rests on, and they can change when the analysis runs again,
      // so they are headed apart.
      // A finding is covered once every question that names it has an answer,
      // in any order, so the count follows the answers given, not the rank.
      const waitsOn = new Map();
      ordered.forEach((q, index) => q.findings.forEach(finding => {
        if (!waitsOn.has(finding)) waitsOn.set(finding, []);
        waitsOn.get(finding).push(index);
      }));
      // Each finding's lane, read from the field its package stamps, so the
      // scope line can say how many lanes the answers reach.
      const laneOfFinding = new Map(R.analyses.flatMap(block => block.claims.map(c =>
        [`${block.framework}/${c.id}`, laneOf(block.framework, c) || block.framework])));
      const tally = el("div", "meta");
      const scope = el("div", "meta");
      function recount() {
        const known = index => factAnswers[index].known();
        const covered = [...waitsOn.values()].filter(asked => asked.every(known)).length;
        tally.textContent =
          `Your answers cover every question for ${covered} of the ${waitsOn.size} findings ` +
          "that wait on one. The analysis decides again whether they are settled.";
        // What pressing the button does, before it is pressed (#561): how far
        // the answers reach, and what runs. A follow-up runs the whole
        // analysis once (ADR 0044), and code records every answer (ADR 0046).
        const given = factAnswers.filter(answer => answer.known()).length;
        const reached = [...waitsOn].filter(([, asked]) => asked.some(known)).map(([f]) => f);
        const lanes = new Set(reached.map(f => laneOfFinding.get(f)).filter(Boolean));
        scope.textContent = given
          ? `${given} answer(s) reach ${reached.length} finding(s) in ${lanes.size} lane(s). ` +
            "Code records each answer, and no model interprets it before the analysis. " +
            "The follow-up then runs the whole analysis once, and its report is final."
          : "No answer given yet.";
      }
      ordered.forEach((q, index) => {
        const into = !q.findings.length ? unwaited : index < SHOWN ? box : more;
        const row = el("p");
        const editor = editorFor(q, RETAINED.get(JSON.stringify(q.key)) || null, recount, false);
        factAnswers.push({ read: editor.read, known: editor.known });
        const lead = [el("b", null, q.label), why(q),
          ` \u2014 ${q.cited_by} finding(s) wait on it; answering down to here covers ${q.covered_so_far}`];
        if (q.form === "facets") row.append(...lead, waitingOn(q), ...editor.nodes);
        else row.append(...lead, " ", ...editor.nodes, waitingOn(q));
        into.append(row);
      });
      if (material.length > SHOWN) box.append(more);
      if (aside.length) box.append(unwaited);
      box.append(tally, scope);
      recount();
    }

    // The answers this report read. A row sends a new answer only once its
    // "Change" button opens it, so the follow-up reads only what changed.
    const earlierFacts = [];
    if (earlierCount) {
      box.append(el("h2", null, "Earlier answers"), el("div", "meta",
        "This report read these answers. If one is wrong, or you now know what was " +
        "\"I don't know\", change it, and the follow-up reads the new answer. A " +
        "known answer, or a known part of one, cannot change back to " +
        "\"I don't know\" here."));
      const changer = (row, shown, open) => {
        const change = el("button", null, "Change");
        change.type = "button";
        change.addEventListener("click", () => {
          change.hidden = true;
          shown.replaceChildren(" \u2014 ", ...open());
        });
        row.append(shown, " ", change);
      };
      EARLIER_LINKS.forEach(a => {
        const row = el("p");
        const element = a.answer.element;
        row.append(el("b", null, a.principal));
        changer(row, el("span", null, ` \u2014 ${element === "none"
          ? "none of these" : NAMES[element] || element}`), () => {
          const select = el("select");
          select.dataset.principal = a.principal;
          linkOptions(select, a.options);
          select.value = element;
          linkSelects.push(select);
          return [select];
        });
        box.append(row);
      });
      EARLIER_FACTS.forEach(a => {
        const row = el("p");
        row.append(el("b", null, a.label));
        changer(row, el("span", null, ` \u2014 ${said(a.answer)}`), () => {
          const editor = editorFor(a, a.answer, () => {}, false);
          earlierFacts.push(editor.read);
          return editor.nodes;
        });
        box.append(row);
      });
    }

    const again = el("button", null, "Run the follow-up with these answers");
    const note = el("div", "meta");
    again.addEventListener("click", async () => {
      const links = linkSelects
        .filter(s => s.value)
        .map(s => ({ principal: s.dataset.principal, element: s.value }));
      const facts = [...factAnswers.map(answer => answer.read()),
        ...earlierFacts.map(read => read())].filter(Boolean);
      again.disabled = true;
      // The run id is this page's own path: /report/{run}.
      const run = location.pathname.split("/").pop();
      const resumed = await fetch("/answer/" + encodeURIComponent(run), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ links, facts }),
      });
      const body = await resumed.json();
      if (!resumed.ok) {
        note.textContent = body.message;
        again.disabled = false;
        return;
      }
      // The form page follows a run's progress; this page shows one report.
      location.href = "/?follow=" + encodeURIComponent(body.run);
    });
    const actions = el("p");
    actions.append(again);
    box.append(actions, note);
  }

  // A final report's answers can be corrected (ADR 0054): a changed value, or
  // "I don't know" where an answer was a guess. A correction is kept beside
  // the report and marks the findings that rest on it; nothing runs again.
  if (FINAL && (CORRECTIONS.answers || []).length) {
    const box = el("details", "followup");
    box.append(el("summary", null, `Correct an answer (${CORRECTIONS.answers.length})`),
      el("div", "meta",
        "If an answer was wrong or a guess, change it, or choose \"I don't know\". " +
        "The correction is kept beside this report and marks the findings that rest " +
        "on it. The analysis does not run again."));
    const edits = [];
    CORRECTIONS.answers.forEach(a => {
      const row = el("p");
      const shown = el("span", null,
        ` \u2014 ${said(a.answer)}` + (a.corrected ? " (corrected after this report)" : ""));
      const change = el("button", null, "Change");
      change.type = "button";
      change.addEventListener("click", () => {
        change.hidden = true;
        const editor = editorFor(a, a.answer, () => {});
        shown.replaceChildren(" \u2014 ", ...editor.nodes);
        edits.push(editor.read);
      });
      row.append(el("b", null, a.label), shown, " ", change);
      box.append(row);
    });
    const save = el("button", null, "Save the corrections");
    const note = el("div", "meta");
    save.addEventListener("click", async () => {
      const facts = edits.map(read => read()).filter(Boolean);
      // The run id is this page's own path: /report/{run}.
      const run = location.pathname.split("/").pop();
      const saved = await fetch("/correct/" + encodeURIComponent(run), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ facts }),
      });
      const body = await saved.json();
      if (!saved.ok) {
        note.textContent = body.message;
        return;
      }
      location.href = location.pathname;
    });
    const actions = el("p");
    actions.append(save);
    box.append(actions, note);
    $("links").append(box);
  }

  // system model table. Each entry returns the cell's children rather than a
  // string of markup — `technology`, `protocol`, `authentication` and
  // `data_classification` are free-text copied out of submitted prose, and this
  // column is where the unescaped-innerHTML bug lived.
  const attrs = {
    "external-entity": e => [
      `kind: ${e.kind}` + (e.assets.length ? ` · assets: ${e.assets.join(", ")}` : ""),
    ],
    "process": e => [`${e.technology} · exposure: ${e.exposure} · presents: ${e.interface_kind}`],
    "data-store": e => [
      `${e.technology} · ${e.data_classification} · at rest: `, mark(e.encryption_at_rest),
    ],
    "data-flow": e => [
      `${e.protocol} · auth: ${e.authentication} · in transit: `, mark(e.encryption_in_transit),
    ],
  };
  function mark(v){ return v === "unknown" ? el("span", "unk", "unknown") : v; }
  const rows = [
    ...R.system_model.external_entities.map(e => ["external-entity", e]),
    ...R.system_model.processes.map(e => ["process", e]),
    ...R.system_model.data_stores.map(e => ["data-store", e]),
    ...R.system_model.data_flows.map(e => ["data-flow", e]),
  ];
  const tb = $("elements").querySelector("tbody");
  rows.forEach(([type, e]) => {
    const zone = e.trust_zone || (e.source + " → " + e.destination);
    const tr = el("tr");
    tr.append(
      cell(type), cell(code(e.id)), cell(e.name), cell(code(zone)), cell(...attrs[type](e))
    );
    tb.append(tr);
  });
  R.boundary_crossings.forEach(c => {
    const d = el("div","crossing");
    d.append(
      "Boundary crossing: ", ref(c.flow_id), " — ",
      ref(c.source_zone), " → ", ref(c.destination_zone)
    );
    // A crossing one of whose zones the service inferred says so here. The
    // assumption itself is listed below with its basis; without this line a
    // reader has to join the two lists to find out that the crossing rests on
    // a placement nobody stated.
    c.assumed_endpoints.forEach(id => d.append(" — assumed zone for ", ref(id)));
    $("crossings").append(d);
  });
  R.system_model.assumptions.forEach(a => {
    const d = el("div","assume");
    d.append(`Assumption: ${a.assumption} (`, ref(a.element_id), `) — ${a.basis}`);
    $("assumptions").append(d);
  });

  // pipeline
  R.nodes.forEach(n => {
    const row = el("div","node");
    row.append(
      el("b", null, n.node), " ",
      el("span", "m", n.model ? n.model : "code"), ` · ${n.duration_ms} ms`
    );
    if (n.execution_fingerprint) {
      const fp = el("code", "m", `fp ${n.execution_fingerprint.slice(0, 12)}…`);
      fp.title = `execution-identity fingerprint: sha256 of the requested route, the served build, the resolved tier sampling, the instruction digest and the build versions\n${n.execution_fingerprint}`;
      row.append(" · ", fp);
    }
    $("nodes").append(row);
  });
  // per-tier resolved sampling (provenance clear block)
  Object.entries(R.sampling || {}).forEach(([tier, params]) => {
    const set = Object.entries(params).filter(([, v]) => v !== null)
      .map(([k, v]) => `${k} = ${v}`).join(", ");
    const row = el("div","node m");
    row.append(
      el("b", null, `tier ${tier}`), ` sampling · ${set} `,
      el("span", "m", "(others: model default)")
    );
    $("nodes").append(row);
  });

  // theme toggle
  $("themebtn").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme");
    const dark = cur ? cur === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.setAttribute("data-theme", dark ? "light" : "dark");
  });

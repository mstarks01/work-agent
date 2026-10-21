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
  // The run whose answers started this one, or null.
  const PARENT_RUN = JSON.parse(document.getElementById("parent_run").textContent);
  // The missing controls two or more confirmed findings rest on, grouped and
  // ordered server-side. Each finding is placed under one control.
  const FIXES = JSON.parse(document.getElementById("fixes").textContent);
  // The principals no element stands for yet, ranked server-side by how many
  // stated facts an answer would place. The page only renders what it is handed.
  const LINK_QUESTIONS = JSON.parse(document.getElementById("link_questions").textContent);
  // The open facts the conditional findings rest on, ranked server-side so
  // with the most-cited fact first. The page only renders them.
  const FACT_QUESTIONS = JSON.parse(document.getElementById("fact_questions").textContent);
  // How many of the reviewer's open facts used the fixed list of questions,
  // and how many it wrote in its own words, counted server-side.
  const FALLBACK = JSON.parse(document.getElementById("question_fallback").textContent);
  // True for a report the follow-up wrote: it asks nothing more (ADR 0054).
  const FINAL = JSON.parse(document.getElementById("final").textContent);
  // The run this report's follow-up started, where one locks it: the report
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
  // The follow-up's saved draft (ADR 0070): answers saved in batches that no
  // run has read yet. Every editor starts from its draft answer.
  const DRAFT = EARLIER.draft || { links: [], facts: [] };
  const DRAFT_FACTS = new Map(DRAFT.facts.map(a => [JSON.stringify(a.key), a]));
  const DRAFT_LINKS = new Map(DRAFT.links.map(a => [a.principal, a.element]));
  // An answer to start an editor from: the draft's, over a kept answer's.
  const startFrom = (kept, key) => {
    const drafted = DRAFT_FACTS.get(JSON.stringify(key));
    if (!drafted) return kept || null;
    if (!drafted.facets) return drafted;
    return { ...drafted, facets: { ...((kept && kept.facets) || {}), ...drafted.facets } };
  };
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
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const code = (text) => el("code", null, text);
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

  // A claim the critic rejected for `evidence` rules its unit out, and the
  // page says which reader did it: the lane excluded it and the critic
  // agreed, while "Does it apply here?" on the same row still carries the
  // rule's own answer, which may be unknown.
  const REVIEW_EXCLUDED = ["Excluded by review",
    "A lane excluded it and the critic agreed. The rule's own answer is above."];

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
    // rather than on a finding. The title is the only trace of what was lost.
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
    // rather than on a finding, grouped by kind.
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

  // The served report links to its analysis's description and report list.
  const reportRoute = location.pathname?.match(/^\/report\/([A-Za-z0-9_-]+)$/);
  if (reportRoute) {
    const analysis = "/?run=" + encodeURIComponent(reportRoute[1]);
    $("description-tab").href = analysis + "&view=description";
    $("reports-tab").href = analysis + "&view=reports";
    $("analysis-tabs").hidden = false;
  }
  // header
  $("sysname").textContent = `${R.input.system_name} security report`;
  // The static title names no system and no framework. The report names
  // both, so the tab reads the run rather than the template.
  document.title = R.input.system_name + " — report";
  const fmt = (t) => new Date(t).toISOString().replace("T"," ").replace(".000Z"," UTC");
  // The envelope's disclaimer says what the *service* is and how many of its
  // facts anybody checked. Each block carries its own, saying what that
  // framework's claims assert.
  $("disclaimer").textContent = R.disclaimer;

  // "in the model", never "analysed". `elements_analyzed` is the embedded
  // model's own element count and nothing more: no stage records that an
  // element was examined, and the coverage block below counts which elements a
  // draft *cited*, which is a different fact again. No field here supports
  // the statement that every element was examined.
  const frameworks = R.analyses.map(b => b.framework).join(", ");
  $("scope").textContent =
    `${R.elements_analyzed} elements in the model, under ${frameworks}`;

  // ---- Sections -----------------------------------------------------------
  // The page has three sections: the report, its questions, and how it was
  // made. The report is the one a reader lands on, and ?view= names another,
  // so a reload stays where the reader was.
  const SECTIONS = ["report", "questions", "about"];
  const showSection = (name) => {
    SECTIONS.forEach(each => {
      $(`${each}-view`).hidden = each !== name;
      if (each === name) $(`${each}-tab`).setAttribute("aria-current", "page");
      else $(`${each}-tab`).removeAttribute("aria-current");
    });
    const query = name === "report" ? "" : `?view=${name}`;
    if (location.search !== query) history.replaceState(null, "", location.pathname + query);
  };
  SECTIONS.forEach(name => {
    $(`${name}-tab`).href = location.pathname + (name === "report" ? "" : `?view=${name}`);
    $(`${name}-tab`).addEventListener("click", (event) => {
      event.preventDefault();
      showSection(name);
    });
  });
  $("back-to-report").addEventListener("click", () => showSection("report"));
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
      body.append(ref(ground.element_id), " \u2192 ", ref(ground.attribute));
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

  // ---- Findings -----------------------------------------------------------
  // A finding is one row that opens in place. The closed row shows its
  // severity, its title, its next action and its ID. The open row holds
  // everything the report says about it, so concision comes from the closed
  // row and nothing is removed.
  //
  // Built from the neutral shape and *widened* by whatever the framework's
  // own record carries: a consumer that does not know a framework reads an
  // ID, a title, a description, the elements and the grounds. So each extra
  // renders behind a presence test rather than being assumed: a claim with no
  // severity gets no severity, not a row that throws reading
  // `undefined.level`. Adding a framework needs no edit here.

  // A finding's anchor, for a link from a question or a gap to reach.
  const findingId = (t) => `finding-${t.framework}-${t.id}`;
  const findingKey = (t) => `${t.framework}/${t.id}`;
  // Each finding by `framework/claim`, for the questions to name.
  const FINDINGS = new Map(R.analyses.flatMap(block =>
    [...block.claims, ...block.rejected_claims].map(c => [`${block.framework}/${c.id}`, c])));
  // The heading over a finding's two recommendations, by its verdict: a
  // conditional finding's are not actions yet, and a dismissed one's are not
  // actions at all.
  const RECOMMENDATIONS = {
    "confirmed": "Recommended actions",
    "needs-info": "Potential mitigations · if confirmed",
    "rejected": "Original proposed mitigations · not active actions",
  };
  // The first thing a reader can do about a finding, for its closed row.
  const nextAction = (t) => {
    const waits = CONDITIONS[findingKey(t)] || [];
    if (t.verdict.status === "needs-info" && waits.length) {
      return `Information needed: ${waits[0].label}`;
    }
    if (t.verdict.status === "rejected") return "Dismissed";
    return t.mitigations && t.mitigations.length ? prose(t.mitigations[0].summary) : "";
  };

  function findingBody(marks, t) {
    const body = el("div", "finding-body");
    // A follow-up's finding says how it moved since the report the answers
    // came from.
    const moved = CHANGED.get(findingKey(t));
    if (moved && moved.change === "new") {
      body.append(el("div", "meta", "New since the earlier report."));
    } else if (moved && moved.change === "changed") {
      body.append(el("div", "meta", `Was ${VERDICT[moved.before][0]} in the earlier report.`));
    }
    // A final report's owner changed an answer this finding rests on, after
    // the analysis ran; the analysis did not run again (ADR 0054).
    if (CORRECTED.has(findingKey(t))) {
      body.append(el("div", "unknown",
        "Corrected after this report: an answer this finding rests on was " +
        "changed after the analysis ran. The analysis did not run again."));
    }

    // The lane, the confidence and the verdict, on one line.
    const facts = el("div", "badges");
    const lane = laneOf(t.framework, t);
    if (lane) facts.append(el("span", "cat", lane));
    if (t.severity) {
      const sev = el("span", "chip");
      const swatch = el("span", "swatch");
      swatch.style.background = svar(t.severity.level);
      sev.append(swatch, SEV[t.severity.level][0]);
      // A property assignment, not an attribute: there is no markup context
      // to escape out of.
      sev.title = `likelihood ${t.severity.likelihood} × impact ${t.severity.impact} → ${t.severity.level}`;
      facts.append(sev);
    }
    const [vlabel, vglyph] = VERDICT[t.verdict.status];
    facts.append(el("span", "chip verdict", `${vglyph} ${vlabel}`));
    if (t.confidence) facts.append(el("span", "chip confidence", `confidence: ${t.confidence}`));
    body.append(facts);

    // The facts a conditional finding waits on, each by its question and
    // what became of it, each linked to its editor where the report asks it.
    const waits = CONDITIONS[findingKey(t)] || [];
    if (waits.length) {
      const needed = el("div", "field");
      needed.append(el("div", "lbl", "Information needed"));
      const list = el("ul");
      waits.forEach(w => {
        const item = el("li");
        const asked = ASKS && QUESTION_ROWS.has(JSON.stringify(w.key));
        if (asked) {
          const go = el("a", null, w.label);
          go.href = `?view=questions`;
          go.addEventListener("click", (event) => {
            event.preventDefault();
            goToQuestion(w.key);
          });
          item.append(go);
        } else {
          item.append(w.label);
        }
        item.append(el("span", "meta", ` — ${WHY_OPEN[w.status] || w.status}`));
        list.append(item);
      });
      needed.append(list);
      body.append(needed);
    }

    if (t.verdict.status === "rejected") {
      const why = el("div", "field why-dismissed");
      why.append(el("div", "lbl", "Why dismissed"), proseEl("p", null, t.verdict.reason));
      body.append(why);
    }

    // The whole description the agent wrote, not a second summary of it.
    const story = el("details", "prov");
    story.append(el("summary", null, "Full attack narrative"), proseEl("div", "desc", t.description));
    body.append(story);

    const refs = el("div", "field refs");
    refs.append(el("div", "lbl", "Affected components and flows"));
    t.affected_element_ids.forEach(r => refs.append(ref(r)));
    body.append(refs);

    const next = el("div", "field");
    next.append(el("div", "lbl", RECOMMENDATIONS[t.verdict.status]));
    if (t.mitigations && t.mitigations.length) {
      const list = el("ul", "mits");
      t.mitigations.forEach(m => list.append(proseEl("li", null, m.summary)));
      next.append(list);
    } else if (marks.unmitigated.has(t.id)) {
      next.append(el("div", "caveat",
        "⚠ No mitigation proposed, and this finding does not rest on an unknown that would explain why."));
    }
    if (next.children.length > 1) body.append(next);

    // Why it matters, the critic's reason, the grounds with who justified
    // what, and each citation the service dropped. A `details` element, so
    // the browser does the toggling, keyboard included.
    const prov = el("details", "prov");
    prov.append(el("summary", null, "Evidence and justification"));
    if (t.severity) {
      const why = el("div", "field");
      why.append(lbl("Why it matters"), el("br"), prose(t.severity.justification));
      prov.append(why);
    }
    if (t.verdict.status !== "rejected" && t.verdict.reason) {
      const reason = el("div", "field");
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
    body.append(prov);
    return body;
  }

  function findingRow(marks, t) {
    const row = el("details", "finding" + (t.verdict.status === "rejected" ? " rejected" : ""));
    row.id = findingId(t);
    const head = el("summary");
    const sev = el("span", "finding-sev");
    if (t.severity) {
      const swatch = el("span", "swatch");
      swatch.style.background = svar(t.severity.level);
      sev.append(swatch, SEV[t.severity.level][0]);
    }
    const what = el("span", "finding-what");
    what.append(proseEl("span", "finding-title", t.title));
    const next = nextAction(t);
    if (next) {
      const line = el("span", "finding-next");
      line.append(next);
      what.append(line);
    }
    head.append(sev, what, el("span", "tid", t.id), el("span", "finding-open"));
    row.append(head, findingBody(marks, t));
    return row;
  }

  // Severity order where every claim is graded, and the given order otherwise
  // — the package's declared lane order, the only ranking a framework that
  // grades nothing has.
  const bySeverity = (list) => list.every(c => c.severity)
    ? [...list].sort((a, b) => SEV_ORDER.indexOf(a.severity.level) - SEV_ORDER.indexOf(b.severity.level))
    : list;
  // Open every `details` around a node, and the node itself, so a link to it
  // lands on it.
  const openTo = (node) => {
    if (node.tagName === "DETAILS") node.open = true;
    for (let d = node.closest("details"); d; d = d.parentElement && d.parentElement.closest("details")) {
      d.open = true;
    }
  };
  // A link from a question to a finding: the report, with the finding open
  // and focused.
  const goToFinding = (key) => {
    const t = FINDINGS.get(key);
    const row = t && $(findingId(t));
    if (!row) return;
    showSection("report");
    openTo(row);
    row.scrollIntoView({ block: "start" });
    row.querySelector("summary").focus();
  };

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
            ? REVIEW_EXCLUDED
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
        (CONDITIONS[`${block.framework}/${claim.id}`] || []).forEach(w => {
          body.append(el("div", "lbl", "Still to answer"));
          body.append(el("p", null, w.label));
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

  // ---- The report -----------------------------------------------------------
  // What a block found, in words: the confirmed findings by severity, then
  // how many are conditional and how many were dismissed. A report is
  // complete for what it was told, so the summary says that no question
  // needs an answer.
  function summaryOf(block, conditional, dismissed) {
    const box = el("div", "summary");
    const confirmed = block.claims.filter(c => c.verdict.status === "confirmed").length;
    const first = el("p");
    let lead = `${confirmed} confirmed ${confirmed === 1 ? "finding" : "findings"}`;
    const graded = block.summary.by_severity_confirmed;
    if (graded && confirmed) {
      lead += ": " + SEV_ORDER.filter(l => graded[l])
        .map(l => `${graded[l]} ${SEV[l][0].toLowerCase()}`).join(", ");
    }
    first.append(el("b", null, `${lead}.`));
    box.append(first);
    const rest = [];
    if (conditional) {
      rest.push(`${counted(conditional, "additional finding is", "additional findings are")} conditional on missing information.`);
    }
    if (dismissed) {
      rest.push(conditional
        ? `${dismissed} ${dismissed === 1 ? "was" : "were"} dismissed.`
        : `${counted(dismissed, "finding was", "findings were")} dismissed.`);
    }
    if (conditional) rest.push("You can use this report without answering any questions.");
    if (rest.length) box.append(el("p", null, rest.join(" ")));
    return box;
  }

  // A collapsed section of finding rows, with its count in its title.
  const findingGroup = (title, rows) => {
    const box = el("details", "finding-group");
    box.append(el("summary", null, `${title} · ${rows.length}`), ...rows);
    return box;
  };

  // One framework's whole section: its summary, the actions two findings
  // share, its confirmed findings, then its conditional and its dismissed
  // findings, each closed, and the units it answered for. Its disclaimer,
  // its scope counts and its diagnostics are about how the analysis ran, so
  // they sit under About the analysis.
  function renderBlock(block) {
    const marks = marksOf(block);
    const section = el("section", "analysis");
    if (R.analyses.length > 1) {
      section.append(el("h2", null, `${block.framework} · v${block.framework_version}`));
    }
    // A requirement ruled out by review is an answer about the requirement,
    // and sits on its unit's row; a draft dismissed for arguing badly is not.
    const dismissed = bySeverity(block.rejected_claims.filter(c => !rulesOut(block, c)));
    const confirmed = bySeverity(block.claims.filter(c => c.verdict.status === "confirmed"));
    const conditional = bySeverity(block.claims.filter(c => c.verdict.status === "needs-info"));
    section.append(summaryOf(block, conditional.length, dismissed.length));

    // One missing control that two or more confirmed findings rest on is one
    // action for all of them. Each finding stays its own row.
    (FIXES[block.framework] || []).forEach(f => {
      const line = el("p", "shared-action");
      line.append(`One shared action addresses ${f.rests.length} findings: `, el("b", null, f.label), ". ");
      f.rests.forEach((id, i) => {
        const link = el("a", "tid", id);
        link.href = `#finding-${block.framework}-${id}`;
        link.addEventListener("click", (event) => {
          event.preventDefault();
          goToFinding(`${block.framework}/${id}`);
        });
        line.append(...(i ? [" · "] : []), link);
      });
      section.append(line);
    });

    if (confirmed.length) {
      section.append(el("h3", null, "Confirmed findings"));
      const list = el("div", "findings");
      confirmed.forEach(t => list.append(findingRow(marks, t)));
      section.append(list);
    } else {
      // Said rather than left blank: a framework that examined the system and
      // confirmed nothing is a result.
      section.append(el("p", "meta", "No finding is confirmed under this framework."));
    }
    if (conditional.length) {
      section.append(findingGroup("Conditional findings", conditional.map(t => findingRow(marks, t))));
    }
    if (dismissed.length) {
      section.append(findingGroup("Dismissed findings", dismissed.map(t => findingRow(marks, t))));
    }
    const units = unitTable(block);
    if (units) section.append(units);
    $("analyses").append(section);
    frameworkNotes(block, marks);
  }

  // ---- Questions --------------------------------------------------------------
  // What the report asks: which element each principal is (link questions),
  // and the open facts its conditional findings rest on (fact questions), in
  // the question list the early questions use. Answers go to /answer/{run}:
  // a save keeps a draft and runs nothing, and an update starts the report's
  // one follow-up (ADR 0070). Code writes every answer, so no model reads
  // one. Every label is untrusted and lands as text.
  //
  // A final report asks nothing. Its answers can be corrected (ADR 0054) in
  // the same list, and a correction runs nothing.

  // Whether the report asks the questions its conditional findings wait on.
  const ASKS = !FINAL && !RESUMED_BY
    && (FACT_QUESTIONS.length + LINK_QUESTIONS.length + EARLIER_FACTS.length + EARLIER_LINKS.length) > 0;
  // Every question row the page draws, by its key as a string: a link
  // question's key is its principal, and a fact question's the JSON of its
  // key. One map, so a finding's link and the question it reaches agree.
  const QUESTION_ROWS = new Map();
  const rowKey = (row) => (row.kind === "link" ? row.q.principal : JSON.stringify(row.q.key));
  // Two answers are one answer when the fields the submitter gives agree.
  const sameAnswer = (a, b) => {
    if (!a || !b) return !a && !b;
    if ("principal" in a) return a.principal === b.principal && a.element === b.element;
    if ((a.detail || "") !== (b.detail || "")) return false;
    if (!a.facets && !b.facets) return a.value === b.value;
    if (!a.facets || !b.facets) return false;
    const given = Object.entries(a.facets);
    return given.length === Object.keys(b.facets).length
      && given.every(([facet, value]) => b.facets[facet] === value);
  };
  // What became of a question before this report, in the words its row shows.
  const HISTORY = {
    open: "New question from this analysis",
    unanswered: "Earlier answer: left blank",
    skipped: "Earlier answer: skipped",
    partial: "Earlier answer: answered in part",
    unknown: "Earlier answer: I don't know",
    answered: "Earlier answer: given, and the analysis did not find it settled",
  };
  const questionTotal = FACT_QUESTIONS.length + LINK_QUESTIONS.length;
  $("questions-tab").textContent = questionTotal ? `Questions (${questionTotal})` : "Questions";

  // A question's findings as links, each its ID with its title on hover.
  const usedBy = (findings) => {
    const line = el("div", "meta used-by");
    line.append("Used by: ");
    findings.forEach((key, i) => {
      const t = FINDINGS.get(key);
      const link = el("a", null, t ? t.id : key);
      link.href = t ? `#${findingId(t)}` : "#";
      if (t) link.title = t.title;
      link.addEventListener("click", (event) => {
        event.preventDefault();
        goToFinding(key);
      });
      line.append(...(i ? [" · "] : []), link);
    });
    return line;
  };
  // The lines under a question's "?": what the answer informs, and that the
  // analysis decides again.
  const whyAsked = (q) => {
    const parts = [];
    const findings = q.findings || [];
    if (findings.length) {
      const titles = findings.map(f => (FINDINGS.get(f) || { title: f }).title);
      parts.push(`Your answer informs ${counted(findings.length, "finding", "findings")}: ${titles.join("; ")}.`);
      parts.push("A finding can need other facts too. The analysis decides again whether each one is settled.");
    } else if (q.findings) {
      parts.push("No conditional finding waits on this fact. The next analysis reads the answer.");
    }
    if (q.band) parts.push(`The most important finding it informs is ${q.band}.`);
    if (q.basis === "critic") {
      parts.push("The reviewer raised this question. It can change when the analysis runs again.");
    }
    return parts;
  };
  // The answers a row starts from: the draft's, over a kept answer's.
  const startOf = (row) => (row.kind === "link"
    ? (DRAFT_LINKS.has(row.q.principal) ? { principal: row.q.principal, element: DRAFT_LINKS.get(row.q.principal) } : null)
    : startFrom(RETAINED.get(JSON.stringify(row.q.key)), row.q.key));

  // Select one group of the list: its box shows, open, and every other box
  // hides. The index marks it.
  const selectGroup = (box) => {
    [...$("list").children].forEach(node => { node.hidden = node !== box; });
    box.open = true;
    $("question-index").querySelectorAll("a").forEach(link => {
      if (link.box === box) link.setAttribute("aria-current", "true");
      else link.removeAttribute("aria-current");
    });
  };
  const indexGroups = () => {
    $("question-index").replaceChildren();
    [...$("list").children].forEach((box, index) => {
      box.id = `question-group-${index}`;
      const link = el("a", null, box.querySelector(".group-heading").textContent);
      const count = box.querySelectorAll(".question, tr[data-key]").length;
      link.append(el("small", null, counted(count, "question", "questions")));
      link.href = `#${box.id}`;
      link.box = box;
      link.addEventListener("click", (event) => {
        event.preventDefault();
        selectGroup(box);
        box.querySelector("summary").focus();
      });
      $("question-index").append(link);
    });
  };
  // A link from a finding to a question: the question's group, open, with
  // the focus on its first input.
  const goToQuestion = (key) => {
    const row = QUESTION_ROWS.get(JSON.stringify(key));
    if (!row) return;
    showSection("questions");
    selectGroup(row.box.node);
    openTo(row.node);
    row.node.scrollIntoView({ block: "center" });
    if (row.input && typeof row.input.focus === "function") row.input.focus();
  };

  // A list over `entries`, each `{ q, kind, saved }`, drawn into the layout.
  // `reopen` is whether a known answer may go back to "I don't know".
  const drawList = (entries, { reopen, onEdit, notes }) => {
    const list = questionList({
      list: $("list"),
      onEdit,
      reopen,
      inputFor: (q, prefill, changed) => answerEditor(q, {
        choices: q.choices.map(id => ({ id, name: NAMES[id] })),
        prefill,
        reopen,
        changed,
      }),
      contextOf: whyAsked,
      notes,
    });
    let prev = null;
    entries.forEach(({ q, kind, saved, compact }) => {
      const row = { q, kind, node: el("p", "question"), saved: null };
      row.node.dataset.key = rowKey(row);
      QUESTION_ROWS.set(rowKey(row), row);
      list.place(row, prev);
      if (compact) list.drawSaved(row, saved);
      else list.drawOpen(row, saved);
      row.start = saved;
      prev = row;
    });
    $("question-layout").hidden = false;
    indexGroups();
    if ($("list").children.length) selectGroup($("list").children[0]);
  };
  // A link question as a row of the list: its options with their names.
  const linkQuestion = (q) => ({
    ...q,
    options: q.options.map(id => ({ id, name: NAMES[id] })),
    group: "links",
    group_heading: "Match each user, team or account to a part of your system",
  });
  // An answer this report read, as a row the list draws compact. The answers
  // share one group, after the questions.
  const EARLIER_GROUP = { group: "earlier", group_heading: "Answers this report read" };
  const earlierFact = (a) => ({ ...a, ...EARLIER_GROUP, element: a.label });

  // The action area: what the answers on the page are, and the two buttons.
  const status = el("p", "save-line");
  status.setAttribute("role", "status");
  const problem = el("p", "problem");
  problem.hidden = true;
  const button = (text, primary) => {
    const control = el("button", primary ? "primary" : null, text);
    control.type = "button";
    return control;
  };
  // What a row sends: its answer where it differs from the one it started
  // from. A facets answer sends only the facets answered otherwise: the
  // service keeps the earlier answer to a facet left out. A changed detail
  // alone sends the facets it starts from, which the service keeps as they
  // are.
  const sendable = (row) => {
    const answer = row.read ? row.read() : null;
    if (!answer || sameAnswer(answer, row.start)) return null;
    if (!answer.facets || !row.start || !row.start.facets) return answer;
    const before = row.start.facets;
    const given = Object.fromEntries(Object.entries(answer.facets)
      .filter(([facet, value]) => value !== before[facet]));
    if (Object.keys(given).length) return withDetail({ key: answer.key, facets: given }, answer.detail || "");
    return (answer.detail || "") !== (row.start.detail || "") ? answer : null;
  };
  // Every row whose answer would be sent, with what it sends.
  const changes = () => [...QUESTION_ROWS.values()]
    .map(row => ({ row, answer: sendable(row) }))
    .filter(({ answer }) => answer);

  if (RESUMED_BY) {
    // The follow-up has run, or runs now: the page offers no second one.
    const note = el("p", "meta",
      "Your answers to this report started its follow-up, so this report asks " +
      "no more questions. When the follow-up finishes, its report is ");
    const link = el("a", null, "here");
    link.href = `/report/${encodeURIComponent(RESUMED_BY)}`;
    note.append(link, ".");
    $("questions-state").append(note);
    $("questions-lead").textContent = "This report's one follow-up has started.";
  } else if (FINAL) {
    $("questions-title").textContent = "Correct an answer";
    $("questions-lead").textContent = "This report is final: its one follow-up has run, " +
      "so it asks no more questions. If an answer was wrong or a guess, change it, or " +
      "choose \"I don't know\". A correction is kept beside this report and marks the " +
      "findings that rest on it. Saving corrections does not run the analysis again, " +
      "and does not change the verdicts in this report.";
    const answers = CORRECTIONS.answers || [];
    if (!answers.length) {
      $("questions-state").append(el("p", "meta", "This report read no answers to correct."));
    } else {
      const save = button("Save corrections", true);
      const showState = () => {
        const n = changes().length;
        status.textContent = n ? `${counted(n, "correction", "corrections")} not saved yet` : "No new corrections";
      };
      drawList(answers.map(a => ({ q: earlierFact(a), kind: "fact", saved: a.answer, compact: true })), {
        reopen: true,
        onEdit: showState,
        notes: (row) => {
          const a = answers.find(each => JSON.stringify(each.key) === JSON.stringify(row.q.key));
          return a && a.corrected ? [el("div", "meta", "Corrected after this report")] : [];
        },
      });
      let saving = false;
      save.addEventListener("click", async () => {
        if (saving) return;
        const facts = changes().map(({ answer }) => answer);
        if (!facts.length) {
          status.textContent = "No new corrections to save.";
          return;
        }
        saving = save.disabled = true;
        status.textContent = "Saving…";
        try {
          const saved = await fetch("/correct/" + encodeURIComponent(location.pathname.split("/").pop()), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ facts, revision: CORRECTIONS.revision }),
          });
          const body = await saved.json();
          if (!saved.ok) throw new Error(body.message);
          location.href = location.pathname + "?view=questions";
        } catch (error) {
          problem.textContent = error.message || "The connection was interrupted. Your corrections are still on this page.";
          problem.hidden = false;
          status.textContent = "Not saved";
          saving = save.disabled = false;
        }
      });
      showState();
      $("question-actions").append(status, el("p", "hint",
        "Saving corrections runs nothing."), save, problem);
    }
  } else if (!ASKS) {
    $("questions-lead").textContent = "This report asks no questions.";
  } else {
    $("questions-lead").textContent = "Answer what you know. You can save and come back.";
    const entries = [
      ...LINK_QUESTIONS.map(q => ({ q: linkQuestion(q), kind: "link" })),
      ...FACT_QUESTIONS.map(q => ({ q, kind: "fact" })),
      ...EARLIER_LINKS.map(a => ({
        q: { ...linkQuestion(a), ...EARLIER_GROUP },
        kind: "link",
        saved: DRAFT_LINKS.has(a.principal)
          ? { principal: a.principal, element: DRAFT_LINKS.get(a.principal) }
          : { principal: a.principal, element: a.answer.element },
        compact: !DRAFT_LINKS.has(a.principal),
      })),
      ...EARLIER_FACTS.map(a => ({
        q: earlierFact(a),
        kind: "fact",
        saved: startFrom(a.answer, a.key),
        compact: !DRAFT_FACTS.has(JSON.stringify(a.key)),
      })),
    ].map(entry => ({ ...entry, saved: entry.saved || startOf(entry) }));

    const save = button("Save draft", false);
    const update = button("Update report", true);
    let revision = EARLIER.revision || 0;
    let busy = false;
    const showState = (text) => {
      if (text) {
        status.textContent = text;
        return;
      }
      const n = changes().length;
      const drafted = DRAFT_FACTS.size + DRAFT_LINKS.size;
      status.textContent = n
        ? `${n} of ${counted(questionTotal, "question", "questions")} edited · Unsaved changes`
        : drafted
          ? `Saved draft: ${counted(drafted, "answer", "answers")}. Nothing runs until you choose Update report.`
          : "No new answers";
    };
    // How far the answers reach, before Update report is pressed (#561). A
    // finding is covered once every question that names it has an answer
    // other than "I don't know", in any order, so the count follows the
    // answers given and not the rank. A finding that needs only some facets
    // of a question is covered once those are known (FactQuestion.needs).
    const coverage = el("p", "meta");
    const reach = el("p", "meta");
    const waitsOn = new Map();
    FACT_QUESTIONS.forEach((q, index) => q.findings.forEach(finding => {
      if (!waitsOn.has(finding)) waitsOn.set(finding, []);
      waitsOn.get(finding).push(index);
    }));
    const factRow = index => QUESTION_ROWS.get(JSON.stringify(FACT_QUESTIONS[index].key));
    const needOf = (index, finding) => (FACT_QUESTIONS[index].needs || {})[finding] || [];
    const knownFor = (index, facets) => {
      const row = factRow(index);
      return row.knownFor ? row.knownFor(facets) : row.known();
    };
    // Each finding's lane, read from the field its package stamps, so the
    // scope line can say how many lanes the answers reach.
    const laneOfFinding = new Map(R.analyses.flatMap(block => block.claims.map(c =>
      [`${block.framework}/${c.id}`, laneOf(block.framework, c) || block.framework])));
    const recount = () => {
      if (!waitsOn.size) return;
      const covered = [...waitsOn]
        .filter(([finding, asked]) => asked.every(index => knownFor(index, needOf(index, finding))))
        .length;
      coverage.textContent =
        `Your answers cover every question for ${covered} of the ${waitsOn.size} findings ` +
        "that wait on one. The analysis decides again whether they are settled.";
      const given = FACT_QUESTIONS.filter((q, index) => knownFor(index, [])).length;
      const reached = [...waitsOn]
        .filter(([, asked]) => asked.some(index => knownFor(index, []))).map(([f]) => f);
      const lanes = new Set(reached.map(f => laneOfFinding.get(f)).filter(Boolean));
      reach.textContent = given
        ? `${counted(given, "answer reaches", "answers reach")} ${counted(reached.length, "finding", "findings")} in ${counted(lanes.size, "lane", "lanes")}. ` +
          "Code records each answer, and no model interprets it before the analysis. " +
          "The follow-up then runs the whole analysis once, and its report is final."
        : "No answer given yet.";
    };
    drawList(entries, {
      reopen: false,
      onEdit: () => {
        showState();
        recount();
      },
      notes: (row) => {
        const lines = [];
        if (row.q.history) lines.push(el("div", "meta question-history", HISTORY[row.q.history] || row.q.history));
        if ((row.q.findings || []).length) lines.push(usedBy(row.q.findings));
        return lines;
      },
    });

    // The run id is this page's own path: /report/{run}.
    const run = location.pathname.split("/").pop();
    const post = async (body) => {
      let response;
      let reply;
      try {
        response = await fetch("/answer/" + encodeURIComponent(run), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        reply = await response.json();
      } catch {
        throw new Error("The connection was interrupted. Your answers are still on this page;"
          + " reload it to check what was saved, or try again.");
      }
      if (!response.ok) throw new Error(reply.message);
      return reply;
    };
    const entered = () => {
      const edited = changes();
      return {
        edited,
        links: edited.filter(({ row }) => row.kind === "link").map(({ answer }) => answer),
        facts: edited.filter(({ row }) => row.kind !== "link").map(({ answer }) => answer),
      };
    };
    // Save the answers in as many requests as the limits need, each naming
    // the revision the last one returned. A row then starts from what it saved.
    const saveInParts = async ({ edited, links, facts }) => {
      const parts = Math.max(Math.ceil(facts.length / ANSWER_LIMITS.facts),
        Math.ceil(links.length / ANSWER_LIMITS.links), 1);
      for (let part = 0; part < parts; part += 1) {
        const reply = await post({
          links: links.slice(part * ANSWER_LIMITS.links, (part + 1) * ANSWER_LIMITS.links),
          facts: facts.slice(part * ANSWER_LIMITS.facts, (part + 1) * ANSWER_LIMITS.facts),
          save: true,
          revision,
        });
        revision = reply.revision;
      }
      edited.forEach(({ row, answer }) => {
        row.start = row.read();
        if (row.kind === "link") DRAFT_LINKS.set(answer.principal, answer.element);
        else DRAFT_FACTS.set(JSON.stringify(answer.key), answer);
      });
    };
    const setBusy = (on) => {
      busy = on;
      save.disabled = update.disabled = on;
    };
    save.addEventListener("click", async () => {
      if (busy) return;
      const answers = entered();
      if (!answers.edited.length) {
        showState("No new answers to save.");
        return;
      }
      setBusy(true);
      problem.hidden = true;
      showState("Saving…");
      try {
        await saveInParts(answers);
        showState();
      } catch (error) {
        problem.textContent = error.message;
        problem.hidden = false;
        showState("Not saved");
      } finally {
        setBusy(false);
      }
    });
    update.addEventListener("click", async () => {
      if (busy) return;
      setBusy(true);
      problem.hidden = true;
      showState("Starting the follow-up…");
      try {
        let answers = entered();
        if (answers.links.length > ANSWER_LIMITS.links || answers.facts.length > ANSWER_LIMITS.facts) {
          // More than one request carries: save them all first, then run on
          // the draft.
          await saveInParts(answers);
          answers = { links: [], facts: [] };
        }
        const reply = await post({ links: answers.links, facts: answers.facts, revision });
        // The form page follows a run's progress and opens its report.
        location.href = "/?follow=" + encodeURIComponent(reply.run);
      } catch (error) {
        problem.textContent = error.message;
        problem.hidden = false;
        showState();
        setBusy(false);
      }
    });
    // Leaving with answers that are not saved asks first.
    window.addEventListener("beforeunload", (event) => {
      if (busy || !changes().length) return;
      event.preventDefault();
      event.returnValue = "";
    });
    showState();
    recount();
    $("question-actions").append(status, coverage, reach,
      el("p", "hint", "Update report uses this report's one follow-up: the analysis runs " +
        "once more with your answers, and its report is final. A fact you do not know " +
        "stays open. This report stays available."),
      save, " ", update, problem);
  }

  // The invitation under the report: optional, and only where the report
  // asks something or can be corrected.
  if (ASKS) {
    const invite = el("div", "invite");
    const lead = el("p");
    lead.append(el("b", null, "Have more information?"));
    invite.append(lead,
      el("p", "meta", "Answer a question once, and use it for every finding that depends on it."));
    const add = el("button", "quiet", "Add information");
    add.type = "button";
    add.addEventListener("click", () => {
      showSection("questions");
      $("question-index").querySelector("a")?.focus();
    });
    invite.append(add);
    $("invite").append(invite);
  } else if (RESUMED_BY) {
    const note = el("p", "meta", "This report's follow-up has started. Its report is ");
    const link = el("a", null, "here");
    link.href = `/report/${encodeURIComponent(RESUMED_BY)}`;
    note.append(link, ".");
    $("invite").append(note);
  } else if (FINAL && (CORRECTIONS.answers || []).length) {
    const note = el("p", "meta", "This report is final. You can still ");
    const link = el("a", null, "correct an answer");
    link.href = "?view=questions";
    link.addEventListener("click", (event) => {
      event.preventDefault();
      showSection("questions");
    });
    note.append(link, "; a correction runs nothing.");
    $("invite").append(note);
  }

  // What the answers did, before any finding: how the follow-up's findings
  // moved since the report the answers came from. A finding the follow-up no
  // longer raises has no row, so it is listed here by title.
  if (CHANGES.length) {
    const tally = new Map();
    CHANGES.forEach(c => {
      const line = CHANGE_LINE[c.change](c);
      tally.set(line, (tally.get(line) || 0) + 1);
    });
    const box = el("div", "meta");
    box.append(`Since the report your answers came from, ${counted(CHANGES.length, "finding", "findings")}: ` +
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
  R.analyses.forEach(renderBlock);

  // ---- About the analysis -----------------------------------------------------
  // How this report was made: its identifiers, its times and the versions
  // that ran, each by a readable label with its whole value. A value the
  // report does not carry is left out, never made up.
  const facts = [
    ["Report ID", code(R.job.id)],
    ["Follow-up of", PARENT_RUN && Object.assign(el("a", null, PARENT_RUN), {
      href: `/report/${encodeURIComponent(PARENT_RUN)}` })],
    ["Follow-up run", RESUMED_BY && Object.assign(el("a", null, RESUMED_BY), {
      href: `/report/${encodeURIComponent(RESUMED_BY)}` })],
    ["Started", fmt(R.job.created_at)],
    ["Generated", fmt(R.job.completed_at)],
    ["Frameworks", R.analyses.map(b => `${b.framework} ${b.framework_version}`).join(", ")],
    ["Sources", R.input.sources.map(s => s.label).join(", ")],
    ["Report schema", R.schema_version],
    ["Critic re-asks", String(R.job.revise_rounds)],
    ["Instruction digest", R.analysis_context && code(R.analysis_context.instruction_sha256)],
    ["Domain packs", R.analysis_context && R.analysis_context.domain_packs.join(", ")],
    ["Execution identity version", R.execution && String(R.execution.identity_version)],
    ["Review independence", R.execution && R.execution.review_independence],
    ["Builds", R.execution && Object.keys(R.execution.build).length
      && Object.entries(R.execution.build).map(([name, version]) => `${name} ${version}`).join(", ")],
  ];
  facts.filter(([, value]) => value).forEach(([label, value]) => {
    const term = el("dt", null, label);
    const detail = el("dd");
    detail.append(value);
    $("report-facts").append(term, detail);
  });

  // One framework's notes on how its analysis ran: its disclaimer, the units
  // it raised nothing about, and what the service dropped or repaired.
  function frameworkNotes(block, marks) {
    const notes = el("section", "analysis");
    notes.append(el("h3", null, `${block.framework} · v${block.framework_version}`));
    notes.append(el("div", "disclaimer-block", block.disclaimer));
    // What this framework considered and raised nothing about, grouped by
    // state, and the deferred group again by the evidence that would settle
    // it. A framework answering in its own units lists every one of them, so
    // the counts stand for the list, which the report holds unit by unit.
    if (block.scope.length) {
      const byState = {};
      block.scope.forEach(e => { (byState[e.state] = byState[e.state] || []).push(e); });
      const ruledOutClaims = block.rejected_claims.filter(c => rulesOut(block, c));
      const lines = [`${block.scope.length} units with no claim raised`];
      const count = (state) => (byState[state] || []).length;
      if (count("not-applicable")) lines.push(`${count("not-applicable")} ruled out — does not apply`);
      if (ruledOutClaims.length) {
        lines.push(`${ruledOutClaims.length} ruled out by review — does not apply; each on its own row in the report`);
      }
      if (count("undecidable")) {
        lines.push(`${count("undecidable")} undecidable — the input never says whether this framework applies`);
      }
      if (count("not-raised")) {
        lines.push(`${count("not-raised")} not raised — no lane filed a claim; not a verdict that they apply`);
      }
      const deferred = byState["needs-other-evidence"] || [];
      if (deferred.length) {
        lines.push(`${deferred.length} could not be evaluated from this input`);
        const byNeed = {};
        deferred.forEach(e => { byNeed[e.needs] = (byNeed[e.needs] || 0) + 1; });
        Object.keys(byNeed).sort().forEach(need => lines.push(`  ${byNeed[need]} need ${need}`));
      }
      const list = el("ul", "meta");
      lines.forEach(line => list.append(el("li", null, line)));
      notes.append(list);
    }
    // What the service dropped for naming something this framework does not
    // have. There is no finding to hang these on, so they are listed here.
    if (marks.unknown.length) {
      const note = el("div", "dropped");
      note.append(el("b", null,
        `${counted(marks.unknown.length, "ruling", "rulings")} dropped for naming an unpublished identifier`));
      const list = el("ul");
      marks.unknown.forEach(m => {
        const item = el("li");
        item.append(code(m.claim_id), " — ", prose(m.title));
        list.append(item);
      });
      note.append(list);
      notes.append(note);
    }
    if (marks.groundless.length) {
      const note = el("div", "dropped");
      note.append(el("b", null,
        `${counted(marks.groundless.length, "finding", "findings")} dropped for a fault in one entry`));
      const list = el("ul");
      marks.groundless.forEach(m => {
        const item = el("li");
        item.append(code(m.claim_id), " — ", prose(m.title), " (", prose(m.reason), ")");
        list.append(item);
      });
      note.append(list);
      notes.append(note);
    }
    // A repaired run and a clean one read alike without this. The count per
    // kind is what a reader acts on, so the kinds are grouped.
    if (marks.unreconciled.length) {
      const note = el("div", "dropped");
      note.append(el("b", null,
        `${counted(marks.unreconciled.length, "problem", "problems")} the review's first pass left for its re-ask`));
      const byKind = new Map();
      marks.unreconciled.forEach(m => {
        if (!byKind.has(m.kind)) byKind.set(m.kind, []);
        byKind.get(m.kind).push(m.claim_id);
      });
      const list = el("ul");
      byKind.forEach((ids, kind) => {
        const item = el("li");
        item.append(code(kind), ` — ${ids.length}: `, prose(ids.join(", ")));
        list.append(item);
      });
      note.append(list);
      notes.append(note);
    }
    $("framework-notes").append(notes);
  }

  // Each question by its Q number and label, with the reference the service
  // keys it by. The reference stays here, out of the question list.
  if (FACT_QUESTIONS.length) {
    const table = el("table");
    const head = el("tr");
    head.append(el("th", null, "Q"), el("th", null, "Question"), el("th", null, "Reference"));
    table.append(head);
    FACT_QUESTIONS.forEach((q, index) => {
      const row = el("tr");
      row.append(cell(`Q${index + 1}`), cell(q.label), cell(code(q.key.filter(Boolean).join(" · "))));
      table.append(row);
    });
    $("question-refs").append(table);
    // How the reviewer named the open facts: from the fixed list of question
    // kinds, or in its own words.
    if (FALLBACK.typed + FALLBACK.free_text) {
      $("question-refs").append(el("div", "meta",
        `The reviewer asked ${counted(FALLBACK.typed, "open fact", "open facts")} from the fixed list of ` +
        `questions and ${FALLBACK.free_text} in its own words.`));
    }
  } else {
    $("question-refs").append(el("div", "meta", "This report asks no questions."));
  }

  // ---- The system model, drawn ------------------------------------------------
  // A box per trust zone, the elements in their zone, and an arrow per flow,
  // drawn as SVG nodes. The table below holds the same facts in words, so it
  // stays the text a reader and a screen reader use.
  //
  // **Placement is stated, never inferred.** An element whose zone is not one
  // of the model's trust boundaries is drawn outside every zone, under "Zone
  // not stated", and an element whose zone the service inferred says "zone
  // assumed" (SystemModel.assumed_zone_elements). Neither reads as a placement
  // the input made. Whether a flow crosses a boundary is the service's
  // reading too (boundary_crossings), never the page's.
  const ASSUMED_ZONES = new Set(JSON.parse($("assumed_zones").textContent));
  const SVG_NS = "http://www.w3.org/2000/svg";
  const svg = (tag, attributes = {}, text = null) => {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attributes).forEach(([name, value]) => node.setAttribute(name, String(value)));
    if (text != null) node.textContent = text;
    return node;
  };
  // A label cut to `max` characters; the whole text is in the node's title.
  const clip = (text, max) => (text.length > max ? `${text.slice(0, max - 1)}…` : text);
  // A name in at most two lines of about `width` characters.
  const wrapped = (text, width) => {
    const out = [""];
    String(text).split(/\s+/).forEach(word => {
      const last = out.length - 1;
      if (!out[last] || `${out[last]} ${word}`.length <= width) out[last] = `${out[last]} ${word}`.trim();
      else out.push(word);
    });
    return (out.length > 2 ? [out[0], out.slice(1).join(" ")] : out).map(line => clip(line, width));
  };
  const KIND_WORDS = {
    "external-entity": "external entity",
    "process": "process",
    "data-store": "data store",
  };
  // The geometry, in viewBox units.
  const DIAGRAM = { width: 600, pad: 16, inset: 16, columns: 3, nodeW: 136, nodeH: 56,
    label: 28, rowGap: 44, bandGap: 32 };

  function drawModel(model) {
    const nodes = [
      ...model.external_entities.map(e => ({ kind: "external-entity", e })),
      ...model.processes.map(e => ({ kind: "process", e })),
      ...model.data_stores.map(e => ({ kind: "data-store", e })),
    ];
    if (!nodes.length) return null;
    const index = new Map(nodes.map((node, i) => [node.e.id, i]));
    const flows = model.data_flows.filter(f => index.has(f.source) && index.has(f.destination));

    // Each element's rank: the longest path to it along the flows, with each
    // flow that closes a cycle left out, so the drawing reads in the flows'
    // direction from the top down.
    const next = nodes.map(() => []);
    flows.forEach(f => next[index.get(f.source)].push(index.get(f.destination)));
    const state = nodes.map(() => 0);
    const closes = new Set();
    const visit = (i) => {
      state[i] = 1;
      next[i].forEach(j => {
        if (state[j] === 1) closes.add(`${i}>${j}`);
        else if (!state[j]) visit(j);
      });
      state[i] = 2;
    };
    nodes.forEach((_, i) => { if (!state[i]) visit(i); });
    const rank = nodes.map(() => 0);
    nodes.forEach(() => next.forEach((targets, i) => targets.forEach(j => {
      if (!closes.has(`${i}>${j}`) && rank[j] < rank[i] + 1) rank[j] = rank[i] + 1;
    })));

    // A band per zone that holds an element, in the order the flows reach
    // them, and the elements with no stated zone last, in a band of their own.
    const zones = model.trust_boundaries.map(b => ({ id: b.id, name: b.name, members: [] }));
    const zoneOf = new Map(zones.map(zone => [zone.id, zone]));
    const unplaced = { id: null, name: "Zone not stated", members: [] };
    nodes.forEach((node, i) => (zoneOf.get(node.e.trust_zone) || unplaced).members.push(i));
    const lead = zone => Math.min(...zone.members.map(i => rank[i]));
    const bands = [...zones, unplaced].filter(zone => zone.members.length)
      .sort((a, b) => (a.id === null) - (b.id === null) || lead(a) - lead(b));

    const D = DIAGRAM;
    const bandW = D.width - 2 * D.pad;
    const gapX = (bandW - 2 * D.inset - D.columns * D.nodeW) / (D.columns - 1);
    const place = new Map();
    // Each element's neighbours along the flows, either way, so an element
    // sits under the elements it talks to and fewer flows cross.
    const neighbours = nodes.map(() => []);
    flows.forEach(f => {
      neighbours[index.get(f.source)].push(index.get(f.destination));
      neighbours[index.get(f.destination)].push(index.get(f.source));
    });
    const across = i => {
      const placed = neighbours[i].filter(j => place.has(j)).map(j => place.get(j).x);
      return placed.length ? placed.reduce((sum, x) => sum + x, 0) / placed.length : Infinity;
    };
    let top = D.pad;
    const drawnBands = bands.map(zone => {
      const members = [...zone.members]
        .sort((a, b) => across(a) - across(b) || rank[a] - rank[b] || a - b);
      const rows = Math.ceil(members.length / D.columns);
      members.forEach((i, at) => {
        const row = Math.floor(at / D.columns);
        const inRow = Math.min(D.columns, members.length - row * D.columns);
        const rowW = inRow * D.nodeW + (inRow - 1) * gapX;
        const left = D.pad + (bandW - rowW) / 2;
        place.set(i, {
          x: left + (at % D.columns) * (D.nodeW + gapX),
          y: top + D.label + row * (D.nodeH + D.rowGap),
        });
      });
      const height = D.label + rows * D.nodeH + (rows - 1) * D.rowGap + D.inset;
      const band = { zone, members, top, height };
      top += height + D.bandGap;
      return band;
    });
    const height = top - D.bandGap + D.pad;

    const root = svg("svg", {
      viewBox: `0 0 ${D.width} ${height}`,
      role: "img",
      "aria-label": `Diagram of the system model: ${counted(nodes.length, "element", "elements")}, ` +
        `${counted(flows.length, "flow", "flows")} and ${counted(zones.filter(z => z.members.length).length, "trust zone", "trust zones")}. ` +
        "The table below lists the same elements.",
    });
    const defs = svg("defs");
    const marker = svg("marker", { id: "model-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5,
      markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" });
    marker.append(svg("path", { d: "M0,0 L10,5 L0,10 z", class: "arrowhead" }));
    defs.append(marker);
    root.append(defs);

    drawnBands.forEach(band => {
      const group = svg("g", { class: band.zone.id === null ? "zone unplaced" : "zone",
        "data-zone": band.zone.id === null ? "" : band.zone.id });
      group.append(
        svg("rect", { x: D.pad, y: band.top, width: bandW, height: band.height, rx: 10, class: "zone-box" }),
        svg("text", { x: D.pad + 12, y: band.top + 18, class: "zone-label" }, clip(band.zone.name, 60)),
      );
      root.append(group);
    });

    // Flows under the elements, and their labels over them. Two flows between
    // one pair of elements bend apart, so neither hides the other.
    const centre = i => ({ x: place.get(i).x + D.nodeW / 2, y: place.get(i).y + D.nodeH / 2 });
    const edge = (c, toward) => {
      const dx = toward.x - c.x;
      const dy = toward.y - c.y;
      const t = Math.min(dx ? (D.nodeW / 2) / Math.abs(dx) : Infinity, dy ? (D.nodeH / 2) / Math.abs(dy) : Infinity);
      return { x: c.x + dx * t, y: c.y + dy * t };
    };
    const crossing = new Map(R.boundary_crossings.map(c => [c.flow_id, c.decided ? "crosses" : "undecided"]));
    const pairs = new Map();
    flows.forEach(f => {
      const pair = [f.source, f.destination].sort().join("|");
      pairs.set(pair, [...(pairs.get(pair) || []), f.id]);
    });
    const lines = svg("g", { class: "flows" });
    const labels = svg("g", { class: "flow-labels" });
    // Whether a point falls on an element other than the flow's two ends.
    const boxes = [...place].map(([i, p]) => ({ i, x: p.x - 6, y: p.y - 6, w: D.nodeW + 12, h: D.nodeH + 12 }));
    const onElement = (pt, ends) => boxes.some(b => !ends.includes(b.i)
      && pt.x > b.x && pt.x < b.x + b.w && pt.y > b.y && pt.y < b.y + b.h);
    const along = (p, c, q, t) => ({
      x: (1 - t) * (1 - t) * p.x + 2 * (1 - t) * t * c.x + t * t * q.x,
      y: (1 - t) * (1 - t) * p.y + 2 * (1 - t) * t * c.y + t * t * q.y,
    });
    const taken = [];
    const overlaps = (r, others) => others.some(o =>
      r.x < o.x + o.w && o.x < r.x + r.w && r.y < o.y + o.h && o.y < r.y + r.h);
    flows.forEach(f => {
      const from = index.get(f.source);
      const to = index.get(f.destination);
      const a = centre(from);
      const b = centre(to);
      const text = clip(f.name, 22);
      const labelW = text.length * 6.4 + 8;
      let d;
      let spots;
      if (from === to) {
        // A flow from an element to itself: a loop on its right side.
        const x = a.x + D.nodeW / 2;
        d = `M${x},${a.y - 12} C${x + 44},${a.y - 30} ${x + 44},${a.y + 30} ${x},${a.y + 12}`;
        spots = [{ x: x + 34, y: a.y }];
      } else {
        // The flow bends away from the other flow between the same two
        // elements, and further until it passes over no other element.
        const pair = pairs.get([f.source, f.destination].sort().join("|"));
        const base = (pair.indexOf(f.id) - (pair.length - 1) / 2) * 56;
        const length = Math.hypot(b.x - a.x, b.y - a.y) || 1;
        const curve = bend => {
          const c = {
            x: (a.x + b.x) / 2 - ((b.y - a.y) / length) * bend,
            y: (a.y + b.y) / 2 + ((b.x - a.x) / length) * bend,
          };
          return { c, p: edge(a, c), q: edge(b, c) };
        };
        const clear = ({ p, c, q }) => [0.15, 0.3, 0.45, 0.6, 0.75, 0.9]
          .every(t => !onElement(along(p, c, q, t), [from, to]));
        const { p, c, q } = [0, 60, -60, 120, -120, 180, -180]
          .map(extra => curve(base + extra)).find(clear) || curve(base);
        d = `M${p.x},${p.y} Q${c.x},${c.y} ${q.x},${q.y}`;
        // Where its label may sit: along the flow, then just beside it.
        // A flow that runs across takes its label on the side it bends to.
        const level = Math.abs(b.x - a.x) > Math.abs(b.y - a.y);
        const down = c.y > (a.y + b.y) / 2;
        const offsets = down ? [14, 32, 48, -12, -30, -46] : [-12, -30, -46, 14, 32, 48];
        spots = [0.5, 0.35, 0.65, 0.25, 0.75].flatMap(t => {
          const at = along(p, c, q, t);
          return level
            ? offsets.map(dy => ({ x: at.x, y: at.y + dy }))
            : [at, { x: at.x, y: at.y - 14 }];
        });
      }
      const box = (spot) => ({ x: spot.x - labelW / 2, y: spot.y - 9, w: labelW, h: 16 });
      const inside = (r) => r.x >= 0 && r.y >= 0 && r.x + r.w <= D.width && r.y + r.h <= height;
      const spot = spots.find(each => inside(box(each)) && !overlaps(box(each), [...boxes, ...taken]))
        || spots.find(each => inside(box(each))) || spots[0];
      taken.push(box(spot));
      const path = svg("path", { d, class: `flow ${crossing.get(f.id) || ""}`.trim(),
        "marker-end": "url(#model-arrow)", "data-flow": f.id });
      path.append(svg("title", {}, `${f.name}: ${NAMES[f.id] || f.id}`));
      lines.append(path);
      const { x, y, w, h } = box(spot);
      labels.append(
        svg("rect", { x, y, width: w, height: h, rx: 3, class: "flow-label-box" }),
        svg("text", { x: spot.x, y: spot.y + 3, class: "flow-label" }, text),
      );
    });
    root.append(lines);

    drawnBands.forEach(band => band.members.forEach(i => {
      const { kind, e } = nodes[i];
      const { x, y } = place.get(i);
      const group = svg("g", { class: `element ${kind}`, "data-element": e.id,
        "data-zone": band.zone.id === null ? "" : band.zone.id });
      const assumed = ASSUMED_ZONES.has(e.id);
      group.append(svg("title", {}, `${e.name} (${KIND_WORDS[kind]})${assumed ? ", zone assumed" : ""}`));
      if (kind === "data-store") {
        group.append(
          svg("rect", { x, y, width: D.nodeW, height: D.nodeH, class: "element-fill" }),
          svg("path", { d: `M${x},${y} H${x + D.nodeW} M${x},${y + D.nodeH} H${x + D.nodeW}`, class: "element-line" }),
        );
      } else {
        group.append(svg("rect", { x, y, width: D.nodeW, height: D.nodeH,
          rx: kind === "process" ? 18 : 2, class: "element-box" }));
      }
      const words = wrapped(e.name, 18);
      const all = assumed ? [...words, "zone assumed"] : words;
      const label = svg("text", { x: x + D.nodeW / 2, y: y + D.nodeH / 2 - (all.length - 1) * 7 + 4,
        class: "element-label" });
      all.forEach((line, at) => label.append(svg("tspan", {
        x: x + D.nodeW / 2, dy: at ? 15 : 0, class: at >= words.length ? "assumed" : "",
      }, line)));
      group.append(label);
      root.append(group);
    }));
    root.append(labels);
    return root;
  }
  const diagram = drawModel(R.system_model);
  if (diagram) {
    const caption = el("figcaption", "meta",
      "A dashed box is a trust zone. A rectangle is an external entity, a rounded " +
      "box a process, and two lines a data store. A thick arrow crosses a trust " +
      "boundary, and a dotted arrow may cross one, because a zone is not stated.");
    $("model-diagram").append(diagram, caption);
  } else {
    $("model-diagram").hidden = true;
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

  // What the model leaves unstated, on the most elements first. An element
  // whose fact this report asks links to its question.
  const GAPS = JSON.parse($("gaps").textContent);
  GAPS.forEach(g => {
    const words = NAMES[g.attribute] || g.attribute;
    const count = g.elements.length;
    const row = el("div", "gap");
    row.append(el("b", null, words[0].toUpperCase() + words.slice(1)),
      ` is not stated for ${count} ${count === 1 ? "element" : "elements"}: `);
    g.elements.forEach((e, i) => {
      if (i) row.append(", ");
      row.append(ref(e.id));
      if (QUESTION_ROWS.has(JSON.stringify(e.key)) && ASKS) {
        const answer = el("a", null, "Answer");
        answer.href = "?view=questions";
        answer.addEventListener("click", (event) => {
          event.preventDefault();
          goToQuestion(e.key);
        });
        row.append(" (", answer, ")");
      }
    });
    $("model-gaps").append(row);
  });
  if (!GAPS.length) $("model-gaps").append(el("div", "meta", "The system model states every attribute."));

  // pipeline
  R.nodes.forEach(n => {
    const row = el("div","node");
    row.append(
      el("b", null, n.node), " ",
      el("span", "m", n.model ? n.model : "code"), ` · ${n.duration_ms} ms`
    );
    if (n.execution_fingerprint) {
      // The whole value, to copy: sha256 of the requested route, the served
      // build, the resolved tier sampling, the instruction digest and the
      // build versions.
      const fp = el("code", "m fingerprint", n.execution_fingerprint);
      fp.title = "execution-identity fingerprint";
      row.append(" · fingerprint ", fp);
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

  // ---- The complete export ---------------------------------------------------
  // The export is this page as one document: every section and finding open,
  // each question with its findings and its history, and About the analysis
  // as an appendix. It reads the same data, and it leaves out the editors: a
  // link says where to answer the questions online. A print is the export.
  const exportQuestions = $("export-questions");
  exportQuestions.append(el("h2", "view-title", FINAL ? "Answers this report read" : "Questions"));
  if (ASKS) {
    const online = location.origin + location.pathname + "?view=questions";
    const note = el("p", "meta", "To answer these questions, open this report online: ");
    const link = el("a", null, online);
    link.href = online;
    note.append(link);
    exportQuestions.append(note);
  }
  const exported = [
    ...LINK_QUESTIONS.map(q => ({ group: "Match each user, team or account to a part of your system", label: q.principal })),
    ...FACT_QUESTIONS.map(q => ({ group: q.group_heading, label: q.label, history: q.history, findings: q.findings })),
    ...(FINAL ? (CORRECTIONS.answers || []) : EARLIER_FACTS).map(a => ({
      group: "Answers this report read", label: a.label, answer: answerText(a.answer) })),
  ];
  const byGroup = new Map();
  exported.forEach(q => {
    if (!byGroup.has(q.group)) byGroup.set(q.group, []);
    byGroup.get(q.group).push(q);
  });
  byGroup.forEach((questions, group) => {
    exportQuestions.append(el("h3", null, group));
    questions.forEach(q => {
      const item = el("div", "export-question");
      item.append(el("b", null, q.label));
      if (q.answer) item.append(el("div", "meta", `Answer: ${q.answer}`));
      if (q.history) item.append(el("div", "meta", HISTORY[q.history] || q.history));
      if ((q.findings || []).length) {
        item.append(el("div", "meta", "Used by: " +
          q.findings.map(f => (FINDINGS.get(f) || { id: f }).id).join(" \u00b7 ")));
      }
      exportQuestions.append(item);
    });
  });
  if (!exported.length) exportQuestions.append(el("p", "meta", "This report asks no questions."));

  // Entering the export opens what was closed and shows every section;
  // leaving it closes those again and returns to the section the reader was
  // in, so the reader's own view comes back as it was.
  let beforeExport = null;
  const enterExport = () => {
    if (beforeExport) return;
    beforeExport = {
      section: SECTIONS.find(name => !$(`${name}-view`).hidden) || "report",
      closed: [...document.querySelectorAll("details:not([open])")],
    };
    document.body.classList.add("exporting");
    beforeExport.closed.forEach(d => { d.open = true; });
    $("report-view").hidden = false;
    $("questions-view").hidden = true;
    exportQuestions.hidden = false;
    $("about-view").hidden = false;
    $("preview-bar").hidden = false;
  };
  const leaveExport = () => {
    if (!beforeExport) return;
    beforeExport.closed.forEach(d => { d.open = false; });
    document.body.classList.remove("exporting");
    exportQuestions.hidden = true;
    $("preview-bar").hidden = true;
    const { section } = beforeExport;
    beforeExport = null;
    showSection(section);
  };
  $("export-preview").addEventListener("click", () => {
    enterExport();
    $("export-close").focus();
  });
  $("export-close").addEventListener("click", () => {
    leaveExport();
    $("export-preview").focus();
  });
  $("export-print").addEventListener("click", () => window.print());
  // A print outside the preview prints the export too, and leaves it after.
  let printedExport = false;
  window.addEventListener("beforeprint", () => {
    printedExport = !beforeExport;
    enterExport();
  });
  window.addEventListener("afterprint", () => {
    if (printedExport) leaveExport();
    printedExport = false;
  });

  // The section ?view= names, and a link from elsewhere lands on its target,
  // opened.
  const view = new URLSearchParams(location.search).get("view");
  showSection(SECTIONS.includes(view) ? view : "report");
  const landed = location.hash && document.getElementById(location.hash.slice(1));
  if (landed) {
    const holder = SECTIONS.find(name => $(`${name}-view`).contains(landed));
    if (holder) showSection(holder);
    openTo(landed);
    landed.scrollIntoView();
  }

  // theme toggle
  $("themebtn").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme");
    const dark = cur ? cur === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.setAttribute("data-theme", dark ? "light" : "dark");
  });

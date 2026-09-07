  // Every string in R is untrusted: model-authored from the submitter's own
  // prose, or supplied by the caller. None of it is escaped for HTML.
  //
  // So nothing on this page interpolates a value into innerHTML. Text goes in
  // as textContent and structure is built as DOM nodes — which is why there is
  // no escape helper here to forget to call. That discipline had already
  // failed once, unnoticed, in the element table's attribute column. Forgetting
  // `textContent` now shows junk on screen instead of executing script.
  //
  // `append` is the primitive that makes this cheap: it takes nodes and
  // strings, and a string always becomes a text node, never markup.
  const R = JSON.parse(document.getElementById("report").textContent);
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const code = (text) => el("code", null, text);
  // A model writes an identifier the way the prompt hands it over: in
  // backticks. Each of those spans becomes a `code` element here, so a
  // description names `process:web-api` in the same face the element table
  // below shows it in, rather than printing the delimiters as characters.
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
  const prose = (text) => {
    const line = String(text);
    const frag = document.createDocumentFragment();
    let end = 0;
    for (const span of line.matchAll(CODE_SPAN)) {
      frag.append(line.slice(end, span.index), code(span[1]));
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

  // Each branch named in its own words, so the four read as different *kinds*
  // of justification rather than four formattings of one. The two attribute
  // branches carry identical fields, so this line is the only place a reader
  // can tell "nobody said" from "somebody said no".
  const GROUND_KIND = {
    "quote": "Quoted from the submission",
    "unknown-attribute": "Unstated in the submission",
    "absent-attribute": "Stated absent in the submission",
    "derived-fact": "Derived from the model",
  };
  // Past this, a quote is clamped to three lines behind a toggle. Short quotes
  // are the common case and get no affordance.
  const CLAMP_OVER = 220;

  // Every mark now sits in the block whose claims it points at, so these are
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
    return { unverified, repaired, references, mentions, composed, unmitigated, unknown, groundless };
  }

  const SEV = { critical: ["Critical","--sev-critical"], high: ["High","--sev-high"], medium: ["Medium","--sev-medium"], low: ["Low","--sev-low"] };
  const SEV_ORDER = ["critical","high","medium","low"];
  const VERDICT = { confirmed: ["Confirmed","✓"], "needs-info": ["Needs info","?"], rejected: ["Rejected","✕"] };
  const svar = (lvl) => `var(${SEV[lvl][1]})`;

  // header
  $("sysname").textContent = R.input.system_name;
  // The static title names no system and no framework. The report names
  // both, so the tab reads the run rather than the template.
  document.title = R.input.system_name + " — report";
  const fmt = (t) => new Date(t).toISOString().replace("T"," ").replace(".000Z"," UTC");
  $("jobmeta").append(
    "Job ", code(R.job.id), ` · ${R.job.status} · ${fmt(R.job.completed_at)} · schema ${R.schema_version}`
  );
  // The envelope's disclaimer says what the *service* is. Each block carries
  // its own, saying what that framework's claims assert — a different sentence
  // the moment a report carries a framework that rules on requirement
  // applicability rather than on attacks.
  $("disclaimer").textContent = R.disclaimer;

  const frameworks = R.analyses.map(b => b.framework).join(", ");
  $("scope").textContent =
    `${R.elements_analyzed} elements analysed under ${frameworks}`;

  // One grounds entry. Every string here is model-authored or lifted verbatim
  // out of the submitter's own prose, so it goes in as text and never as markup.
  //
  // Grounds are the neutral half of a claim: the three branches are properties
  // of the shared System Model and of the submission, not of any framework's
  // method, so this renders identically in every block.
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
      cite.append(unverified ? `\u26a0 not found in ${ground.source_label}`
                             : `\u2014 ${ground.source_label}`);
      row.append(cite);
      const repaired = marks.repaired.get(`${claimId}#${index}`);
      if (repaired) {
        const note = el("div", "cite unverified");
        note.append(`\u270e replaced the agent's wording (similarity ${repaired.similarity}): `);
        note.append(document.createTextNode(repaired.written));
        row.append(note);
      }
      return row;
    }
    if (ground.kind === "unknown-attribute" || ground.kind === "absent-attribute") {
      body.append(code(ground.element_id), " \u2192 ", code(ground.attribute));
    } else {
      body.append(code(ground.flow_id));
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
    // The lane, where the framework stamps one. Each package names the field
    // itself — STRIDE's is its category, ASVS's is its chapter — so this reads
    // the ones it knows and a framework it does not know renders without a lane
    // chip rather than not rendering.
    const lane = t.category || t.chapter;
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

    // Directly under the argument it qualifies. The prose above cites an
    // element this report does not describe, which a reader following the ID
    // into the model table below would otherwise discover by finding nothing.
    const references = marks.references.get(t.id);
    if (references && references.length) {
      const note = el("div", "caveat");
      note.append("\u26a0 Named as affected but not in the system model, so dropped from this claim: ");
      references.forEach((m, i) => { if (i) note.append(", "); note.append(code(m)); });
      card.append(note);
    }
    const mentions = marks.mentions.get(t.id);
    if (mentions && mentions.length) {
      const note = el("div", "caveat");
      note.append("\u26a0 Cited above but not in the system model: ");
      mentions.forEach((m, i) => { if (i) note.append(", "); note.append(code(m)); });
      card.append(note);
    }

    // Beside it, and deliberately worded as a citation failure rather than a
    // doubt about the finding: the grounds shown below are the ones that did
    // resolve, and they are why this claim is still here.
    const composed = marks.composed.get(t.id);
    if (composed && composed.length) {
      const note = el("div", "caveat");
      note.append("\u26a0 Cited evidence not in this job's catalog, and dropped: ");
      composed.forEach((r, i) => { if (i) note.append(", "); note.append(code(r)); });
      card.append(note);
    }

    if (t.severity) {
      const rationale = el("div","field");
      rationale.append(lbl("Severity rationale"), el("br"), prose(t.severity.justification));
      card.append(rationale);
    }

    const refs = el("div","field refs");
    refs.append(lbl("Affected elements"), el("br"));
    t.affected_element_ids.forEach(r => refs.append(code(r)));
    card.append(refs);

    // After the analysis, not before it: the card's job on first read is
    // triage, and attribution is what you turn to once a finding has your
    // attention.
    card.append(groundsBlock(marks, t));

    // A needs-info banner may repeat an element/attribute pair that also
    // appears as an unknown-attribute ground above. Both stay: the ground is
    // the lane agent's *trigger*, the banner is the critic's citation for its
    // *verdict*. Different authors, and this block's whole value is that it
    // says who justified what.
    if (t.verdict.status === "needs-info" && t.verdict.related_unknowns.length) {
      const u = el("div","unknown");
      u.append(el("b", null, "Needs info."), " ", prose(t.verdict.reason), " Unknown: ");
      t.verdict.related_unknowns.forEach((r, i) => {
        if (i) u.append(", ");
        u.append(code(r.element_id), " \u2192 ", code(r.attribute));
      });
      card.append(u);
    }
    if (t.verdict.status === "rejected") {
      const why = el("div","field");
      why.append(lbl("Reason dismissed"), el("br"), prose(t.verdict.reason));
      card.append(why);
    }
    if (t.mitigations && t.mitigations.length) {
      const list = el("ul","mits"); t.mitigations.forEach(m => list.append(proseEl("li", null, m.summary)));
      const wrap = el("div","field"); wrap.append(el("div","lbl","Mitigations"), list); card.append(wrap);
    } else if (marks.unmitigated.has(t.id)) {
      // Where the Mitigations block would have been, so its absence is stated
      // rather than left as a gap the reader has to notice.
      card.append(el("div", "caveat",
        "\u26a0 No mitigation proposed, and this finding does not rest on an unknown that would explain why."));
    }
    return card;
  }

  // One framework's whole section: its heading, its disclaimer, its own counts,
  // and its two claim arrays. Nothing here reaches back into the envelope
  // except for the element count, which is a fact about the one shared model
  // and so is stated once at the top rather than N times.
  function renderBlock(block) {
    const marks = marksOf(block);
    const section = el("section", "analysis");
    section.append(el("h2", null, `${block.framework} \u00b7 v${block.framework_version}`));
    section.append(el("div", "disclaimer-block", block.disclaimer));

    const tiles = el("div", "tiles");
    [
      [block.summary.claim_count, "Actionable claims"],
      [block.summary.needs_info_count, "Needs info"],
      [block.summary.rejected_count, "Rejected"],
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
        present.forEach(l => {
          const item = el("div");
          const swatch = el("span", "swatch");
          swatch.style.background = svar(l);
          item.append(swatch, `${SEV[l][0]} \u00b7 ${by[l]}`);
          legend.append(item);
        });
        wrap.append(mix, legend);
        section.append(wrap);
      }
    }

    // Severity order where the framework grades, and the block's own order
    // otherwise — which is the package's declared lane order, and is the only
    // ranking a framework that grades nothing has.
    const claims = [...block.claims];
    if (claims.every(c => c.severity)) {
      claims.sort((a,b) => SEV_ORDER.indexOf(a.severity.level) - SEV_ORDER.indexOf(b.severity.level));
    }
    section.append(el("h3", null, "Claims"));
    if (claims.length) {
      claims.forEach(t => section.append(claimCard(marks, t, false)));
    } else {
      // Said rather than left blank. A framework that examined the system and
      // raised nothing is a result; an empty heading reads as a rendering fault.
      section.append(el("div", "meta", "No claims were raised under this framework."));
    }
    // Only where there are any. Every block carries this heading otherwise, and
    // on a report with two frameworks that is two empty sections a reader has
    // to scroll past to reach the model.
    if (block.rejected_claims.length) {
      section.append(el("h3", null, "Rejected \u2014 considered and dismissed"));
      block.rejected_claims.forEach(t => section.append(claimCard(marks, t, true)));
    }
    $("analyses").append(section);
  }

  // In the job's own selection order, which the envelope has already checked
  // against `job.frameworks`.
  R.analyses.forEach(renderBlock);

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
      "Boundary crossing: ", code(c.flow_id), " — ",
      code(c.source_zone), " → ", code(c.destination_zone)
    );
    $("crossings").append(d);
  });
  R.system_model.assumptions.forEach(a => {
    const d = el("div","assume");
    d.append(`Assumption: ${a.assumption} (`, code(a.element_id), `) — ${a.basis}`);
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

// The input for one answer, as every page that takes answers builds it: the
// report page's follow-up and corrections, and the pause page's rounds. Each
// page keeps its own layout and its own rule for what it sends; the inputs and
// the answer each one reads are built here, once.
//
// The answer limits the service admits (answer_round.ANSWER_LIMITS), handed to
// every page that takes answers.
const ANSWER_LIMITS = JSON.parse(document.getElementById("answer_limits").textContent);
// The answer that says the submitter does not know. The service writes
// nothing for it, so the fact stays open.
const DONT_KNOW = "unknown";
// The answers a facet takes, as the service lists them in FACET_ANSWERS.
const FACET_CHOICES = [
  ["yes", "yes"], ["no", "no"], ["not applicable", "not applicable"],
  ["I don't know", DONT_KNOW],
];
// Whether an input offers "I don't know" where it starts from `before`. With
// `reopen` false, a known value offers none: the service refuses an answer
// that reopens a settled fact or a settled facet, except at a job's pause and
// in a correction (fact_writes.check_fact_answers).
const offersDontKnow = (before, reopen) => reopen || !before || before === DONT_KNOW;
const optionOf = (label, value) => {
  const choice = document.createElement("option");
  choice.value = value;
  choice.textContent = label;
  return choice;
};
// The submitter's own words beside a closed answer: an exception or a scope the
// answer alone would overstate (FactAnswer.detail, ADR 0073).
const answerDetail = (value) => {
  const detail = document.createElement("input");
  detail.type = "text";
  detail.maxLength = ANSWER_LIMITS.detail;
  detail.className = "answer-detail";
  detail.placeholder = "exceptions or detail (optional)";
  detail.value = value;
  return detail;
};
const withDetail = (answer, detail) => (detail ? { ...answer, detail } : answer);
// One facet's select, starting from `before`. `blank` labels the empty option,
// or is null for no empty option.
const facetSelect = (blank, before, reopen) => {
  const select = document.createElement("select");
  if (blank != null) select.append(optionOf(blank, ""));
  for (const [label, value] of FACET_CHOICES) {
    if (value !== DONT_KNOW || offersDontKnow(before, reopen)) select.append(optionOf(label, value));
  }
  select.value = before;
  return select;
};
let suggestLists = 0;
// The input for one answer that is not a facet table: a select of `choices`
// with a detail beside it, a control's none / don't know / mechanism, or a
// line of text with an "I don't know" box. `choices` are `{ id, name }`;
// `prefill` is an earlier answer to start from, or null; `changed` runs on
// every edit.
//
// `nodes` follow the label. `read` is the answer's value, or "" for none;
// `set` writes a value into the input; `answer` is the answer as the service
// takes it, or null; `known` is whether it says more than "I don't know".
const answerEditor = (q, { choices, prefill, reopen, changed = () => {} }) => {
  const value = (prefill && prefill.value) || "";
  const dontKnowOffered = offersDontKnow(value, reopen);
  let input;
  let read = () => input.value.trim();
  let set;
  let nodes;
  // A closed answer takes a detail beside it; free text needs none.
  let detail = null;
  if (choices.length) {
    input = document.createElement("select");
    input.append(optionOf("(leave unanswered)", ""));
    for (const { id, name } of choices) input.append(optionOf(name ? `${name} (${id})` : id, id));
    if (dontKnowOffered) input.append(optionOf("I don't know", DONT_KNOW));
    input.addEventListener("change", changed);
    set = (next) => { input.value = next; };
    detail = answerDetail((prefill && prefill.detail) || "");
    detail.addEventListener("input", changed);
    nodes = [input, " ", detail];
  } else if (q.form === "control") {
    // A control: say there is none, say you do not know, or name the
    // mechanism. The suggestions are a start; the text is the answer.
    input = document.createElement("input");
    input.type = "text";
    input.maxLength = q.max_length;
    input.placeholder = "type it, or pick a common one";
    const list = document.createElement("datalist");
    list.id = `answer-suggest-${suggestLists++}`;
    for (const suggestion of q.suggestions) list.append(optionOf(suggestion, suggestion));
    input.setAttribute("list", list.id);
    const state = document.createElement("select");
    state.append(optionOf("(leave unanswered)", ""), optionOf("There is none", "none"));
    if (dontKnowOffered) state.append(optionOf("I don't know", DONT_KNOW));
    state.append(optionOf("A mechanism, in my own words:", "mechanism"));
    // The state is the answer: blank sends nothing, and the text is read
    // only under "mechanism". Typing a mechanism chooses it.
    const fix = () => { input.disabled = state.value === "none" || state.value === DONT_KNOW; };
    state.addEventListener("change", () => {
      fix();
      changed();
    });
    input.addEventListener("input", () => {
      if (input.value.trim()) state.value = "mechanism";
      changed();
    });
    set = (next) => {
      const fixed = next === "none" || next === DONT_KNOW;
      state.value = fixed || !next ? next : "mechanism";
      input.value = fixed ? "" : next;
      fix();
    };
    read = () => (state.value === "mechanism" ? input.value.trim() : state.value);
    nodes = [state, " ", input, list];
  } else {
    input = document.createElement("input");
    input.type = "text";
    input.maxLength = q.max_length;
    input.placeholder = "(leave unanswered)";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.addEventListener("change", () => {
      input.value = box.checked ? DONT_KNOW : "";
      input.disabled = box.checked;
      changed();
    });
    input.addEventListener("input", changed);
    const dontKnow = document.createElement("label");
    dontKnow.append(box, " I don't know");
    set = (next) => {
      input.value = next;
      box.checked = input.disabled = next === DONT_KNOW;
    };
    nodes = dontKnowOffered ? [input, " ", dontKnow] : [input];
  }
  set(value);
  input.dataset.key = JSON.stringify(q.key);
  const readDetail = () => (detail ? detail.value.trim() : "");
  return {
    input,
    nodes,
    read,
    set,
    readDetail,
    answer: () => (read() ? withDetail({ key: q.key, value: read() }, readDetail()) : null),
    known: () => Boolean(read()) && read() !== DONT_KNOW,
  };
};

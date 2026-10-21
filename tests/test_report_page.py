"""The whole report page, run under ``node`` against a page the service rendered.

Where the other harnesses cut one block out of the script, this one runs all
of it, so the claim cards, their grounds and the page's summaries are checked
as a person would read them (#1289, PR 4).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from analysis_service.claims import UnknownRef
from analysis_service.fact_answers import FactAnswer
from tests.factories import (
    asking_threat,
    report_state,
    sample_report,
    sample_threat,
    valid_model,
)
from webapp.main import render_report

_SHIM = r"""
class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.dataset = {};
    this.style = {}; this.listeners = {}; this.attributes = {};
    this.classList = { add() {}, remove() {}, toggle() { return true; }, contains() { return false; } }; }
  append(...kids) { this.children.push(...kids); }
  prepend(...kids) { this.children.unshift(...kids); }
  appendChild(kid) { this.children.push(kid); return kid; }
  insertBefore(kid) { this.children.push(kid); return kid; }
  replaceChildren(...kids) { this.children = [...kids]; }
  set textContent(t) { this.children = [String(t)]; }
  get textContent() {
    return this.children.map(c => typeof c === "object" ? c.textContent : String(c)).join("");
  }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
  querySelector() { return this._query || (this._query = new Node("query")); }
  querySelectorAll() { return []; }
  closest() { return new Node("closest"); }
  remove() {}
}
const payloads = PAYLOADS;
const ids = {};
globalThis.document = {
  getElementById: (id) => id in payloads
    ? Object.assign(new Node("script"), { children: [payloads[id]] })
    : (ids[id] = ids[id] || new Node(id)),
  createElement: (tag) => new Node(tag),
  createTextNode: (t) => String(t),
  createDocumentFragment: () => new Node("#fragment"),
  querySelector: () => new Node("query"),
  querySelectorAll: () => [],
  body: new Node("body"),
};
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.location = { pathname: "/report/r1", href: "" };
"""


def run_report_page(html: str, search: str = "") -> dict[str, str]:
    """The text of each part of the page, once its script has run over ``html``
    at the query ``search``. ``hidden`` lists the parts the script hid."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on PATH to run the report page")
    payloads = dict(
        re.findall(
            r'<script type="application/json" id="([^"]+)"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
    )
    (script,) = re.findall(r"<script nonce=\"[^\"]*\">(.*?)</script>", html, re.DOTALL)
    program = (
        _SHIM.replace("PAYLOADS", json.dumps(payloads))
        + f"location.search = {json.dumps(search)};\n"
        + script
        + "\nconsole.log(JSON.stringify({...Object.fromEntries("
        "Object.entries(ids).map(([id, n]) => [id, n.textContent])),"
        " hidden: Object.keys(ids).filter(id => ids[id].hidden).join(' ')}));"
    )
    done = subprocess.run(
        [node, "-"],
        input=program,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


ASKED = UnknownRef(
    element_id=valid_model().data_flows[1].id, attribute="encryption_in_transit"
)


def page(answered=(), shown=(), final=False, skipped=()):
    report = sample_report([asking_threat(ASKED)])
    return render_report(
        report,
        report_state(
            report, answered=answered, final=final, shown=shown, skipped=skipped
        ),
    ).html


def test_the_page_runs_whole():
    parts = run_report_page(page())
    assert "Needs info." in parts["analyses"]


def waits_line(parts):
    """The banner's line for the one fact the finding waits on."""
    (line,) = re.findall(r"encrypted\? — ([^.]*?)(?=Affected)", parts["analyses"])
    return line


@pytest.mark.parametrize(
    ("answered", "shown", "why"),
    [
        ([], [], "not asked yet"),
        ([], [ASKED.key], "shown before the analysis and left blank"),
        (
            [FactAnswer(key=ASKED.key, value="unknown")],
            [],
            'nobody knew: an answer said "I don\'t know"',
        ),
        (
            [FactAnswer(key=ASKED.key, value="TLS 1.3")],
            [],
            "you answered it, and the analysis still did not find it settled",
        ),
    ],
    ids=["open", "unanswered", "unknown", "answered"],
)
def test_a_conditional_finding_says_why_each_fact_is_still_open(answered, shown, why):
    """The banner named an element and an attribute and nothing more (#1289, PR 4)."""
    parts = run_report_page(page(answered=answered, shown=shown))
    assert waits_line(parts) == why
    assert "neither confirmed nor cleared" in parts["analyses"]


def test_a_skipped_fact_is_told_apart_from_a_blank_one():
    """Only the submitter's own skip reads as skipped (#1542 F4)."""
    parts = run_report_page(page(shown=[ASKED.key], skipped=[ASKED.key]))
    assert waits_line(parts) == "skipped before the analysis"


def two_findings():
    """A report of one confirmed finding and one that waits on ``ASKED``."""
    waiting = asking_threat(ASKED).model_copy(
        update={"id": "S-02", "title": "Cookies cross the network in clear"}
    )
    report = sample_report([sample_threat(), waiting])
    return render_report(report, report_state(report)).html


def test_the_summary_counts_the_confirmed_and_the_waiting_findings():
    """One summary per block, in place of the tiles and the severity bar."""
    text = run_report_page(two_findings())["analyses"]
    assert "1 confirmed finding (1 high)." in text
    assert "1 finding needs an answer to 1 open question. Answer the questions" in text
    assert "Actionable claims" not in text


def test_the_findings_table_lists_each_finding_with_its_first_step():
    """A row per finding, before the cards: the confirmed one first, with its
    first mitigation, and the waiting one with the question it waits on."""
    text = run_report_page(two_findings())["analyses"]
    head = "SeverityFindingStatusAffectedFirst next step"
    assert head in text
    table = text[text.index(head) : text.index("AI-generated STRIDE")]
    confirmed = table.index("Session cookie theft enables account takeover S-01")
    waiting = table.index("Cookies cross the network in clear S-02")
    assert confirmed < waiting
    assert "\u2713 Confirmed" in table
    assert "Set HttpOnly and Secure on cookies" in table
    assert "Answer: Is the connection" in table


def test_findings_that_rest_on_one_missing_control_share_a_group(monkeypatch):
    """Two confirmed findings that cite one stated-absent control sit under one
    group, named for the control to add, and the table still lists both."""
    from analysis_service.claims import Ground
    from tests import factories

    model = valid_model()
    flow = model.data_flows[1].model_copy(update={"encryption_in_transit": "none"})
    stated = model.model_copy(update={"data_flows": [model.data_flows[0], flow]})
    monkeypatch.setattr(factories, "valid_model", lambda: stated)
    missing = Ground(
        kind="absent-attribute", element_id=flow.id, attribute="encryption_in_transit"
    )
    threats = [
        sample_threat(
            "T-01", "tampering", title="Orders change in transit", grounds=[missing]
        ),
        sample_threat(
            "I-01",
            "information-disclosure",
            title="Orders leak in transit",
            grounds=[missing],
        ),
    ]
    report = sample_report(threats)
    text = run_report_page(render_report(report, report_state(report)).html)["analyses"]
    assert (
        "Add encryption in transit to the connection from Web App to Orders DB"
        " \u2014 2 findings rest on this missing control"
    ) in text
    assert text.count("Orders change in transit") == 2


def test_the_conditional_section_says_its_guidance_once():
    """The cards under a question carry no copy of the section's guidance."""
    text = run_report_page(page())["analyses"]
    assert text.count("neither confirmed nor cleared") == 1
    assert text.count("Confirm each fact with the people who run the component") == 1
    assert "Questions that would settle the conditional findings" in text
    assert "an answer alone covers 1 Answer" in text


def test_the_findings_view_hides_how_the_report_was_made():
    parts = run_report_page(two_findings())
    assert parts["hidden"].split() == ["provenance-view"]
    assert parts["jobmeta"].endswith("How this report was made")


def test_the_provenance_view_holds_the_model_and_the_pipeline():
    """The service disclaimer, the element count, the System Model and the
    pipeline move to ?view=provenance, which hides the findings."""
    parts = run_report_page(two_findings(), search="?view=provenance")
    assert parts["hidden"].split() == ["report-view"]
    assert parts["jobmeta"].endswith("Back to the findings")
    assert parts["disclaimer"].startswith("AI-generated security analysis.")
    assert "elements in the model" in parts["scope"]


def test_the_next_step_follows_whether_the_report_is_final():
    follow_up = run_report_page(page())["analyses"]
    final = run_report_page(page(final=True))["analyses"]
    assert "answer the questions in the follow-up below" in follow_up
    assert "correct an answer below or submit the description again" in final


def test_a_corrected_finding_is_marked_on_its_card():
    """The correction mark (#1355) reaches the finding's card."""
    report = sample_report([asking_threat(ASKED)])
    html = render_report(
        report,
        report_state(
            report,
            answered=[FactAnswer(key=ASKED.key, value="TLS 1.3")],
            final=True,
            corrections=[FactAnswer(key=ASKED.key, value="unknown")],
        ),
    ).html
    assert "Corrected after this report" in run_report_page(html)["analyses"]


def test_an_owner_s_answer_is_marked_as_unchecked():
    """A quote of the owner's answer read like the description (#1289, PR 4)."""
    from analysis_service.claims import Ground
    from analysis_service.fact_answers import fact_line
    from analysis_service.sources import ANSWERS_LABEL
    from tests.factories import sample_threat

    answer = FactAnswer(key=ASKED.key, value="TLS 1.3")
    quote = Ground(kind="quote", text=fact_line(answer), source_label=ANSWERS_LABEL)
    derived = sample_threat().grounds[1]
    report = sample_report([sample_threat(grounds=[quote, derived])])
    html = render_report(report, report_state(report, answered=[answer])).html
    text = run_report_page(html)["analyses"]
    assert "your answer; the service did not check it" in text
    assert f"— {ANSWERS_LABEL}" not in text


def test_every_reason_a_fact_is_open_has_a_line_on_the_page():
    """The page's WHY_OPEN table and FactStatus are one set."""
    from typing import get_args

    from analysis_service.report_conditions import FactStatus
    from tests.test_webapp import viewer_javascript

    table = re.search(
        r"const WHY_OPEN = \{(.*?)\n  \};", viewer_javascript(), re.DOTALL
    )
    keys = set(re.findall(r"^\s*(\w+):", table.group(1), re.MULTILINE))
    assert keys == set(get_args(FactStatus))


@pytest.mark.parametrize("step", [None, "evidence", "reasoning", "lane", "duplicate"])
def test_the_page_rules_out_a_unit_where_the_claim_rules_on_it(step):
    """The page's ``rulesOut`` and ``RuledClaim.rules_on_unit`` are two readers of
    one rule, so each rejection cause is put to both."""
    from types import SimpleNamespace
    from typing import get_args

    from analysis_service.claims import RejectionStep, RuledClaim
    from tests.test_webapp import viewer_javascript

    assert set(get_args(RejectionStep)) == {
        "evidence",
        "reasoning",
        "lane",
        "duplicate",
    }
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on PATH to run the report page's script")
    javascript = viewer_javascript()
    rule = "\n".join(
        re.search(rf"const {name} = .*?;\n", javascript, re.DOTALL)[0]
        for name in ("answersInUnits", "rulesOut")
    )
    verdict = {"status": "rejected", "rejected_because": step}
    program = (
        f"const UNITS = {{asvs: ['1.1.1']}};\n{rule}\n"
        f"console.log(JSON.stringify(rulesOut("
        f"{{framework: 'asvs'}}, {{verdict: {json.dumps(verdict)}}})));"
    )
    done = subprocess.run(
        [node, "-e", program], capture_output=True, text=True, timeout=30, check=False
    )
    assert done.returncode == 0, done.stderr

    claim = SimpleNamespace(verdict=SimpleNamespace(**verdict))
    assert json.loads(done.stdout) is RuledClaim.rules_on_unit(claim)


def test_the_page_names_the_attribute_ground_kinds_the_claims_name():
    """The page lists the attribute ground kinds again where it draws a
    ground, so its list is held to ``ATTRIBUTE_GROUNDS``."""
    from analysis_service.claims import ATTRIBUTE_GROUNDS
    from tests.test_webapp import viewer_javascript

    branch = re.search(
        r"if \(((?:ground\.kind === \"[\w-]+\"(?: \|\| )?)+)\) \{\s*"
        r"body\.append\(ref\(ground\.element_id\)",
        viewer_javascript(),
    )

    assert branch is not None, "the page draws no attribute ground"
    assert set(re.findall(r'"([\w-]+)"', branch.group(1))) == ATTRIBUTE_GROUNDS


def test_a_description_names_an_element_by_its_name():
    """A finding never shows `flow:entity:customer>process:web-app>login` (#561).

    An element ID in backticks reads as the element's name, and a flow as its
    two endpoints. A span that names no element, such as an attribute, stays
    as it was written.
    """
    from tests.factories import sample_threat

    model = valid_model()
    flow = next(f for f in model.data_flows if f.id.endswith(">login"))
    by_id = {element.id: element.name for element in model.elements()}
    described = sample_threat(
        description=f"`{flow.source}` sends `{flow.id}` with `encryption_in_transit` unknown."
    )
    report = sample_report([described])
    text = run_report_page(render_report(report, report_state(report)).html)["analyses"]

    endpoints = f"{by_id[flow.source]} → {by_id[flow.destination]}"
    assert (
        f"{by_id[flow.source]} sends {endpoints} with encryption_in_transit unknown."
        in text
    )
    assert flow.id not in text


def test_a_description_names_an_evidence_reference_by_its_label():
    """A lane cites `unknown:<flow>:<attribute>`, and 158 of 201 archived
    reports showed such references raw (#561).

    The reference reads as the element and the attribute in words, and a
    crossing as the flow that crosses; the reference stays on hover.
    """
    from analysis_service.evidence import (
        crossing_evidence_ref,
        evidence_catalog,
        unknown_evidence_ref,
    )
    from tests.factories import sample_threat

    model = valid_model()
    by_id = {element.id: element.name for element in model.elements()}
    catalog = evidence_catalog(model)
    flow = next(
        f
        for f in model.data_flows
        if unknown_evidence_ref(f.id, "encryption_in_transit") in catalog
    )
    crossing = next(
        f for f in model.data_flows if crossing_evidence_ref(f.id) in catalog
    )
    unstated = unknown_evidence_ref(flow.id, "encryption_in_transit")
    crosses = crossing_evidence_ref(crossing.id)
    described = sample_threat(description=f"It rests on `{unstated}` and `{crosses}`.")
    report = sample_report([described])
    text = run_report_page(render_report(report, report_state(report)).html)["analyses"]

    def endpoints(f):
        return f"{by_id[f.source]} → {by_id[f.destination]}"

    assert (
        f"It rests on {endpoints(flow)}: encryption in transit, unstated and"
        f" {endpoints(crossing)} crosses a trust boundary." in text
    )
    assert unstated not in text
    assert crosses not in text


def _split_by_provenance(html: str) -> tuple[str, str]:
    """The analyses text outside the provenance toggle, and the text inside it."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on PATH to run the report page")
    payloads = dict(
        re.findall(
            r'<script type="application/json" id="([^"]+)"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
    )
    (script,) = re.findall(r"<script nonce=\"[^\"]*\">(.*?)</script>", html, re.DOTALL)
    walk = r"""
const text = (n) => typeof n === "object" ? n.textContent : String(n);
const split = (n, out) => {
  if (typeof n !== "object") { out.shown.push(String(n)); return; }
  if (n.tag === "details" && n.className === "prov") { out.hidden.push(text(n)); return; }
  n.children.forEach(k => split(k, out));
};
const out = { shown: [], hidden: [] };
split(ids["analyses"], out);
console.log(JSON.stringify({ shown: out.shown.join(""), hidden: out.hidden.join("") }));
"""
    program = _SHIM.replace("PAYLOADS", json.dumps(payloads)) + script + walk
    done = subprocess.run(
        [node, "-"],
        input=program,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    parts = json.loads(done.stdout.strip().splitlines()[-1])
    return parts["shown"], parts["hidden"]


def test_a_finding_shows_its_summary_and_hides_its_provenance():
    """#561's default view: what is missing, what it touches and what to do
    next. Why it matters, the grounds and the critic's reason sit behind the
    toggle, and nothing is dropped."""
    shown, hidden = _split_by_provenance(page())

    for part in ("Missing information", "Affected", "Next step"):
        assert part in shown, part
    assert "Why it matters" in hidden
    assert "Why it matters" not in shown
    assert "The critic's reason" in hidden
    assert "The sources do not state this." in hidden
    assert "The sources do not state this." not in shown
    assert "Quoted from the submission" in hidden
    assert "Quoted from the submission" not in shown


def test_a_follow_up_says_how_each_finding_moved():
    """A follow-up shows its reader what the answers did to each finding."""
    from tests.factories import sample_threat

    before = sample_report(
        [asking_threat(ASKED), sample_threat("S-02", verb="impersonate", title="Lost")]
    )
    after = sample_report([sample_threat()])
    html = render_report(after, report_state(after, final=True), before).html
    text = run_report_page(html)["analyses"]

    assert (
        "Since the report your answers came from, 2 finding(s): 1 Needs info"
        " → Confirmed; 1 no longer raised." in text
    )
    assert "Was Needs info in the earlier report." in text
    assert "Lost" in text


def test_a_first_report_says_nothing_about_changes():
    text = run_report_page(page())["analyses"]

    assert "Since the report your answers came from" not in text
    assert "in the earlier report" not in text


def test_the_page_reads_each_lane_from_the_service_s_table():
    """The page names no package's lane field: it reads ``lane_field``.

    A lane read from guessed field names counts every finding of a framework
    it does not know as one lane in the follow-up's scope line.
    """
    from analysis_service.frameworks import PACKAGES

    (lanes,) = re.findall(r'id="lanes"[^>]*>(.*?)</script>', page(), re.DOTALL)

    assert json.loads(lanes) == {
        name: package.id_rule.lane_field for name, package in PACKAGES.items()
    }


def test_every_ground_kind_renders_its_label_and_its_reference():
    """A ground of each kind shows a label and what it cites, never ``undefined``.

    A page that names only some kinds and renders the rest as a flow would
    show ``undefined`` for an assertion row or an absent term.
    """
    from typing import get_args

    from analysis_service.claims import Ground, GroundKind

    fields = {
        "quote": {"text": "a quote", "source_label": "description"},
        "unknown-attribute": {"element_id": "process:a", "attribute": "auth"},
        "absent-attribute": {"element_id": "process:a", "attribute": "auth"},
        "derived-fact": {"flow_id": "flow:x"},
        "absent-element": {"term": "directory service"},
        "assertion": {"assertion": "row-stated"},
        "unknown-assertion": {"assertion": "row-open"},
    }
    assert set(fields) == set(get_args(GroundKind))
    report = sample_report([sample_threat()])
    # Placed after validation: the page renders what it is given, and the
    # report's own rules would want a catalog and a model for every reference.
    report.analyses[0].claims[0] = (
        report.analyses[0]
        .claims[0]
        .model_copy(
            update={
                "grounds": [
                    Ground.model_construct(kind=kind, **values)
                    for kind, values in fields.items()
                ]
            }
        )
    )
    text = run_report_page(render_report(report, report_state(report)).html)["analyses"]

    assert "undefined" not in text
    for cited in ("directory service", "row-stated", "row-open"):
        assert cited in text


def _page_table_keys(name):
    """The keys of one ``const NAME = {...}`` table in the page script."""
    from tests.test_webapp import viewer_javascript

    body = re.search(
        rf"const {name} = \{{(.*?)\}};", viewer_javascript(), re.DOTALL
    ).group(1)
    return set(re.findall(r'(?:^|[,\n])\s*"?([\w-]+)"?\s*:', body))


def _scope_states():
    from analysis_service.claims import ScopeEntry

    return ScopeEntry.model_fields["state"].annotation


@pytest.mark.parametrize(
    ("table", "vocabulary"),
    [
        ("GROUND_KIND", "analysis_service.claims:GroundKind"),
        ("SEV", "analysis_service.claims:SeverityLevel"),
        ("VERDICT", "analysis_service.claims:VerdictStatus"),
        ("VERDICT_STATE", "analysis_service.claims:VerdictStatus"),
        ("SCOPE_STATE", None),
        ("CHANGE_LINE", "analysis_service.report_changes:Change"),
    ],
)
def test_each_page_table_answers_for_its_whole_vocabulary(table, vocabulary):
    """A page table keyed by a closed vocabulary holds every value of it.

    A value the table lacks renders as a raw key or as nothing.
    """
    import importlib
    from typing import get_args

    if vocabulary is None:
        values = get_args(_scope_states())
    else:
        module, name = vocabulary.split(":")
        values = get_args(getattr(importlib.import_module(module), name))

    assert _page_table_keys(table) == set(values)


def test_the_page_reads_the_service_s_severity_order():
    """The page orders severity by ``SEVERITY_ORDER`` as the server sends it.

    The page holds no order of its own, so a level the service adds lands in
    the summary and the findings table. ``SEV`` labels each level, and the
    table test holds its keys.
    """
    from analysis_service.critic import SEVERITY_ORDER
    from tests.test_webapp import viewer_javascript

    (order,) = re.findall(
        r'<script type="application/json" id="severity_order"[^>]*>(.*?)</script>',
        page(),
    )
    assert json.loads(order) == list(SEVERITY_ORDER)
    assert re.search(
        r'const SEV_ORDER = JSON\.parse\(document\.getElementById\("severity_order"\)',
        viewer_javascript(),
    )

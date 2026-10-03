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
from tests.factories import asking_threat, report_state, sample_report, valid_model
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
  querySelectorAll: () => [],
  body: new Node("body"),
};
globalThis.window = globalThis;
globalThis.location = { pathname: "/report/r1", href: "" };
"""


def run_report_page(html: str) -> dict[str, str]:
    """The text of each part of the page, once its script has run over ``html``."""
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
        + script
        + "\nconsole.log(JSON.stringify(Object.fromEntries("
        "Object.entries(ids).map(([id, n]) => [id, n.textContent]))));"
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


def page(answered=(), shown=(), final=False):
    report = sample_report([asking_threat(ASKED)])
    return render_report(
        report, report_state(report, answered=answered, final=final, shown=shown)
    ).html


def test_the_page_runs_whole():
    parts = run_report_page(page())
    assert "Needs info." in parts["analyses"]


def waits_line(parts):
    """The banner's line for the one fact the finding waits on."""
    (line,) = re.findall(
        r"encryption in transit — ([^.]*?)(?=Until)", parts["analyses"]
    )
    return line


@pytest.mark.parametrize(
    ("answered", "shown", "why", "summary"),
    [
        ([], [], "not asked yet", "1 on a fact nobody was asked"),
        ([], [ASKED.key], "skipped before the analysis", "1 on a fact skipped"),
        (
            [FactAnswer(key=ASKED.key, value="unknown")],
            [],
            'nobody knew: an answer said "I don\'t know"',
            "1 wait on a fact nobody knew",
        ),
        (
            [FactAnswer(key=ASKED.key, value="TLS 1.3")],
            [],
            "you answered it, and the analysis still did not find it settled",
            "1 on a fact you answered",
        ),
    ],
    ids=["open", "skipped", "unknown", "answered"],
)
def test_a_conditional_finding_says_why_each_fact_is_still_open(
    answered, shown, why, summary
):
    """The banner named an element and an attribute and nothing more (#1289, PR 4)."""
    parts = run_report_page(page(answered=answered, shown=shown))
    assert waits_line(parts) == why
    assert "neither confirmed nor cleared" in parts["analyses"]
    assert "What remains open: 1 finding(s) are conditional." in parts["analyses"]
    assert summary in parts["analyses"]


def test_the_next_step_follows_whether_the_report_is_final():
    follow_up = run_report_page(page())["analyses"]
    final = run_report_page(page(final=True))["analyses"]
    assert "answer the questions in the follow-up below" in follow_up
    assert "correct an answer below or submit the description again" in final


def test_a_corrected_finding_is_marked_on_its_card():
    """The mark #1355 added had no test that reached the cards."""
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
    """The page's WHY_OPEN and OPEN_SUMMARY tables and FactStatus are one set."""
    from typing import get_args

    from analysis_service.report_conditions import FactStatus
    from tests.test_webapp import viewer_javascript

    for name in ("WHY_OPEN", "OPEN_SUMMARY"):
        table = re.search(
            rf"const {name} = \{{(.*?)\n  \}};", viewer_javascript(), re.DOTALL
        )
        keys = set(re.findall(r"^\s*(\w+):", table.group(1), re.MULTILINE))
        assert keys == set(get_args(FactStatus)), name


def test_a_description_names_an_element_by_its_name():
    """A finding showed `flow:entity:customer>process:web-app>login` (#561).

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
    """#561's default view: why it matters, what is missing, what it touches
    and what to do next. The grounds and the critic's reason sit behind the
    toggle, and nothing is dropped."""
    shown, hidden = _split_by_provenance(page())

    for part in ("Why it matters", "Missing information", "Affected", "Next step"):
        assert part in shown, part
    assert "The critic's reason" in hidden
    assert "The sources do not state this." in hidden
    assert "The sources do not state this." not in shown
    assert "Quoted from the submission" in hidden
    assert "Quoted from the submission" not in shown

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
from analysis_service.questions import FactAnswer
from tests.factories import asking_threat, sample_report, valid_model
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
        [node, "-e", program], capture_output=True, text=True, timeout=30, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


ASKED = UnknownRef(
    element_id=valid_model().data_flows[1].id, attribute="encryption_in_transit"
)


def page(answered=(), shown=(), final=False):
    report = sample_report([asking_threat(ASKED)])
    return render_report(
        report, answered=answered, answered_links=[], final=final, shown=shown
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
    assert "answer them in the follow-up below" in follow_up
    assert "correct an answer below or submit the description again" in final


def test_a_corrected_finding_is_marked_on_its_card():
    """The mark #1355 added had no test that reached the cards."""
    report = sample_report([asking_threat(ASKED)])
    html = render_report(
        report,
        answered=[FactAnswer(key=ASKED.key, value="TLS 1.3")],
        answered_links=[],
        final=True,
        shown=[],
        corrections=[FactAnswer(key=ASKED.key, value="unknown")],
    ).html
    assert "Corrected after this report" in run_report_page(html)["analyses"]


def test_an_owner_s_answer_is_marked_as_unchecked():
    """A quote of the owner's answer read like the description (#1289, PR 4)."""
    from analysis_service.claims import Ground
    from analysis_service.questions import fact_line
    from analysis_service.sources import ANSWERS_LABEL
    from tests.factories import sample_threat

    answer = FactAnswer(key=ASKED.key, value="TLS 1.3")
    quote = Ground(kind="quote", text=fact_line(answer), source_label=ANSWERS_LABEL)
    derived = sample_threat().grounds[1]
    report = sample_report([sample_threat(grounds=[quote, derived])])
    html = render_report(
        report, answered=[answer], answered_links=[], final=False, shown=[]
    ).html
    text = run_report_page(html)["analyses"]
    assert "your answer; the service did not check it" in text
    assert f"— {ANSWERS_LABEL}" not in text


def test_every_reason_a_fact_is_open_has_a_line_on_the_page():
    """The page's WHY_OPEN table and FactStatus are one set in two places."""
    from typing import get_args

    from analysis_service.questions import FactStatus
    from tests.test_webapp import viewer_javascript

    table = re.search(r"const WHY_OPEN = \{(.*?)\};", viewer_javascript(), re.DOTALL)
    keys = set(re.findall(r"^\s*(\w+):", table.group(1), re.MULTILINE))
    assert keys == set(get_args(FactStatus))

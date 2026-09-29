"""The first-run app asks link questions and resumes a run with the answers (#1252).

Offline, with a runner that pauses a job that asks and completes every other.
The flow under test is the whole one a person walks: tick the toggle, get the
questions on the form page, answer them, and reach a report; or answer the
questions a finished report asks and run it again.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from types import MappingProxyType

import pytest
from fastapi.testclient import TestClient

from analysis_service import Engine, StubPipelineRunner
from analysis_service.assertions import AssertionRecord
from analysis_service.claims import UnknownRef
from analysis_service.jobs import Checkpoint, PipelineAwaiting, PipelineCompleted
from analysis_service.question_kinds import QUESTION_KINDS
from analysis_service.questions import FACET_ANSWERS, FactAnswer
from tests import test_open_facts, test_questions, test_webapp
from tests.factories import asking_threat, sample_analysis, valid_model
from tests.test_resume import parent_catalog
from tests.test_webapp import (
    CARRIED,
    LOOPBACK,
    PROJECT_ROOT,
    SAME_ORIGIN,
    TEST_DEADLINE,
    WEBAPP_LIMITS,
    posted,
)
from webapp.main import Startup, create_app

#: The shipped tier config, as the webapp tests build it: one fixture, used here.
tiers = test_webapp.tiers

HELD = Checkpoint(
    system_model=valid_model(),
    assertions=AssertionRecord(proposed=1, catalog=parent_catalog()),
)

#: The fact a finished report asks: the valid model's first flow's transport.
FACT_REF = UnknownRef(
    element_id=valid_model().data_flows[1].id, attribute="encryption_in_transit"
)


class PausingRunner(StubPipelineRunner):
    """Pauses a job that asks; completes every other, with a catalog on its report."""

    def __init__(self, catalog: bool = True) -> None:
        super().__init__()
        self.catalog = catalog
        self.resumed_links: list = []
        self.resumed_facts: list = []

    async def run(self, job, on_node):
        if job.pauses():
            held = (
                HELD if self.catalog else HELD.model_copy(update={"assertions": None})
            )
            return PipelineAwaiting(checkpoint=held)
        if job.resumption is not None:
            self.resumed_links.append(list(job.links))
            self.resumed_facts.append(list(job.facts))
        outcome = await super().run(job, on_node)
        assert isinstance(outcome, PipelineCompleted)
        report = outcome.report.model_copy(
            update={
                "assertions": HELD.assertions if self.catalog else None,
                "system_model": HELD.system_model,
                "analyses": [sample_analysis([asking_threat(FACT_REF)])],
            }
        )
        return PipelineCompleted(report=report)


@pytest.fixture
def runner():
    return PausingRunner()


def client_for(tiers, runner, catalog=True):
    def engine_for(selection):
        return Engine(
            runner,
            limits=WEBAPP_LIMITS,
            deadline_seconds=TEST_DEADLINE,
            frameworks=selection,
            carries_catalog=catalog,
        )

    startup = Startup(
        engine_for=engine_for, frameworks=CARRIED, tiers=tiers, error=None
    )
    return TestClient(create_app(startup), base_url=LOOPBACK)


def start(client, questions: bool) -> str:
    started = client.post(
        "/analyze",
        json=posted("A web app talks to a database.") | {"questions": questions},
        headers=SAME_ORIGIN,
    )
    assert started.status_code == 200, started.text
    return started.json()["run"]


def event(stream: str, name: str) -> dict:
    match = re.search(rf"event: {name}\ndata: (.*)\n", stream)
    assert match, stream
    return json.loads(match.group(1))


def answer(client, run_id: str, *links) -> dict:
    response = client.post(
        f"/answer/{run_id}", json={"links": list(links)}, headers=SAME_ORIGIN
    )
    return response


LINK = {"principal": "customer accounts", "element": "entity:customer"}


class TestTheToggle:
    def test_the_form_offers_it_on_every_install(self, tiers, runner):
        assert 'id="ask"' in client_for(tiers, runner).get("/").text
        assert 'id="ask"' in client_for(tiers, runner, catalog=False).get("/").text

    def test_an_install_with_no_catalog_pauses_and_asks_early_questions(self, tiers):
        client = client_for(tiers, PausingRunner(catalog=False), catalog=False)
        paused = start(client, questions=True)
        asked = event(client.get(f"/events/{paused}").text, "questions")
        assert asked["questions"] == []
        assert asked["facts"]
        answered = client.post(
            f"/answer/{paused}",
            json={
                "links": [],
                "facts": [{"key": asked["facts"][0]["key"], "value": "yes"}],
            },
            headers=SAME_ORIGIN,
        )
        assert answered.status_code == 200, answered.text


class TestTheWholeFlow:
    def test_a_paused_run_asks_and_its_answers_reach_a_report(self, tiers, runner):
        client = client_for(tiers, runner)
        paused = start(client, questions=True)
        asked = event(client.get(f"/events/{paused}").text, "questions")

        (question,) = asked["questions"]
        assert question["principal"] == "customer accounts"
        assert {"id": "entity:customer", "name": "Customer"} in question["options"]

        resumed = answer(client, paused, LINK)
        assert resumed.status_code == 200, resumed.text
        done = event(client.get(f"/events/{resumed.json()['run']}").text, "done")
        assert client.get(done["url"]).status_code == 200
        assert [link.element for link in runner.resumed_links[0]] == ["entity:customer"]

    def test_no_answers_continues_a_paused_run(self, tiers, runner):
        client = client_for(tiers, runner)
        paused = start(client, questions=True)
        client.get(f"/events/{paused}")
        resumed = answer(client, paused)
        assert resumed.status_code == 200
        assert "event: done" in client.get(f"/events/{resumed.json()['run']}").text

    def test_without_the_toggle_the_run_never_pauses(self, tiers, runner):
        client = client_for(tiers, runner)
        run = start(client, questions=False)
        assert "event: done" in client.get(f"/events/{run}").text

    def test_a_finished_report_s_questions_run_it_again(self, tiers, runner):
        client = client_for(tiers, runner)
        finished = start(client, questions=False)
        client.get(f"/events/{finished}")
        resumed = answer(client, finished, LINK)
        assert resumed.status_code == 200
        assert "event: done" in client.get(f"/events/{resumed.json()['run']}").text


class TestTheAnswerEndpoint:
    def test_it_requires_the_app_s_own_page(self, tiers, runner):
        client = client_for(tiers, runner)
        paused = start(client, questions=True)
        client.get(f"/events/{paused}")
        response = client.post(f"/answer/{paused}", json={"links": [LINK]})
        assert response.status_code == 403

    def test_an_unknown_run_is_not_found(self, tiers, runner):
        assert answer(client_for(tiers, runner), "nope", LINK).status_code == 404

    def test_no_answers_to_a_finished_run_is_refused(self, tiers, runner):
        client = client_for(tiers, runner)
        finished = start(client, questions=False)
        client.get(f"/events/{finished}")
        assert answer(client, finished).status_code == 400

    def test_two_answers_about_one_principal_are_refused(self, tiers, runner):
        client = client_for(tiers, runner)
        paused = start(client, questions=True)
        client.get(f"/events/{paused}")
        twice = {"principal": "Customer Accounts", "element": "none"}
        response = answer(client, paused, LINK, twice)
        assert response.status_code == 400
        assert "twice" in response.json()["message"]

    def test_a_malformed_answer_is_refused(self, tiers, runner):
        client = client_for(tiers, runner)
        paused = start(client, questions=True)
        client.get(f"/events/{paused}")
        bad = {"principal": "customer accounts", "element": "flow:x>y>z"}
        assert answer(client, paused, bad).status_code == 400


def test_the_report_page_may_reach_its_own_origin_and_nothing_else(tiers, runner):
    """It posts answers to ``/answer/{run}``, so it needs ``connect-src 'self'``."""
    client = client_for(tiers, runner)
    finished = start(client, questions=False)
    done = event(client.get(f"/events/{finished}").text, "done")
    policy = client.get(done["url"]).headers["content-security-policy"]
    assert "connect-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy


# A stand-in DOM, fetch, EventSource and location: enough to run the real
# form-page script through the question flow under ``node``. No browser is
# needed, so the flow a person walks is checked on every run of the suite.
_FORM_HARNESS = r"""
const calls = [], streams = [];
class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.dataset = {};
    this.hidden = false; this.value = ""; this.textContent = ""; this.listeners = {}; }
  append(...kids) { for (const k of kids) this.children.push(k); }
  replaceChildren(...kids) { this.children = [...kids]; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  setAttribute(name, value) { this[name] = value; }
  querySelectorAll(sel) {
    const out = [];
    const walk = (n) => { for (const c of n.children || []) {
      if (typeof c === "object") { if (c.tag === sel) out.push(c); walk(c); } } };
    walk(this); return out;
  }
  closest() { return new Node("div"); }
  querySelector() { return new Node("div"); }
}
const ids = {};
for (const id of ["analyze","description","ticks","problem","go","load","ask",
                  "asked","questions","continue","status","status-text"])
  ids[id] = new Node(id);
ids.ask.checked = true;
globalThis.document = {
  getElementById: (id) => ids[id] || null,
  createElement: (tag) => new Node(tag),
  querySelectorAll: () => [],
};
globalThis.location = { href: "", search: SEARCH };
globalThis.fetch = async (url, init) => {
  calls.push({ url, body: init && init.body ? JSON.parse(init.body) : null });
  const next = url === "/analyze" ? "r1" : "r2";
  return { ok: true, json: async () => ({ run: next }) };
};
globalThis.EventSource = class { constructor(url) { this.url = url;
  this.listeners = {}; streams.push(this); }
  addEventListener(name, fn) { this.listeners[name] = fn; } close() {} };
const settle = () => new Promise((r) => setTimeout(r, 0));
"""


def _run_form_script(steps: str, search: str = "") -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on PATH to run the form page's script")
    script = (PROJECT_ROOT / "webapp" / "static" / "first_run.js").read_text()
    program = (
        _FORM_HARNESS.replace("SEARCH", json.dumps(search))
        + script
        + "\n(async () => {\n"
        + steps
        + "\nconsole.log(JSON.stringify({calls, streams: streams.map(s => s.url),"
        " href: location.href, asked: !ids.asked.hidden}));\n})();"
    )
    done = subprocess.run(
        [node, "-e", program], capture_output=True, text=True, timeout=30, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_form_script_asks_answers_and_follows_to_the_report():
    steps = """
await ids.analyze.listeners.submit({ preventDefault() {} }); await settle();
streams[0].listeners.questions({ data: JSON.stringify({ run: "r1", questions: [
  { principal: "shopper accounts", rows: 2,
    options: [{ id: "entity:shopper", name: "Shopper" }] }], facts: [] }) });
const select = ids.questions.querySelectorAll("select")[0];
select.value = "entity:shopper";
await ids.continue.listeners.click(); await settle();
streams[1].listeners.done({ data: JSON.stringify({ url: "/report/r2" }) });
"""
    seen = _run_form_script(steps)

    assert seen["calls"][0]["url"] == "/analyze"
    assert seen["calls"][0]["body"]["questions"] is True
    assert seen["calls"][1] == {
        "url": "/answer/r1",
        "body": {
            "links": [{"principal": "shopper accounts", "element": "entity:shopper"}],
            "facts": [],
        },
    }
    assert seen["streams"] == ["/events/r1", "/events/r2"]
    assert seen["href"] == "/report/r2"
    assert seen["asked"] is False


def test_an_unanswered_question_is_left_out_of_the_answers():
    steps = """
await ids.analyze.listeners.submit({ preventDefault() {} }); await settle();
streams[0].listeners.questions({ data: JSON.stringify({ run: "r1", questions: [
  { principal: "ml engineers", rows: 1, options: [] }], facts: [] }) });
await ids.continue.listeners.click(); await settle();
"""
    seen = _run_form_script(steps)
    assert seen["calls"][1]["body"] == {"links": [], "facts": []}


def test_the_form_script_sends_an_early_answer_with_the_links():
    key = [valid_model().data_flows[0].id, "encryption_in_transit", "", "", "", ""]
    kind = [valid_model().data_stores[0].id, "", "", "", "audit-evidence", ""]
    facts = [
        {
            "key": key,
            "kind": "attribute",
            "label": "login: encryption in transit",
            "reasons": ["This flow crosses a trust boundary."],
            "choices": [],
        },
        {
            "key": kind,
            "kind": "question",
            "label": "What record shows who acted?",
            "reasons": [],
            "choices": [],
        },
    ]
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1", questions: [],
  facts: {json.dumps(facts)} }}) }});
const [first] = ids.questions.querySelectorAll("input");
first.value = "  TLS 1.3 ";
await ids.continue.listeners.click(); await settle();
"""
    seen = _run_form_script(steps)
    assert seen["calls"][1]["body"] == {
        "links": [],
        "facts": [{"key": key, "value": "TLS 1.3"}],
    }


def test_the_form_script_follows_a_run_the_report_page_started():
    seen = _run_form_script("await settle();", search="?follow=r9")
    assert seen["streams"] == ["/events/r9"]


#: One answer to the valid model's first flow's transport protection.
FACT = MappingProxyType(
    {
        "key": (
            valid_model().data_flows[1].id,
            "encryption_in_transit",
            "",
            "",
            "",
            "",
        ),
        "value": "TLS 1.3",
    }
)


class TestFactAnswers:
    """The report's open facts, answered on an install with or without a catalog."""

    def test_a_fact_answer_reruns_a_report_that_built_no_catalog(self, tiers):
        runner = PausingRunner(catalog=False)
        client = client_for(tiers, runner, catalog=False)
        finished = start(client, questions=False)
        client.get(f"/events/{finished}")
        response = client.post(
            f"/answer/{finished}",
            json={"links": [], "facts": [dict(FACT)]},
            headers=SAME_ORIGIN,
        )
        assert response.status_code == 200, response.text
        assert "event: done" in client.get(f"/events/{response.json()['run']}").text
        assert [fact.value for fact in runner.resumed_facts[0]] == ["TLS 1.3"]

    def test_a_link_answer_is_refused_where_the_report_built_no_catalog(self, tiers):
        client = client_for(tiers, PausingRunner(catalog=False), catalog=False)
        finished = start(client, questions=False)
        client.get(f"/events/{finished}")
        response = client.post(
            f"/answer/{finished}", json={"links": [LINK]}, headers=SAME_ORIGIN
        )
        assert response.status_code == 400
        assert "no assertion catalog" in response.json()["message"]

    def test_a_fact_the_report_does_not_hold_is_refused(self, tiers, runner):
        client = client_for(tiers, runner)
        finished = start(client, questions=False)
        client.get(f"/events/{finished}")
        wrong = {
            "key": [FACT["key"][0], "exposure", "", "", "", ""],
            "value": "internal",
        }
        response = client.post(
            f"/answer/{finished}",
            json={"links": [], "facts": [wrong]},
            headers=SAME_ORIGIN,
        )
        assert response.status_code == 400

    def test_the_report_page_carries_its_fact_questions(self, tiers, runner):
        client = client_for(tiers, runner)
        finished = start(client, questions=False)
        done = event(client.get(f"/events/{finished}").text, "done")
        page = client.get(done["url"]).text
        assert re.search(r'id="fact_questions"[^>]*>\[', page)
        assert re.search(r'id="question_fallback"[^>]*>\{"typed"', page)


# The report page's answer block, run for real: the viewer's helper block
# (which parses the page's payloads and defines ``el`` and ``$``), then the
# answer block cut from the shipped script by its first and last lines. The
# served script carries no comments, so neither marker is one.
_VIEWER_ANSWER_HARNESS = r"""
const calls = [];
class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.dataset = {};
    this.value = ""; this.listeners = {}; this._text = ""; }
  append(...kids) { for (const k of kids) this.children.push(k); }
  set textContent(t) { this._text = t; }
  get textContent() { return this._text; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  setAttribute(name, value) { this[name] = value; }
  all(tag) { const out = [];
    const walk = (n) => { for (const c of n.children) if (typeof c === "object") {
      if (c.tag === tag) out.push(c); walk(c); } };
    walk(this); return out; }
}
const payloads = PAYLOADS;
const box = new Node("div");
globalThis.document = {
  getElementById: (id) => id === "links" ? box
    : Object.assign(new Node("script"), { _text: JSON.stringify(payloads[id] ?? {}) }),
  createElement: (tag) => new Node(tag),
  createDocumentFragment: () => new Node("#fragment"),
  createTextNode: (t) => Object.assign(new Node("#text"), { _text: t }),
};
globalThis.location = { href: "", pathname: "/report/r1" };
globalThis.fetch = async (url, init) => {
  calls.push({ url, body: JSON.parse(init.body) });
  return { ok: true, json: async () => ({ run: "r2" }) };
};
"""

ANSWER_BLOCK_START = "  if (LINK_QUESTIONS.length || FACT_QUESTIONS.length) {"
ANSWER_BLOCK_END = "    box.append(actions, note);\n  }\n"


def _run_answer_block(payloads: dict, steps: str) -> dict:
    from tests.test_webapp import FIRST_VIEWER_CONSTANT, viewer_javascript

    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on PATH to run the report page's script")
    javascript = viewer_javascript()
    helpers = javascript.split(FIRST_VIEWER_CONSTANT)[0]
    start = javascript.index(ANSWER_BLOCK_START)
    end = javascript.index(ANSWER_BLOCK_END, start) + len(ANSWER_BLOCK_END)
    program = (
        _VIEWER_ANSWER_HARNESS.replace("PAYLOADS", json.dumps(payloads))
        + helpers
        + javascript[start:end]
        + "\n(async () => {\n"
        + steps
        + "\nconsole.log(JSON.stringify({calls, href: location.href}));\n})();"
    )
    done = subprocess.run(
        [node, "-e", program], capture_output=True, text=True, timeout=30, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_report_page_sends_both_kinds_of_answer_and_follows_the_run():
    model = valid_model().model_dump(mode="json")
    payloads = {
        "report": {"system_model": model},
        "link_questions": [
            {
                "principal": "customer accounts",
                "rows": 1,
                "options": ["entity:customer"],
            }
        ],
        "fact_questions": [
            {
                "key": list(FACT["key"]),
                "kind": "attribute",
                "label": "login",
                "cited_by": 2,
                "covered_so_far": 2,
                "choices": [],
                "findings": ["stride/I-01", "stride/T-01"],
            },
            {
                "key": ["", "", "", "whether queries are bound", "", ""],
                "kind": "subject",
                "label": "whether queries are bound",
                "cited_by": 1,
                "covered_so_far": 2,
                "choices": [],
                "findings": ["stride/T-01"],
            },
        ],
    }
    steps = """
const [link] = box.all("select");
link.value = "entity:customer";
const [first, second] = box.all("input");
first.value = "  TLS 1.3 ";
const button = box.all("button")[0];
await button.listeners.click();
"""
    seen = _run_answer_block(payloads, steps)

    assert seen["calls"] == [
        {
            "url": "/answer/r1",
            "body": {
                "links": [
                    {"principal": "customer accounts", "element": "entity:customer"}
                ],
                "facts": [{"key": list(FACT["key"]), "value": "TLS 1.3"}],
            },
        }
    ]
    assert seen["href"] == "/?follow=r2"


def tallies(
    questions: list[dict], answer: list[dict], value: str | None = None
) -> list[str]:
    """The running line after answering the questions in ``answer``.

    Each answer is ``value`` where one is given, and otherwise the first choice
    or a line of text.
    """
    payloads = {
        "report": {"system_model": valid_model().model_dump(mode="json")},
        "link_questions": [],
        "fact_questions": questions,
    }
    steps = f"""
const inputs = [...box.all("input"), ...box.all("select")];
const byKey = new Map(inputs.map(i => [i.dataset.key, i]));
for (const q of {json.dumps(answer)}) {{
  const input = byKey.get(JSON.stringify(q.key));
  input.value = {json.dumps(value)} ?? (q.choices.length ? q.choices[0] : "an answer");
  (input.listeners.input || input.listeners.change)();
}}
calls.push(...box.all("div").map(d => d.textContent).filter(t => t.startsWith("Your")));
"""
    return _run_answer_block(payloads, steps)["calls"]


report = test_open_facts.report


DECIDED = "The analysis decides again whether they are settled."


def test_an_answer_of_unknown_covers_no_finding(report):
    """I don't know leaves the fact open, so the count does not move (#1289)."""
    questions = [q.to_json() for q in test_questions.ask(report)]
    if not questions:
        pytest.skip("this report waits on no fact")
    total = len({f for q in questions for f in q["findings"]})
    line = f"Your answers cover every question for 0 of the {total} findings"
    assert tallies(questions, questions, value="unknown") == [
        f"{line} that wait on one. {DECIDED}"
    ]


class TestTheRunningCount:
    """The page's count and the ranking's ``covered_so_far`` are two readers of
    one rule, so each is held against the other on real reports."""

    def test_it_matches_the_ranking_at_every_depth(self, report):
        questions = [q.to_json() for q in test_questions.ask(report)]
        if not questions:
            pytest.skip("this report waits on no fact")
        total = len({f for q in questions for f in q["findings"]})
        for depth in sorted({0, 1, len(questions) // 2, len(questions)}):
            covered = questions[depth - 1]["covered_so_far"] if depth else 0
            line = f"Your answers cover every question for {covered} of the {total}"
            assert tallies(questions, questions[:depth]) == [
                f"{line} findings that wait on one. {DECIDED}"
            ]

    def test_it_counts_answers_given_out_of_order(self):
        questions = [
            {
                "key": ["", "", "", "a", "", ""],
                "kind": "subject",
                "basis": "critic",
                "label": "a",
                "cited_by": 1,
                "covered_so_far": 1,
                "choices": [],
                "findings": ["stride/S-01"],
            },
            {
                "key": ["", "", "", "b", "", ""],
                "kind": "subject",
                "basis": "critic",
                "label": "b",
                "cited_by": 1,
                "covered_so_far": 2,
                "choices": [],
                "findings": ["stride/S-02"],
            },
        ]
        line = "Your answers cover every question for 1 of the 2 findings that wait on one."
        assert tallies(questions, questions[1:]) == [f"{line} {DECIDED}"]


def test_the_report_page_says_how_many_facts_fell_back_to_free_text():
    question = {
        "key": ["", "", "", "a", "", ""],
        "kind": "subject",
        "basis": "critic",
        "label": "a",
        "cited_by": 1,
        "covered_so_far": 1,
        "choices": [],
        "findings": ["stride/S-01"],
    }
    payloads = {
        "report": {"system_model": valid_model().model_dump(mode="json")},
        "link_questions": [],
        "fact_questions": [question],
        "question_fallback": {"typed": 12, "free_text": 1, "rate": 0.0769},
    }
    steps = """
calls.push(...box.all("div").map(d => d.textContent).filter(t => t.includes("fixed list")));
"""
    line = "The reviewer asked 12 open fact(s) from the fixed list of questions"
    assert _run_answer_block(payloads, steps)["calls"] == [
        f"{line} and 1 in its own words."
    ]


def test_the_registry_removes_a_run_that_waits_for_answers_last():
    """A paused run outlives later finished runs (#1289, Q7)."""
    from analysis_service.jobs import Checkpoint
    from tests.factories import sample_report, valid_model
    from webapp.main import Analyses

    analyses = Analyses(max_runs=2)
    paused = analyses.claim()
    paused.checkpoint = Checkpoint(system_model=valid_model(), assertions=None)
    analyses.release()
    for _ in range(3):
        run = analyses.claim()
        run.report = sample_report()
        analyses.release()

    assert analyses.get(paused.id) is paused
    assert analyses.get(run.id) is run


def _waiting_registry():
    from analysis_service.jobs import Checkpoint
    from tests.factories import valid_model
    from webapp.main import Analyses

    analyses = Analyses(max_runs=2)
    held = []
    for _ in range(2):
        run = analyses.claim()
        run.checkpoint = Checkpoint(system_model=valid_model(), assertions=None)
        analyses.release()
        held.append(run)
    return analyses, held


def test_a_full_registry_of_waiting_runs_refuses_a_new_one():
    """A third run evicted the oldest paused one while it still waited
    (#1289, F4)."""
    from webapp.main import RegistryFull

    analyses, held = _waiting_registry()

    with pytest.raises(RegistryFull, match="wait for answers"):
        analyses.claim()
    assert [analyses.get(run.id) for run in held] == held
    assert analyses.claim(answering=held[1]) is not None, "the refusal held the gate"


def test_answers_to_a_waiting_run_may_take_its_place():
    analyses, (first, second) = _waiting_registry()

    run = analyses.claim(answering=first)

    assert analyses.get(first.id) is None
    assert analyses.get(second.id) is second
    assert analyses.get(run.id) is run


CONTROL = {
    "key": list(FACT["key"]),
    "kind": "attribute",
    "label": "login: encryption in transit",
    "cited_by": 1,
    "covered_so_far": 1,
    "choices": [],
    "form": "control",
    "suggestions": ["HTTPS", "TLS 1.3"],
    "findings": ["stride/I-01"],
}


def control_answer(steps: str) -> list:
    """The facts the report page sends after ``steps`` fill the control."""
    payloads = {
        "report": {"system_model": valid_model().model_dump(mode="json")},
        "link_questions": [],
        "fact_questions": [CONTROL],
    }
    click = "\nawait box.all('button')[0].listeners.click();\n"
    seen = _run_answer_block(payloads, steps + click)
    return seen["calls"][0]["body"]["facts"]


@pytest.mark.parametrize(("state", "sent"), [("none", "none"), ("unknown", "unknown")])
def test_a_control_says_there_is_none_or_that_nobody_knows(state, sent):
    facts = control_answer(
        f"""
const [state] = box.all("select");
state.value = "{state}";
state.listeners.change();
"""
    )
    assert facts == [{"key": CONTROL["key"], "value": sent}]


def test_a_control_names_its_mechanism_in_text():
    facts = control_answer(
        """
const [state] = box.all("select");
state.value = "mechanism";
state.listeners.change();
const [input] = box.all("input");
input.value = "mutual TLS, certificates rotated yearly";
"""
    )
    assert facts == [
        {"key": CONTROL["key"], "value": "mutual TLS, certificates rotated yearly"}
    ]


def test_a_control_offers_its_suggestions_beside_the_text():
    payloads = {
        "report": {"system_model": valid_model().model_dump(mode="json")},
        "link_questions": [],
        "fact_questions": [CONTROL],
    }
    steps = """
const [list] = box.all("datalist");
const [input] = box.all("input");
calls.push({ list: list.id, points: input.list,
  values: list.children.map(o => o.value) });
"""
    (seen,) = _run_answer_block(payloads, steps)["calls"]
    assert seen["points"] == seen["list"]
    assert seen["values"] == CONTROL["suggestions"]


def test_the_form_script_sends_a_control_s_state():
    """At the pause too, "There is none" sends ``none``."""
    key = [valid_model().data_flows[0].id, "encryption_in_transit", "", "", "", ""]
    facts = [
        {
            "key": key,
            "kind": "attribute",
            "label": "login: encryption in transit",
            "reasons": [],
            "choices": [],
            "form": "control",
            "suggestions": ["HTTPS"],
        }
    ]
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1", questions: [],
  facts: {json.dumps(facts)} }}) }});
const [state] = ids.questions.querySelectorAll("select");
state.value = "none";
state.listeners.change();
await ids.continue.listeners.click(); await settle();
"""
    seen = _run_form_script(steps)
    assert seen["calls"][1]["body"] == {
        "links": [],
        "facts": [{"key": key, "value": "none"}],
    }


def test_the_form_script_shows_what_the_service_is_doing():
    """A spinner and a sentence from Analyze until the pause, and again after."""
    steps = """
await ids.analyze.listeners.submit({ preventDefault() {} }); await settle();
calls.push({ building: !ids.status.hidden, text: ids["status-text"].textContent });
streams[0].listeners.questions({ data: JSON.stringify({ run: "r1", questions: [],
  facts: [] }) });
calls.push({ paused: ids.status.hidden });
await ids.continue.listeners.click(); await settle();
calls.push({ analysing: !ids.status.hidden, text: ids["status-text"].textContent });
"""
    seen = _run_form_script(steps)["calls"]
    building, paused, analysing = (c for c in seen if "url" not in c)

    assert building["building"]
    assert "building the system model" in building["text"]
    assert "stops for your answers" in building["text"]
    assert paused == {"paused": True}
    assert analysing["analysing"]
    assert "threat analysis with your answers" in analysing["text"]


def test_the_form_script_asks_each_group_once_with_a_row_per_element():
    """Two elements under one kind make one group with two rows (#1289)."""
    store, flow = valid_model().data_stores[0], valid_model().data_flows[0]

    def fact(element, name, choices):
        return {
            "key": [element.id, "", "", "", name, ""],
            "kind": "question",
            "label": name,
            "reasons": ["why"],
            "choices": [{"id": c, "name": ""} for c in choices],
            "form": "choice" if choices else "text",
            "suggestions": [],
            "group": name,
            "group_heading": f"{name} heading?",
            "element": element.name,
        }

    facts = [
        fact(store, "stored-copy-integrity", ["yes", "no"]),
        fact(flow, "audit-evidence", []),
        fact(flow, "stored-copy-integrity", ["yes", "no"]),
    ]
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1", questions: [],
  facts: {json.dumps(facts)} }}) }});
const groups = ids.questions.querySelectorAll("details");
calls.push({{ groups: groups.map(g => ({{
  title: g.children[0].textContent,
  rows: g.children.slice(1).map(r => r.children[0].textContent),
  open: g.open }})) }});
const [first] = ids.questions.querySelectorAll("select");
first.value = "no";
await ids.continue.listeners.click(); await settle();
"""
    seen = _run_form_script(steps)["calls"]
    (layout,) = [c for c in seen if "groups" in c]

    assert layout["groups"] == [
        {
            "title": "stored-copy-integrity heading? (2)",
            "rows": [store.name, flow.name],
            "open": True,
        },
        {"title": "audit-evidence heading? (1)", "rows": [flow.name], "open": True},
    ]
    (sent,) = [c for c in seen if c.get("url") == "/answer/r1"]
    assert sent["body"]["facts"] == [{"key": facts[0]["key"], "value": "no"}]


def facet_fact(element, kind="capacity-limits"):
    facets = QUESTION_KINDS[kind].facets
    return {
        "key": [element.id, "", "", "", kind, ""],
        "kind": "question",
        "label": kind,
        "reasons": [],
        "choices": [],
        "form": "facets",
        "suggestions": [],
        "facets": [{"id": f.id, "question": f.question} for f in facets],
        "group": kind,
        "group_heading": f"{kind}?",
        "element": element.name,
    }


def test_the_pause_asks_facets_as_a_table_with_a_same_for_all_row():
    """A column per facet, a row per element, and a row that sets a column."""
    store, flow = valid_model().data_stores[0], valid_model().data_flows[0]
    facts = [facet_fact(store), facet_fact(flow)]
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1", questions: [],
  facts: {json.dumps(facts)} }}) }});
const [table] = ids.questions.querySelectorAll("table");
const [head, all, ...rows] = table.children;
calls.push({{ columns: head.children.map(c => c.textContent), rows: rows.length }});
const every = all.children[1].children[0];
every.value = "yes";
every.listeners.change();
const quota = rows[1].children[4].children[0];
quota.value = "not applicable";
await ids.continue.listeners.click(); await settle();
"""
    seen = _run_form_script(steps)["calls"]
    (layout,) = [c for c in seen if "columns" in c]
    (sent,) = [c for c in seen if c.get("url") == "/answer/r1"]

    facets = QUESTION_KINDS["capacity-limits"].facets
    assert layout == {
        "columns": ["Part of your system", *(f.question for f in facets)],
        "rows": 2,
    }
    assert sent["body"]["facts"] == [
        {"key": facts[0]["key"], "facets": {"rate": "yes"}},
        {"key": facts[1]["key"], "facets": {"rate": "yes", "quota": "not applicable"}},
    ]
    for fact in sent["body"]["facts"]:
        FactAnswer.model_validate(fact)


@pytest.mark.parametrize("value", ["yes", "no", "not applicable", "unknown"])
def test_every_facet_answer_the_page_offers_is_one_the_service_takes(value):
    """The page's FACET_CHOICES and the service's FACET_ANSWERS are two readers."""
    store = valid_model().data_stores[0]
    facts = [facet_fact(store)]
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1", questions: [],
  facts: {json.dumps(facts)} }}) }});
const [table] = ids.questions.querySelectorAll("table");
const select = table.children[2].children[1].children[0];
calls.push({{ offered: select.children.map(o => o.value) }});
select.value = {json.dumps(value)};
await ids.continue.listeners.click(); await settle();
"""
    seen = _run_form_script(steps)["calls"]
    (offered,) = [c["offered"] for c in seen if "offered" in c]
    (sent,) = [c for c in seen if c.get("url") == "/answer/r1"]

    assert offered == ["", *FACET_ANSWERS, "unknown"]
    FactAnswer.model_validate(sent["body"]["facts"][0])


def test_the_report_page_counts_a_kind_only_when_every_facet_is_known():
    """The critic names the kind, so one known facet does not cover it."""
    store = valid_model().data_stores[0]
    question = {
        **facet_fact(store, "audit-evidence"),
        "basis": "evidence",
        "cited_by": 1,
        "covered_so_far": 1,
        "findings": ["stride/R-01"],
    }
    payloads = {
        "report": {"system_model": valid_model().model_dump(mode="json")},
        "link_questions": [],
        "fact_questions": [question],
    }
    steps = """
const tally = () => box.all("div").map(d => d.textContent).filter(t => t.startsWith("Your"))[0];
const [first, second] = box.all("select");
second.value = "no";
second.listeners.change();
calls.push({ one_known: tally() });
first.value = "unknown";
first.listeners.change();
calls.push({ one_unknown: tally() });
first.value = "yes";
first.listeners.change();
calls.push({ both_known: tally() });
await box.all("button")[0].listeners.click();
"""
    seen = _run_answer_block(payloads, steps)["calls"]

    assert seen[0]["one_known"].startswith("Your answers cover every question for 0")
    assert seen[1]["one_unknown"].startswith("Your answers cover every question for 0")
    assert seen[2]["both_known"].startswith("Your answers cover every question for 1")
    assert seen[3]["body"]["facts"] == [
        {
            "key": question["key"],
            "facets": {"records-actor": "yes", "record-protected": "no"},
        }
    ]

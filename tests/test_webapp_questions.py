"""The first-run app asks link questions and resumes a run with the answers (#1252).

Offline, with a runner that pauses a job that asks and completes every other.
The flow under test is the whole one a person walks: tick the toggle, get the
questions on the form page, answer them, and reach a report; or answer the
questions a finished report asks and run it again.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from types import MappingProxyType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from analysis_service import Engine, StubPipelineRunner
from analysis_service.assertions import AssertionRecord
from analysis_service.claims import UnknownRef
from analysis_service.fact_answers import FACET_ANSWERS, FactAnswer, merged_facts
from analysis_service.fact_writes import check_fact_answers
from analysis_service.frameworks import PACKAGES
from analysis_service.jobs import Checkpoint, PipelineAwaiting, PipelineCompleted
from analysis_service.question_kinds import QUESTION_KINDS
from tests import test_open_facts, test_questions, test_webapp
from tests.factories import (
    asking_threat,
    sample_analysis,
    sample_report,
    valid_model,
)
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
from webapp.main import Run, Startup, create_app

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


def app_for(tiers, runner, catalog=True, analyses=None):
    """The first-run app over ``runner``, as the browser tests serve it too."""

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
    return create_app(startup, analyses)


def client_for(tiers, runner, catalog=True):
    return TestClient(app_for(tiers, runner, catalog), base_url=LOOPBACK)


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
        f"/answer/{run_id}",
        json={"links": list(links), "revision": 0},
        headers=SAME_ORIGIN,
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
                "revision": 0,
            },
            headers=SAME_ORIGIN,
        )
        assert answered.status_code == 200, answered.text


class TestTheRounds:
    """A paused run asks in rounds, and starts once nothing is left (ADR 0053)."""

    def test_a_saved_round_shows_the_next_and_only_the_start_button_starts(self, tiers):
        """The last save started the analysis by itself (#1289, item 7)."""
        client = client_for(tiers, PausingRunner(catalog=False), catalog=False)
        paused = start(client, questions=True)
        shown = event(client.get(f"/events/{paused}").text, "questions")
        for _ in range(20):
            facts = [{"key": q["key"], "value": "unknown"} for q in shown["facts"]]
            saved = client.post(
                f"/answer/{paused}",
                json={
                    "links": [],
                    "facts": facts,
                    "save": True,
                    "revision": shown["revision"],
                },
                headers=SAME_ORIGIN,
            )
            assert saved.status_code == 200, saved.text
            before = {tuple(q["key"]) for q in shown["facts"]}
            shown = saved.json()
            assert shown["run"] == paused, "a save starts no run"
            assert before <= {tuple(a["key"]) for a in shown["answered"]}
            if not shown["facts"]:
                break
            assert shown["stop"] is None
            assert not before & {tuple(q["key"]) for q in shown["facts"]}
        else:
            pytest.fail("the rounds never ended")
        # The stop says why truthfully: the limits held questions back
        # exactly where it says so.
        assert shown["stop"] == (
            "budget-exhausted" if shown["withheld"] else "nothing-left"
        )

        started = client.post(
            f"/answer/{paused}",
            json={"links": [], "facts": [], "revision": shown["revision"]},
            headers=SAME_ORIGIN,
        )
        assert started.status_code == 200, started.text
        done = client.get(f"/events/{started.json()['run']}").text
        assert "event: done" in done

    def test_a_save_from_a_page_left_on_an_earlier_round_is_refused(self, tiers):
        """A page left open on an earlier round saved over a later one (#1289)."""
        client = client_for(tiers, PausingRunner(catalog=False), catalog=False)
        paused = start(client, questions=True)
        shown = event(client.get(f"/events/{paused}").text, "questions")
        assert shown["revision"] == 0
        first, second = (q["key"] for q in shown["facts"][:2])

        def save(key, revision):
            body = {"links": [], "facts": [], "save": True, "skip": [key]}
            return client.post(
                f"/answer/{paused}",
                json=body | {"revision": revision},
                headers=SAME_ORIGIN,
            )

        landed = save(first, 0)
        assert landed.status_code == 200, landed.text
        assert landed.json()["revision"] == 1
        stale = save(second, 0)
        assert stale.status_code == 409
        assert "another tab" in stale.json()["message"]
        missing = client.post(
            f"/answer/{paused}", json={"links": [], "facts": []}, headers=SAME_ORIGIN
        )
        assert missing.status_code == 400

    def test_a_round_of_skips_alone_is_saved_and_listed(self, tiers):
        client = client_for(tiers, PausingRunner(catalog=False), catalog=False)
        paused = start(client, questions=True)
        shown = event(client.get(f"/events/{paused}").text, "questions")
        keys = [q["key"] for q in shown["facts"]]
        saved = client.post(
            f"/answer/{paused}",
            json={"links": [], "facts": [], "save": True, "skip": keys, "revision": 0},
            headers=SAME_ORIGIN,
        )
        assert saved.status_code == 200, saved.text
        after = saved.json()
        assert sorted(q["key"] for q in after["skipped"]) == sorted(keys)
        assert not {tuple(k) for k in keys} & {tuple(q["key"]) for q in after["facts"]}
        assert after["answered"] == []

    @pytest.mark.parametrize(
        "skip", ["x", [["a", "b"]], [[1, 2, 3, 4, 5, 6]], [""], ["x" * 201], [7]]
    )
    def test_a_malformed_skip_is_refused(self, tiers, skip):
        client = client_for(tiers, PausingRunner(catalog=False), catalog=False)
        paused = start(client, questions=True)
        client.get(f"/events/{paused}")
        saved = client.post(
            f"/answer/{paused}",
            json={"links": [], "facts": [], "save": True, "skip": skip, "revision": 0},
            headers=SAME_ORIGIN,
        )
        assert saved.status_code == 400

    def test_a_start_after_a_skipped_round_admits_what_the_route_admits(self, tiers):
        """The route admitted a round-2 answer and the engine refused it (#1289).

        The engine built its round again without the skipped list, so it asked
        round 1 again and the run failed after the route returned 200.
        """
        client = client_for(tiers, PausingRunner(catalog=False), catalog=False)
        paused = start(client, questions=True)
        shown = event(client.get(f"/events/{paused}").text, "questions")
        skipped = client.post(
            f"/answer/{paused}",
            json={
                "links": [],
                "facts": [],
                "save": True,
                "skip": [q["key"] for q in shown["facts"]],
                "revision": 0,
            },
            headers=SAME_ORIGIN,
        )
        asked = next(q for q in skipped.json()["facts"] if q["form"] == "choice")
        started = client.post(
            f"/answer/{paused}",
            json={
                "links": [],
                "facts": [{"key": asked["key"], "value": asked["choices"][0]["id"]}],
                "revision": 1,
            },
            headers=SAME_ORIGIN,
        )
        assert started.status_code == 200, started.text
        assert "event: done" in client.get(f"/events/{started.json()['run']}").text

    def test_a_refused_answer_names_its_question(self, tiers):
        """Every malformed answer got one message that named nothing (#1289)."""
        client = client_for(tiers, PausingRunner(catalog=False), catalog=False)
        paused = start(client, questions=True)
        first = event(client.get(f"/events/{paused}").text, "questions")["facts"][0]
        saved = client.post(
            f"/answer/{paused}",
            json={
                "links": [],
                "facts": [{"key": first["key"], "value": "x" * 1001}],
                "revision": 0,
            },
            headers=SAME_ORIGIN,
        )
        assert saved.status_code == 400
        assert saved.json()["message"] == (
            f'The answer to "{first["label"]}" was refused:'
            " String should have at most 1000 characters"
        )

    def test_a_save_with_no_answer_is_refused(self, tiers):
        client = client_for(tiers, PausingRunner(catalog=False), catalog=False)
        paused = start(client, questions=True)
        client.get(f"/events/{paused}")
        saved = client.post(
            f"/answer/{paused}",
            json={"links": [], "facts": [], "save": True, "revision": 0},
            headers=SAME_ORIGIN,
        )
        assert saved.status_code == 400
        assert "at least one question" in saved.json()["message"]


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

    def test_a_follow_up_s_page_compares_it_with_the_report_before(self, tiers, runner):
        """The follow-up keeps the report its answers came from (#561)."""
        client = client_for(tiers, runner)
        finished = start(client, questions=False)
        client.get(f"/events/{finished}")
        resumed = answer(client, finished, LINK).json()["run"]
        client.get(f"/events/{resumed}")

        def changes(run_id):
            page = client.get(f"/report/{run_id}").text
            block = re.search(r'id="changes"[^>]*>(.*?)</script>', page, re.DOTALL)
            return json.loads(block.group(1))

        assert changes(finished) == {}
        assert changes(resumed)["findings"], "the follow-up lists no finding"


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
                  "asked","questions","earlier","save","continue","status",
                  "status-text","answer-problem","skip","skipped"])
  ids[id] = new Node(id);
ids.ask.checked = true;
globalThis.document = {
  getElementById: (id) => ids[id] || null,
  createElement: (tag) => new Node(tag),
  querySelectorAll: () => [],
};
globalThis.location = { href: "", search: SEARCH };
globalThis.window = { scrollTo() {} };
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
  replaceChildren(...kids) { this.children = [...kids]; }
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

ANSWER_BLOCK_START = (
    "  const earlierCount = EARLIER_FACTS.length + EARLIER_LINKS.length;\n"
)
ANSWER_BLOCK_END = "    box.append(actions, note);\n  }\n"


def _run_answer_block(
    payloads: dict,
    steps: str,
    start_marker: str = ANSWER_BLOCK_START,
    end_marker: str = ANSWER_BLOCK_END,
) -> dict:
    from tests.test_webapp import FIRST_VIEWER_CONSTANT, viewer_javascript

    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on PATH to run the report page's script")
    # Every report carries `analyses`; a stub that states only the model
    # stands for one with no findings.
    if isinstance(payloads.get("report"), dict):
        payloads["report"] = {"analyses": [], **payloads["report"]}
    # The page reads each lane through the service's own table, as it is served.
    payloads.setdefault(
        "lanes", {name: pkg.id_rule.lane_field for name, pkg in PACKAGES.items()}
    )
    javascript = viewer_javascript()
    helpers = javascript.split(FIRST_VIEWER_CONSTANT)[0]
    start = javascript.index(start_marker)
    end = javascript.index(end_marker, start) + len(end_marker)
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


def _subject_question(name, findings):
    return {
        "key": ["", "", "", name, "", ""],
        "kind": "subject",
        "basis": "evidence",
        "label": name,
        "cited_by": len(findings),
        "covered_so_far": 0,
        "choices": [],
        "form": "text",
        "suggestions": [],
        "facets": [],
        "max_length": 500,
        "findings": findings,
        "asked_before": False,
    }


def test_an_early_row_carries_its_element_s_source_words(tiers):
    """The owner could not see what the service read about an element (#1289, B2)."""
    client = client_for(tiers, PausingRunner(catalog=False), catalog=False)
    paused = start(client, questions=True)
    shown = event(client.get(f"/events/{paused}").text, "questions")
    model = valid_model()
    for row in shown["facts"]:
        element = model.get(row["key"][0])
        assert row["excerpt"] == ("" if element is None else element.source_excerpt)
    assert any(row["excerpt"] for row in shown["facts"]), "a control: some are read"


def test_the_form_page_says_why_and_quotes_the_description():
    """Each early row shows its first reason and its element's words (#1289, B1)."""
    long = "x" * 250
    rows = [
        _text_row(["", "", "", "who?", "", ""], "who?")
        | {
            "reasons": ["Can an attacker reach it?", "second"],
            "excerpt": long,
            "frameworks": ["asvs", "stride"],
        }
    ]
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1", questions: [],
  facts: {json.dumps(rows)}, remaining: {{ field: 1 }},
  answered: [], answered_links: [], revision: 0 }}) }});
const walk = (n, out = []) => {{ for (const c of n.children || [])
  if (typeof c === "object") {{ if (c.className === "hint") out.push(c); walk(c, out); }}
  return out; }};
calls.push(walk(ids.questions).map(h => ({{ text: h.textContent, title: h.title }})));
"""
    hints = _run_form_script(steps)["calls"][-1]
    (row,) = [h for h in hints if h["text"].startswith("Why:")]
    assert row["text"] == (
        "Why: Can an attacker reach it? \u00b7 Used by the asvs and stride analyses"
        " \u00b7 Your description of this part, for reference: \u201c"
        + "x" * 200
        + "\u2026\u201d"
    )
    assert row["title"] == long


def test_the_report_names_the_findings_a_question_settles():
    """The report showed a count, never which findings wait (#1289, B1)."""
    payloads = {
        "report": {
            "system_model": valid_model().model_dump(mode="json"),
            "analyses": [
                {
                    "framework": "stride",
                    "claims": [{"id": "T-01", "title": "Build tampering"}],
                }
            ],
        },
        "link_questions": [],
        "fact_questions": [
            _subject_question("who signs builds?", ["stride/T-01"]) | {"band": "high"}
        ],
    }
    steps = """
calls.push(box.all("div").map(d => d.textContent).filter(t => t.startsWith("Waiting")));
"""
    (waiting,) = _run_answer_block(payloads, steps)["calls"]
    assert waiting == ["Waiting on it: Build tampering. The most important is high."]


def test_a_question_no_finding_waits_on_is_set_apart():
    """A follow-up listed questions that no conditional finding waits on among
    the ones that settle findings (#1289, item 8)."""
    payloads = {
        "report": {"system_model": valid_model().model_dump(mode="json")},
        "link_questions": [],
        "fact_questions": [
            _subject_question("who rotates keys?", []),
            _subject_question("who signs builds?", ["stride/T-01"]),
        ],
    }
    steps = """
const direct = (node, tag) => node.children.filter(c => typeof c === "object" && c.tag === tag);
calls.push(box.all("details").map(d => ({
  summary: direct(d, "summary")[0].textContent,
  labels: direct(d, "p").flatMap(p => p.all("b").map(b => b.textContent)),
})));
const inputs = box.all("input").filter(i => i.type === "text");
inputs.forEach(i => { i.value = "the release team"; });
await box.all("button")[0].listeners.click();
"""
    sections, sent = _run_answer_block(payloads, steps)["calls"]
    (followup,) = [s for s in sections if s["summary"].startswith("Optional follow-up")]
    (apart,) = [s for s in sections if s["summary"].startswith("Questions no")]
    assert followup["labels"] == ["who signs builds?"]
    assert apart["summary"] == "Questions no conditional finding waits on (1)"
    assert apart["labels"] == ["who rotates keys?"]
    assert {fact["key"][3] for fact in sent["body"]["facts"]} == {
        "who rotates keys?",
        "who signs builds?",
    }


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
    questions: list[dict],
    answer: list[dict],
    value: str | None = None,
    *,
    analyses: list[dict] | None = None,
    starts: str = "Your",
) -> list[str]:
    """The running lines starting ``starts`` after answering the questions in ``answer``.

    Each answer is ``value`` where one is given, and otherwise the first choice
    or a line of text. ``analyses`` stands in for the report's findings.
    """
    payloads = {
        "report": {
            "system_model": valid_model().model_dump(mode="json"),
            "analyses": analyses or [],
        },
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
calls.push(...box.all("div").map(d => d.textContent)
  .filter(t => t.startsWith({json.dumps(starts)})));
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


def test_the_follow_up_states_its_scope_before_it_runs():
    """#561: how far the answers reach, and what pressing the button runs."""
    questions = [
        {
            "key": ["", "", "", name, "", ""],
            "kind": "subject",
            "basis": "critic",
            "label": name,
            "cited_by": 1,
            "covered_so_far": 1,
            "choices": [],
            "findings": [finding],
        }
        for name, finding in (("a", "stride/S-01"), ("b", "stride/S-02"))
    ]
    analyses = [
        {
            "framework": "stride",
            "claims": [
                {"id": "S-01", "category": "spoofing"},
                {"id": "S-02", "category": "tampering"},
            ],
        }
    ]

    assert tallies(questions, [], analyses=analyses, starts="No answer") == [
        "No answer given yet."
    ]
    (line,) = tallies(questions, questions, analyses=analyses, starts="2 answer")
    assert line.startswith("2 answer(s) reach 2 finding(s) in 2 lane(s).")
    assert "runs the whole analysis once" in line
    (line,) = tallies(questions, questions[:1], analyses=analyses, starts="1 answer")
    assert line.startswith("1 answer(s) reach 1 finding(s) in 1 lane(s).")


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


def test_answers_to_a_full_registry_of_waiting_runs_still_start():
    analyses, (first, second) = _waiting_registry()

    run = analyses.claim(answering=first)

    assert [analyses.get(held.id) for held in (first, second, run)] == [
        first,
        second,
        run,
    ]


async def _deadline(on_node):
    from analysis_service.engine import EngineDeadlineError

    raise EngineDeadlineError("the deadline")


async def _bad_config(on_node):
    from analysis_service.errors import ConfigError

    raise ConfigError("a setting")


async def _rejected(on_node):
    from analysis_service.jobs import PipelineRejected

    return PipelineRejected(issues=[], nodes=[])


async def _cancelled(on_node):
    raise asyncio.CancelledError


@pytest.mark.parametrize("start", [_deadline, _bad_config, _rejected, _cancelled])
def test_a_failed_resumed_run_leaves_its_paused_run_to_answer_again(start):
    """A full registry removed the paused run its answers resumed from, and
    the resumed run then failed with no checkpoint (#1289, F1)."""
    from webapp.main import _drive

    analyses, (first, _) = _waiting_registry()
    first.facts = [FactAnswer(key=FACT["key"], value="TLS")]
    run = analyses.claim(answering=first)
    first.resumed_by = run

    async def drive():
        run.task = asyncio.create_task(_drive(analyses, run, start))
        await asyncio.wait([run.task])

    asyncio.run(drive())

    assert run.status == "failed"
    assert analyses.get(first.id) is first
    assert first.waiting and not first.resumed
    assert first.facts[0].value == "TLS"
    assert analyses.claim(answering=first) is not None


class FailingOnceRunner(PausingRunner):
    """Pauses, then fails its first resumption with a provider error."""

    def __init__(self) -> None:
        super().__init__(catalog=False)
        self.failed = False

    async def run(self, job, on_node):
        if job.resumption is not None and not self.failed:
            from litellm.exceptions import APIConnectionError

            self.failed = True
            raise APIConnectionError("the provider", "openai", "a-model")
        return await super().run(job, on_node)


def test_a_full_registry_retries_a_failed_resumed_run_with_its_saved_answers(tiers):
    """The retry runs through the answer route from the kept checkpoint
    (#1289, F1)."""
    from webapp.main import Analyses

    runner = FailingOnceRunner()
    client = TestClient(
        app_for(tiers, runner, catalog=False, analyses=Analyses(1)), base_url=LOOPBACK
    )
    paused = start(client, questions=True)
    shown = event(client.get(f"/events/{paused}").text, "questions")
    saved_fact = {"key": shown["facts"][0]["key"], "value": "yes"}
    saved = client.post(
        f"/answer/{paused}",
        json={"links": [], "facts": [saved_fact], "save": True, "revision": 0},
        headers=SAME_ORIGIN,
    )
    assert saved.status_code == 200, saved.text

    def resume():
        started = client.post(
            f"/answer/{paused}",
            json={"links": [], "facts": [], "revision": saved.json()["revision"]},
            headers=SAME_ORIGIN,
        )
        assert started.status_code == 200, started.text
        return client.get(f"/events/{started.json()['run']}").text

    assert "event: failed" in resume()
    assert "event: done" in resume()
    [retried] = runner.resumed_facts
    assert saved_fact["key"] in [list(fact.key) for fact in retried]


def test_a_paused_run_goes_once_its_resumed_run_has_a_report():
    analyses, (first, second) = _waiting_registry()
    run = analyses.claim(answering=first)
    first.resumed_by = run
    run.report = sample_report([])
    analyses.release()

    analyses.claim()

    assert analyses.get(first.id) is None
    assert analyses.get(second.id) is second


def test_a_run_waits_until_the_run_its_answers_started_has_a_report():
    """A failed resumed run left its paused run open to eviction (#1289)."""
    _, (paused, _) = _waiting_registry()
    resumed = Run(id="resumed")
    paused.resumed_by = resumed

    assert paused.waiting
    resumed.report = sample_report([])
    assert not paused.waiting


CONTROL = {
    "key": list(FACT["key"]),
    "kind": "attribute",
    "label": "login: encryption in transit",
    "cited_by": 1,
    "covered_so_far": 1,
    "choices": [],
    "form": "control",
    "suggestions": ["HTTPS", "TLS 1.3"],
    "max_length": 200,
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


#: Each state a control's selector takes, and what the row then sends.
CONTROL_SENDS = {"": None, "none": "none", "unknown": "unknown", "mechanism": "mTLS"}

#: Choose ``state`` on the control's selector, and type a mechanism under it.
_CHOOSE = """
state.value = {state!r}; state.listeners.change();
if (state.value === "mechanism") {{ input.value = "mTLS"; input.listeners.input(); }}
"""


def _control_steps(states) -> str:
    return "".join(_CHOOSE.format(state=state) for state in states)


def _early_control_calls(steps: str) -> list:
    """Every call the form page records, with one control asked, after ``steps``.

    ``steps`` ends with a click on "Start the analysis".
    """
    key = [valid_model().data_flows[0].id, "encryption_in_transit", "", "", "", ""]
    facts = [
        {
            "key": key,
            "kind": "attribute",
            "label": "login: encryption in transit",
            "reasons": [],
            "choices": [],
            "form": "control",
            "suggestions": [],
            "max_length": 200,
        }
    ]
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1", questions: [],
  facts: {json.dumps(facts)} }}) }});
const [state] = ids.questions.querySelectorAll("select");
const [input] = ids.questions.querySelectorAll("input");
{steps}
await ids.continue.listeners.click(); await settle();
"""
    return _run_form_script(steps)["calls"]


def _early_control_sent(steps: str) -> list:
    """What the form page sends for its control after ``steps``."""
    return [fact["value"] for fact in _early_control_calls(steps)[1]["body"]["facts"]]


def _report_control_sent(steps: str) -> list:
    """What the report page sends for its control after ``steps``."""
    found = 'const [state] = box.all("select");\nconst [input] = box.all("input");\n'
    return [fact["value"] for fact in control_answer(found + steps)]


@pytest.mark.parametrize("send", [_early_control_sent, _report_control_sent])
@pytest.mark.parametrize("before", list(CONTROL_SENDS))
@pytest.mark.parametrize("after", list(CONTROL_SENDS))
def test_a_control_sends_the_state_chosen_last(send, before, after):
    """Blank after "There is none" sent ``none`` (#1289, B1).

    Both pages read one rule: the selector is the answer, and the text is
    read only under "mechanism".
    """
    expected = CONTROL_SENDS[after]
    assert send(_control_steps([before, after])) == (
        [] if expected is None else [expected]
    )


@pytest.mark.parametrize("button", ["save", "continue"])
def test_a_refused_answer_shows_beside_the_buttons_and_keeps_the_answers(button):
    """The refusal showed above the description, out of sight (#1289)."""
    steps = f"""
state.value = "none"; state.listeners.change();
globalThis.fetch = async (url, init) => {{
  calls.push({{ url, body: JSON.parse(init.body) }});
  return {{ ok: false, json: async () => ({{ message: "refused" }}) }};
}};
await ids[{button!r}].listeners.click(); await settle();
calls.push({{ shown: !ids["answer-problem"].hidden,
  said: ids["answer-problem"].textContent, state: state.value,
  asked: !ids.asked.hidden }});
"""
    _, sent, seen, again = _early_control_calls(steps)
    assert seen == {"shown": True, "said": "refused", "state": "none", "asked": True}
    assert again["body"]["facts"] == sent["body"]["facts"], "the answers stand"


@pytest.mark.parametrize("send", [_early_control_sent, _report_control_sent])
def test_a_text_box_takes_no_more_than_its_field_holds(send):
    """Every box took 1,000 characters where the field held 200 (#1289)."""
    steps = """
input.value = "x".repeat(input.maxLength); input.listeners.input();
"""
    assert send(steps) == ["x" * 200]


@pytest.mark.parametrize("send", [_early_control_sent, _report_control_sent])
def test_typing_a_mechanism_chooses_it(send):
    assert send('input.value = "mTLS"; input.listeners.input();') == ["mTLS"]


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


def _control_row(flow, form="control"):
    return {
        "key": [flow.id, "encryption_in_transit", "", "", "", ""],
        "kind": "attribute",
        "label": f"{flow.name}: encryption in transit",
        "reasons": [],
        "choices": [],
        "form": form,
        "suggestions": ["TLS 1.3"],
        "facets": [],
        "max_length": 200,
        "decisions": 1,
        "excerpt": "",
        "group": "encryption_in_transit",
        "group_heading": "Encryption in transit?",
        "element": flow.name,
    }


_SHARED_STEPS = """
await ids.analyze.listeners.submit({ preventDefault() {} }); await settle();
streams[0].listeners.questions({ data: JSON.stringify({ run: "r1", questions: [],
  facts: FACTS, remaining: {}, answered: [], answered_links: [], revision: 0 }) });
const [group] = ids.questions.querySelectorAll("details");
const child = (r, tag) => r.children.find(c => typeof c === "object" && c.tag === tag);
const walk = (n, out = []) => { for (const c of n.children || [])
  if (typeof c === "object") { out.push(c); walk(c, out); } return out; };
const shared = walk(group).filter(n => n.tag === "b" && n.textContent === "Same for all");
calls.push({ shared: shared.length });
"""


def test_same_for_all_fills_only_the_ticked_rows():
    """One answer for several parts, with exceptions (#1289, design note C)."""
    first, second = valid_model().data_flows[:2]
    facts = [_control_row(first), _control_row(second)]
    steps = (
        _SHARED_STEPS.replace("FACTS", json.dumps(facts))
        + """
const [top, ...rows] = group.children.filter(c => c.tag === "div" || c.tag === "p");
const line = child(top, "p");
const state = child(line, "select");
state.value = "none"; state.listeners.change();
const ticks = group.children.filter(c => c.tag === "p").map(r => child(r, "input"));
ticks[1].checked = false;
await child(line, "button").listeners.click();
await ids.continue.listeners.click(); await settle();
"""
    )
    seen = _run_form_script(steps)["calls"]
    assert seen[1] == {"shared": 1}
    (sent,) = [c for c in seen if c.get("url") == "/answer/r1"]
    assert sent["body"]["facts"] == [{"key": facts[0]["key"], "value": "none"}]


@pytest.mark.parametrize("case", ["one-row", "two-forms"])
def test_same_for_all_is_offered_only_where_rows_share_a_question(case):
    first, second = valid_model().data_flows[:2]
    facts = (
        [_control_row(first)]
        if case == "one-row"
        else [_control_row(first), _control_row(second, form="text")]
    )
    steps = _SHARED_STEPS.replace("FACTS", json.dumps(facts))
    assert _run_form_script(steps)["calls"][1] == {"shared": 0}


def test_the_form_script_boxes_each_run_of_one_group_with_a_row_per_element():
    """Questions of one kind that come together share a box, a row each; the
    same kind later in the round opens a new box, so the list order holds."""
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
        fact(flow, "stored-copy-integrity", ["yes", "no"]),
        fact(flow, "audit-evidence", []),
        fact(store, "audit-evidence", []),
        fact(store, "stored-copy-integrity", ["yes", "no"]),
    ]
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1", questions: [],
  facts: {json.dumps(facts)} }}) }});
const groups = ids.questions.querySelectorAll("details");
const rowsOf = g => g.children.filter(r => r.tag === "p");
const child = (r, tag) => r.children.find(c => typeof c === "object" && c.tag === tag);
calls.push({{ groups: groups.map(g => ({{
  title: g.children[0].children.map(n => n.textContent).join(""),
  rows: rowsOf(g).map(r => child(r, "b").textContent),
  open: g.open }})) }});
child(rowsOf(groups[0])[0], "select").value = "no";
await ids.continue.listeners.click(); await settle();
"""
    seen = _run_form_script(steps)["calls"]
    (layout,) = [c for c in seen if "groups" in c]

    assert layout["groups"] == [
        {
            "title": "stored-copy-integrity heading? (2 question(s), 2 choice(s))",
            "rows": [store.name, flow.name],
            "open": True,
        },
        {
            "title": "audit-evidence heading? (2 question(s), 2 choice(s))",
            "rows": [flow.name, store.name],
            "open": True,
        },
        {
            "title": "stored-copy-integrity heading? (1 question(s), 1 choice(s))",
            "rows": [store.name],
            "open": True,
        },
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


@pytest.mark.parametrize(("rows", "shared"), [(1, False), (2, True)])
def test_a_facet_table_sets_a_column_only_over_two_rows(rows, shared):
    """A table of one row showed a "Same for all" row that set only that row."""
    elements = [valid_model().data_stores[0], valid_model().data_flows[0]]
    facts = [facet_fact(element) for element in elements[:rows]]
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1", questions: [],
  facts: {json.dumps(facts)} }}) }});
const [table] = ids.questions.querySelectorAll("table");
calls.push({{ labels: table.children.map(row => row.children[0].textContent) }});
"""
    seen = _run_form_script(steps)["calls"]
    (labels,) = [c["labels"] for c in seen if "labels" in c]

    assert ("Same for all" in labels) is shared
    assert len(labels) == 1 + rows + shared


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
const select = table.children[1].children[1].children[0];
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


def test_the_report_page_offers_one_optional_follow_up():
    payloads = {
        "report": {"system_model": valid_model().model_dump(mode="json")},
        "link_questions": [
            {
                "principal": "customer accounts",
                "rows": 1,
                "options": ["entity:customer"],
            }
        ],
        "fact_questions": [],
        "final": False,
    }
    steps = "calls.push(box.all('summary').map(node => node.textContent));"
    [lines] = _run_answer_block(payloads, steps)["calls"]
    assert lines[0].startswith("Optional follow-up: 1 question(s).")
    assert "After that, the report is final." in lines[0]


def test_a_pause_with_nothing_to_ask_starts_the_analysis_at_once():
    steps = """
await ids.analyze.listeners.submit({ preventDefault() {} }); await settle();
streams[0].listeners.questions({ data: JSON.stringify({ run: "r1", questions: [],
  facts: [], remaining: {}, stop: "nothing-left", answered: [], answered_links: [] }) });
await settle();
"""
    seen = _run_form_script(steps)

    assert seen["calls"][1] == {"url": "/answer/r1", "body": {"links": [], "facts": []}}
    assert seen["streams"] == ["/events/r1", "/events/r2"]
    assert not seen["asked"]


@pytest.mark.parametrize(
    ("withheld", "said"), [(0, "No question is left."), (3, "3 more question(s)")]
)
def test_the_last_save_waits_for_the_start_button(withheld, said):
    """A save that left nothing to ask started the analysis (#1289, item 7)."""
    link = {"principal": "shopper accounts", "element": "entity:shopper"}
    ready = {
        "run": "r1",
        "revision": 4,
        "questions": [],
        "facts": [],
        "remaining": {},
        "stop": "budget-exhausted" if withheld else "nothing-left",
        "withheld": withheld,
        "answered": [],
        "answered_links": [
            {
                "principal": "shopper accounts",
                "rows": 2,
                "options": [{"id": "entity:shopper", "name": "Shopper"}],
                "answer": link,
            }
        ],
    }
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1", questions: [
  {{ principal: "shopper accounts", rows: 2,
    options: [{{ id: "entity:shopper", name: "Shopper" }}] }}], facts: [],
  remaining: {{}}, answered: [], answered_links: [], revision: 3 }}) }});
ids.questions.querySelectorAll("select")[0].value = "entity:shopper";
const answered = fetch;
globalThis.fetch = async (url, init) => {{
  calls.push({{ url, body: JSON.parse(init.body) }});
  return {{ ok: true, json: async () => ({json.dumps(ready)}) }};
}};
await ids.save.listeners.click(); await settle();
const text = (n) => typeof n === "string" ? n
  : [n.textContent || "", ...(n.children || []).map(text)].join("");
calls.push({{ waiting: streams.length === 1, saveShown: !ids.save.hidden,
  asked: !ids.asked.hidden, said: text(ids.questions) }});
globalThis.fetch = answered;
await ids.continue.listeners.click(); await settle();
"""
    seen = _run_form_script(steps)
    _, save, after, start = seen["calls"]

    assert save["body"] == {
        "links": [link],
        "facts": [],
        "save": True,
        "skip": [],
        "revision": 3,
    }
    assert start["body"]["revision"] == 4, "the start sends the revision it read"
    assert after["waiting"] and after["asked"] and not after["saveShown"]
    assert said in after["said"]
    assert "Nothing runs until you choose Start the analysis" in after["said"]
    assert start["url"] == "/answer/r1" and "save" not in start["body"]
    assert seen["streams"] == ["/events/r1", "/events/r2"]


def _text_row(key, label):
    return {
        "key": key,
        "kind": "subject",
        "label": label,
        "element": label,
        "form": "text",
        "choices": [],
        "suggestions": [],
        "facets": [],
        "max_length": 500,
        "decisions": 1,
        "group": "g",
        "group_heading": "G",
        "reasons": [],
    }


def test_the_round_size_counts_its_link_questions():
    """The line said how many choices a round asks, and left out its link
    questions, 1 to 3 a pause on the archived reports with a catalog (#1289)."""
    row = _text_row(["", "", "", "who?", "", ""], "who?")
    link = {
        "principal": "shopper accounts",
        "rows": 2,
        "options": [{"id": "entity:shopper", "name": "Shopper"}],
    }
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1",
  questions: [{json.dumps(link)}], facts: [{json.dumps(row)}],
  remaining: {{ field: 1 }}, answered: [], answered_links: [], revision: 0 }}) }});
const walk = (n, out = []) => {{ for (const c of n.children || [])
  if (typeof c === "object") {{ out.push(c); walk(c, out); }} return out; }};
calls.push(walk(ids.questions).map(n => n.textContent).find(t => t.includes("This round asks")));
"""
    said = _run_form_script(steps)["calls"][-1]
    assert "This round asks 2 choice(s)." in said


def test_the_round_size_leaves_out_a_part_until_its_parent_is_yes():
    """A part hidden until its parent's "yes" was counted in the round's
    choices, so the line named a choice the page did not show (#1289)."""
    parent_key = ["", "", "", "", "", "oauth"]
    choices = [{"id": "yes", "name": ""}, {"id": "no", "name": ""}]
    parent = _text_row(parent_key, "OAuth?") | {
        "kind": "capability",
        "form": "choice",
        "choices": choices,
    }
    part = _text_row(["", "", "", "", "", "oauth-client"], "A client?") | {
        "kind": "capability",
        "form": "choice",
        "choices": choices,
        "parent": parent_key,
    }
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1",
  questions: [], facts: [{json.dumps(parent)}, {json.dumps(part)}],
  remaining: {{ capability: 2 }}, answered: [], answered_links: [], revision: 0 }}) }});
const walk = (n, out = []) => {{ for (const c of n.children || [])
  if (typeof c === "object") {{ out.push(c); walk(c, out); }} return out; }};
const said = () => walk(ids.questions).map(n => n.textContent)
  .find(t => t.includes("This round asks"));
calls.push(said());
const [yes] = walk(ids.questions).filter(n => n.tag === "select");
yes.value = "yes"; yes.listeners.change();
calls.push(said());
yes.value = "no"; yes.listeners.change();
calls.push(said());
"""
    before, opened, closed = _run_form_script(steps)["calls"][-3:]
    assert "This round asks 1 choice(s). Up to 1 more appear" in before
    assert opened.endswith("This round asks 2 choice(s).")
    assert closed == before


def test_a_part_of_a_part_hides_with_its_parent():
    """A part's own part stayed shown after the part above it hid."""
    choices = [{"id": "yes", "name": ""}, {"id": "no", "name": ""}]
    keys = [["", "", "", "", "", name] for name in ("a", "b", "c")]
    rows = [
        _text_row(key, f"{key[5]}?")
        | {"kind": "capability", "form": "choice", "choices": choices}
        | ({"parent": keys[at - 1]} if at else {})
        for at, key in enumerate(keys)
    ]
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1",
  questions: [], facts: {json.dumps(rows)},
  remaining: {{ capability: 3 }}, answered: [], answered_links: [], revision: 0 }}) }});
const walk = (n, out = []) => {{ for (const c of n.children || [])
  if (typeof c === "object") {{ out.push(c); walk(c, out); }} return out; }};
const [a, b] = walk(ids.questions).filter(n => n.tag === "select");
const shown = () => walk(ids.questions).filter(n => n.tag === "p" && n.children
  .some(c => typeof c === "object" && c.tag === "select")).map(p => !p.hidden);
a.value = "yes"; a.listeners.change();
b.value = "yes"; b.listeners.change();
calls.push(shown());
a.value = "no"; a.listeners.change();
calls.push(shown());
"""
    opened, closed = _run_form_script(steps)["calls"][-2:]
    assert opened == [True, True, True]
    assert closed == [True, False, False]


def test_a_capability_says_it_decides_what_an_analysis_covers():
    row = _text_row(["", "", "", "", "", "oauth"], "OAuth?") | {
        "kind": "capability",
        "form": "choice",
        "choices": [{"id": "yes", "name": ""}, {"id": "no", "name": ""}],
        "reasons": ["An answer settles whether up to 3 units apply."],
        "frameworks": ["asvs"],
    }
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1",
  questions: [], facts: [{json.dumps(row)}],
  remaining: {{ capability: 1 }}, answered: [], answered_links: [], revision: 0 }}) }});
const walk = (n, out = []) => {{ for (const c of n.children || [])
  if (typeof c === "object") {{ out.push(c); walk(c, out); }} return out; }};
calls.push(walk(ids.questions).map(n => n.textContent).find(t => t.startsWith("Why:")));
"""
    said = _run_form_script(steps)["calls"][-1]
    assert "Decides what the asvs analysis covers" in said


def test_a_link_only_round_says_what_it_asks():
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1",
  questions: [{json.dumps(_LINK)}], facts: [], remaining: {{}},
  answered: [], answered_links: [], revision: 0 }}) }});
const walk = (n, out = []) => {{ for (const c of n.children || [])
  if (typeof c === "object") {{ out.push(c); walk(c, out); }} return out; }};
calls.push(walk(ids.questions).map(n => n.textContent).find(t => t.includes("This round asks")));
"""
    said = _run_form_script(steps)["calls"][-1]
    assert "1 question(s) about which part of your system a name is" in said
    assert "This round asks 1 choice(s)." in said


def test_skip_the_rest_skips_only_the_blank_questions():
    answered, blank = (["", "", "", name, "", ""] for name in ("who?", "when?"))
    rows = [_text_row(answered, "who?"), _text_row(blank, "when?")]
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1", questions: [],
  facts: {json.dumps(rows)}, remaining: {{ field: 2 }},
  answered: [], answered_links: [] }}) }});
ids.questions.querySelectorAll("input")[0].value = "the admins";
globalThis.fetch = async (url, init) => {{
  calls.push({{ url, body: JSON.parse(init.body) }});
  return {{ ok: false, json: async () => ({{ message: "stop here" }}) }};
}};
await ids.skip.listeners.click(); await settle();
"""
    (_, sent) = _run_form_script(steps)["calls"]
    assert sent["body"] == {
        "links": [],
        "facts": [{"key": answered, "value": "the admins"}],
        "save": True,
        "skip": [blank],
    }


def test_skip_the_rest_sets_aside_a_question_answered_in_part():
    """A question with facets answered in part came back first in every round,
    and "Skip the rest" left it alone (#1289)."""
    key = ["process:web-app", "", "", "", "capacity-limits", ""]
    facets = [{"id": f, "question": f"{f}?"} for f in ("rate", "size")]
    row = _text_row(key, "Web App") | {
        "kind": "question",
        "form": "facets",
        "facets": facets,
        "decisions": 2,
    }
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1", questions: [],
  facts: [{json.dumps(row)}], remaining: {{ field: 1 }},
  answered: [], answered_links: [], revision: 0 }}) }});
const selects = ids.questions.querySelectorAll("select");
selects[0].value = "yes";
globalThis.fetch = async (url, init) => {{
  calls.push({{ url, body: JSON.parse(init.body) }});
  return {{ ok: false, json: async () => ({{ message: "stop here" }}) }};
}};
await ids.skip.listeners.click(); await settle();
"""
    (_, sent) = _run_form_script(steps)["calls"]
    assert sent["body"]["facts"] == [{"key": key, "facets": {"rate": "yes"}}]
    assert sent["body"]["skip"] == [key]


def test_a_skipped_question_can_be_answered_from_its_list():
    kept, skipped = (["", "", "", name, "", ""] for name in ("who?", "when?"))
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1", questions: [],
  facts: [{json.dumps(_text_row(kept, "who?"))}], remaining: {{ field: 1 }},
  skipped: [{json.dumps(_text_row(skipped, "when?"))}],
  answered: [], answered_links: [] }}) }});
calls.push({{ listed: !ids.skipped.hidden }});
const [open] = ids.skipped.querySelectorAll("button");
open.listeners.click();
ids.skipped.querySelectorAll("input")[0].value = "nightly";
globalThis.fetch = async (url, init) => {{
  calls.push({{ url, body: JSON.parse(init.body) }});
  return {{ ok: false, json: async () => ({{ message: "stop here" }}) }};
}};
await ids.save.listeners.click(); await settle();
"""
    _, listed, sent = _run_form_script(steps)["calls"]
    assert listed == {"listed": True}
    assert sent["body"]["facts"] == [{"key": skipped, "value": "nightly"}]
    assert sent["body"]["skip"] == []


_LINK = {
    "key": "shopper account",
    "principal": "shopper accounts",
    "rows": 2,
    "options": [{"id": "entity:shopper", "name": "Shopper"}],
}


def _skip_the_rest(links, facts) -> dict:
    """The body "Skip the rest" sends for a round of these, every row blank."""
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1",
  questions: {json.dumps(links)}, facts: {json.dumps(facts)},
  remaining: {{ field: 1 }}, answered: [], answered_links: [], revision: 0 }}) }});
globalThis.fetch = async (url, init) => {{
  calls.push({{ url, body: JSON.parse(init.body) }});
  return {{ ok: false, json: async () => ({{ message: "stop here" }}) }};
}};
await ids.skip.listeners.click(); await settle();
"""
    (_, sent) = _run_form_script(steps)["calls"]
    return sent["body"]


def test_skip_the_rest_skips_a_link_only_round():
    """The page sent an empty save, which the service refuses (#1289, A2)."""
    body = _skip_the_rest([_LINK], [])
    assert body["links"] == []
    assert body["skip"] == [_LINK["key"]]


def test_skip_the_rest_skips_the_blank_links_and_facts_together():
    key = ["", "", "", "who?", "", ""]
    body = _skip_the_rest([_LINK], [_text_row(key, "who?")])
    assert body["skip"] == [_LINK["key"], key]


def test_a_skipped_link_can_be_answered_from_its_list():
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1", questions: [],
  facts: [], remaining: {{}}, skipped: [], skipped_links: [{json.dumps(_LINK)}],
  answered: [], answered_links: [], revision: 1 }}) }});
const [open] = ids.skipped.querySelectorAll("button");
open.listeners.click();
ids.skipped.querySelectorAll("select")[0].value = "entity:shopper";
globalThis.fetch = async (url, init) => {{
  calls.push({{ url, body: JSON.parse(init.body) }});
  return {{ ok: false, json: async () => ({{ message: "stop here" }}) }};
}};
await ids.save.listeners.click(); await settle();
"""
    (_, sent) = _run_form_script(steps)["calls"]
    assert sent["body"]["links"] == [
        {"principal": "shopper accounts", "element": "entity:shopper"}
    ]


def test_a_question_answered_in_part_and_skipped_has_one_editor():
    """It was listed under "Your answers" and "Skipped for now", and the two
    editors sent two answers to one key, which the service refuses (#1289, A3).
    """
    key = ["store:db", "", "", "", "capacity-limits", ""]
    facets = [{"id": f, "question": f"{f}?"} for f in ("rate", "size")]
    question = _text_row(key, "DB") | {
        "kind": "question",
        "form": "facets",
        "facets": facets,
        "decisions": 2,
    }
    answered = question | {
        "answer": {"key": key, "value": None, "facets": {"rate": "yes"}}
    }
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1", questions: [],
  facts: [], remaining: {{}}, skipped: [{json.dumps(question)}],
  answered: [{json.dumps(answered)}], answered_links: [], revision: 1 }}) }});
calls.push({{ earlier: ids.earlier.querySelectorAll("button").length }});
const [open] = ids.skipped.querySelectorAll("button");
open.listeners.click();
const [rate, size] = ids.skipped.querySelectorAll("select");
calls.push({{ kept: rate.value }});
size.value = "no";
globalThis.fetch = async (url, init) => {{
  calls.push({{ url, body: JSON.parse(init.body) }});
  return {{ ok: false, json: async () => ({{ message: "stop here" }}) }};
}};
await ids.save.listeners.click(); await settle();
"""
    _, earlier, kept, sent = _run_form_script(steps)["calls"]
    assert earlier == {"earlier": 0}
    assert kept == {"kept": "yes"}
    assert sent["body"]["facts"] == [
        {"key": key, "facets": {"rate": "yes", "size": "no"}}
    ]


def test_an_earlier_answer_can_be_changed():
    key = ["flow:a>b:x", "encryption_in_transit", "", "", "", ""]
    answered = {
        "key": key,
        "kind": "attribute",
        "label": "x: encryption in transit",
        "element": "x",
        "form": "control",
        "choices": [],
        "suggestions": ["TLS 1.3"],
        "facets": [],
        "answer": {"key": key, "value": "none", "facets": None},
    }
    pending = {**answered, "key": ["process:p", "", "", "", "capacity-limits", ""]}
    pending |= {"form": "text", "group": "g", "group_heading": "G", "reasons": []}
    del pending["answer"]
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1", questions: [],
  facts: [{json.dumps(pending)}], remaining: {{ field: 1 }},
  answered: [{json.dumps(answered)}], answered_links: [] }}) }});
const walk = (n, tag, out = []) => {{ for (const c of n.children || [])
  if (typeof c === "object") {{ if (c.tag === tag) out.push(c); walk(c, tag, out); }}
  return out; }};
walk(ids.earlier, "button")[0].listeners.click();
const [state] = walk(ids.earlier, "select");
const [text] = walk(ids.earlier, "input");
state.value = "mechanism"; state.listeners.change();
text.value = "TLS 1.3";
await ids.continue.listeners.click(); await settle();
"""
    seen = _run_form_script(steps)

    assert seen["calls"][1]["body"]["facts"] == [{"key": key, "value": "TLS 1.3"}]


def test_a_table_that_comes_back_keeps_its_earlier_facets():
    """A size answer in a later round kept the rate answer (#1289, F2)."""
    store = valid_model().data_stores[0]
    fact = facet_fact(store)
    earlier = {**fact, "answer": {"key": fact["key"], "facets": {"rate": "yes"}}}
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1", questions: [],
  facts: [{json.dumps(fact)}], answered: [{json.dumps(earlier)}] }}) }});
await ids.continue.listeners.click(); await settle();
"""
    seen = _run_form_script(steps)["calls"]
    (sent,) = [c for c in seen if c.get("url") == "/answer/r1"]

    assert sent["body"]["facts"] == [{"key": fact["key"], "facets": {"rate": "yes"}}]


def test_a_follow_up_question_says_why_it_is_asked():
    """Skipped before the analysis, or new from it (ADR 0054)."""

    def fact(label, basis, asked_before):
        return {
            "key": ["", "", "", label, "", ""],
            "kind": "subject",
            "basis": basis,
            "asked_before": asked_before,
            "label": label,
            "cited_by": 1,
            "covered_so_far": 1,
            "choices": [],
            "findings": ["stride/T-01"],
        }

    payloads = {
        "report": {"system_model": valid_model().model_dump(mode="json")},
        "link_questions": [],
        "fact_questions": [
            fact("skipped", "evidence", True),
            fact("new", "evidence", False),
            fact("reviewer", "critic", False),
        ],
        "final": False,
    }
    steps = """
calls.push(box.all("p").map(p => p.children.filter(c => typeof c === "string")
  .join("")).filter(text => text.includes("wait on it")));
"""
    [lines] = _run_answer_block(payloads, steps)["calls"]
    assert "(you skipped this before the analysis)" in lines[0]
    assert "(new from the analysis)" in lines[1]
    assert "skipped" not in lines[2] and "new from" not in lines[2]
    assert "(raised by the reviewer;" in lines[2]


CORRECTIONS_BLOCK_START = "  if (FINAL && (CORRECTIONS.answers || []).length) {"
CORRECTIONS_BLOCK_END = '    $("links").append(box);\n  }\n'


class TestCorrections:
    """A final report takes corrections and runs nothing (#1289, ADR 0054)."""

    def runs(self, tiers):
        """A client, a finished run, and the final run its follow-up wrote."""
        client = client_for(tiers, PausingRunner(catalog=False), catalog=False)
        finished = start(client, questions=False)
        client.get(f"/events/{finished}")
        final = client.post(
            f"/answer/{finished}",
            json={"links": [], "facts": [dict(FACT)]},
            headers=SAME_ORIGIN,
        ).json()["run"]
        client.get(f"/events/{final}")
        return client, finished, final

    def correct(self, client, run, value, headers=SAME_ORIGIN):
        body = {"facts": [{"key": list(FACT["key"]), "value": value}]}
        return client.post(f"/correct/{run}", json=body, headers=headers)

    def test_a_final_report_keeps_a_correction_and_the_page_carries_it(self, tiers):
        client, _, final = self.runs(tiers)
        assert self.correct(client, final, "unknown").status_code == 200
        page = client.get(f"/report/{final}").text
        match = re.search(r'id="corrections"[^>]*>(.*?)</script>', page, re.DOTALL)
        payload = json.loads(match.group(1))
        (row,) = payload["answers"]
        assert row["key"] == list(FACT["key"])
        assert row["answer"]["value"] == "unknown"
        assert row["corrected"] is True
        assert row["form"] == "control"

    def test_a_report_that_is_not_final_refuses_it(self, tiers):
        client, finished, _ = self.runs(tiers)
        response = self.correct(client, finished, "none")
        assert response.status_code == 400
        assert "only a final report" in response.json()["message"]

    def test_it_requires_the_app_s_own_page(self, tiers):
        client, _, final = self.runs(tiers)
        assert self.correct(client, final, "none", headers={}).status_code == 403

    def test_the_page_sends_a_changed_answer(self):
        row = {
            "key": list(FACT["key"]),
            "label": "login: encryption in transit",
            "form": "control",
            "choices": [],
            "facets": [],
            "suggestions": [],
            "max_length": 200,
            "answer": dict(FACT) | {"key": list(FACT["key"])},
            "corrected": False,
        }
        payloads = {
            "report": {"system_model": valid_model().model_dump(mode="json")},
            "final": True,
            "corrections": {"answers": [row], "findings": []},
        }
        steps = """
await box.all("button")[0].listeners.click();
const [state] = box.all("select");
state.value = "unknown"; state.listeners.change();
await box.all("button")[1].listeners.click();
"""
        seen = _run_answer_block(
            payloads, steps, CORRECTIONS_BLOCK_START, CORRECTIONS_BLOCK_END
        )
        (sent,) = seen["calls"]
        assert sent["url"] == "/correct/r1"
        assert sent["body"] == {
            "facts": [{"key": list(FACT["key"]), "value": "unknown"}]
        }


def test_a_paused_run_starts_one_analysis(tiers):
    """A paused run's answers started a second analysis once the first ended
    (#1289, ADR 0054)."""
    client = client_for(tiers, PausingRunner(catalog=False), catalog=False)
    paused = start(client, questions=True)
    event(client.get(f"/events/{paused}").text, "questions")
    body = {"links": [], "facts": [], "revision": 0}
    first = client.post(f"/answer/{paused}", json=body, headers=SAME_ORIGIN)
    assert "event: done" in client.get(f"/events/{first.json()['run']}").text

    again = client.post(f"/answer/{paused}", json=body, headers=SAME_ORIGIN)

    assert again.status_code == 409
    assert "already started an analysis" in again.json()["message"]


def test_a_refused_answer_to_a_skipped_question_names_it(tiers):
    """The label map left out skipped questions, which still take answers."""
    client = client_for(tiers, PausingRunner(catalog=False), catalog=False)
    paused = start(client, questions=True)
    first = event(client.get(f"/events/{paused}").text, "questions")["facts"][0]
    body = {"links": [], "facts": [], "save": True, "skip": [first["key"]]}
    client.post(f"/answer/{paused}", json=body | {"revision": 0}, headers=SAME_ORIGIN)

    refused = client.post(
        f"/answer/{paused}",
        json={
            "links": [],
            "facts": [{"key": first["key"], "value": ""}],
            "revision": 1,
        },
        headers=SAME_ORIGIN,
    )

    assert refused.status_code == 400
    assert f'The answer to "{first["label"]}"' in refused.json()["message"]


def test_a_save_after_the_start_is_refused(tiers):
    """A save after the start landed on the paused run, and no run read it."""
    client = client_for(tiers, PausingRunner(catalog=False), catalog=False)
    paused = start(client, questions=True)
    first = event(client.get(f"/events/{paused}").text, "questions")["facts"][0]
    body = {"links": [], "facts": [], "revision": 0}
    run = client.post(f"/answer/{paused}", json=body, headers=SAME_ORIGIN)
    client.get(f"/events/{run.json()['run']}")

    saved = client.post(
        f"/answer/{paused}",
        json=body | {"save": True, "skip": [first["key"]]},
        headers=SAME_ORIGIN,
    )

    assert saved.status_code == 409


@pytest.mark.parametrize(
    ("state", "status"),
    [
        ({"task": SimpleNamespace(done=lambda: False)}, "running"),
        ({"task": SimpleNamespace(done=lambda: True)}, "failed"),
        ({"task": SimpleNamespace(done=lambda: True), "report": "report"}, "completed"),
    ],
    ids=["running", "ended-without-a-report", "completed"],
)
def test_the_app_and_the_store_free_a_parent_alike(state, status):
    """The first-run app and the job store each held "a failed resumed run
    frees its parent", each tested against its own expectation (#1289)."""
    import asyncio

    from analysis_service.jobs import InMemoryJobStore, JobRecord, Resumption
    from analysis_service.sources import Source
    from tests.factories import SEEDING_BUDGET, sample_selection

    if state.get("report"):
        state = state | {"report": sample_report([])}
    resumed = Run(id="resumed", **state)
    paused = Run(id="paused", checkpoint=HELD, resumed_by=resumed)

    store = InMemoryJobStore()
    held = JobRecord.create(
        owner_subject="idp|user-1",
        sources=[Source.description("A web app talks to a database.")],
        frameworks=sample_selection(),
        resumption=Resumption(follow_up=False, parent_id="paused", checkpoint=HELD),
    )
    asyncio.run(store.reserve(held, ceiling=10, budget=SEEDING_BUDGET))
    asyncio.run(store.save(held.model_copy(update={"status": resumed.status})))

    assert resumed.status == status
    assert paused.resumed is (store._resumed_by("paused") is not None)


@pytest.mark.parametrize("save", [True, False], ids=["save", "start"])
def test_answers_that_break_the_input_limits_are_refused_at_once(tiers, save):
    """The route took answers over the limits, and the run then failed."""
    client = client_for(tiers, PausingRunner(catalog=False), catalog=False)
    head = "A web app talks to a database. "
    text = head + "x" * (100 * 1024 - len(head) - 10)
    started = client.post(
        "/analyze", json=posted(text) | {"questions": True}, headers=SAME_ORIGIN
    )
    paused = started.json()["run"]
    shown = event(client.get(f"/events/{paused}").text, "questions")
    asked = next(q for q in shown["facts"] if q["form"] == "choice")
    fact = {"key": asked["key"], "value": asked["choices"][0]["id"]}

    sent = client.post(
        f"/answer/{paused}",
        json={"links": [], "facts": [fact], "save": save, "revision": 0},
        headers=SAME_ORIGIN,
    )

    assert sent.status_code == 400, sent.text
    assert "bytes" in sent.json()["message"]


def test_the_page_and_settles_agree_on_every_facet_answer():
    """The page counts coverage in its own code, a second reader of
    ``FactAnswer.settles``, so the two are asked the same answers here."""
    import itertools

    store = valid_model().data_stores[0]
    fact = facet_fact(store, "audit-evidence")
    question = {
        **fact,
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
    choices = ["", *FACET_ANSWERS, "unknown"]
    combos = list(itertools.product(choices, repeat=len(fact["facets"])))
    steps = f"""
const tally = () => box.all("div").map(d => d.textContent).filter(t => t.startsWith("Your"))[0];
const selects = box.all("select");
for (const combo of {json.dumps(combos)}) {{
  combo.forEach((value, i) => {{ selects[i].value = value; selects[i].listeners.change(); }});
  calls.push({{ covered: tally().startsWith("Your answers cover every question for 1") }});
}}
"""
    seen = _run_answer_block(payloads, steps)["calls"]

    for combo, page in zip(combos, seen, strict=True):
        given = {
            facet["id"]: value
            for facet, value in zip(fact["facets"], combo, strict=True)
            if value
        }
        settles = bool(given) and (
            FactAnswer.model_validate({"key": fact["key"], "facets": given}).settles
        )
        assert page["covered"] is settles, combo


def test_a_report_whose_follow_up_started_asks_nothing_and_says_where_it_went(tiers):
    """The first-run app still offered a report's follow-up after its answers
    started one, and every answer to it was refused (#1369)."""
    client = client_for(tiers, PausingRunner(catalog=False), catalog=False)
    finished = start(client, questions=False)
    client.get(f"/events/{finished}")
    page = client.get(f"/report/{finished}").text
    (question,) = json.loads(
        re.search(r'id="fact_questions"[^>]*>(.*?)</script>', page).group(1)
    )
    answer = {"key": question["key"], "value": "TLS 1.3"}
    child = client.post(
        f"/answer/{finished}",
        json={"links": [], "facts": [answer]},
        headers=SAME_ORIGIN,
    ).json()["run"]
    client.get(f"/events/{child}")

    page = client.get(f"/report/{finished}").text
    slot = re.search(r'id="resumed_by"[^>]*>(.*?)</script>', page).group(1)
    asked = re.search(r'id="fact_questions"[^>]*>(.*?)</script>', page).group(1)

    assert json.loads(slot) == child
    assert json.loads(asked) == []


@pytest.mark.parametrize(
    "case", sorted(path.name for path in (PROJECT_ROOT / "evals/corpus").iterdir())
)
def test_the_page_opens_a_round_with_the_questions_the_service_counts(case):
    """The page hides a part until its parent's "yes", and the service counts
    a round without such a part: two readers of one rule, so a real ASVS
    level 2 round is given to both (#1378)."""
    from analysis_service.answer_round import question_set
    from analysis_service.system_model import SystemModel
    from webapp.main import early_rows

    path = PROJECT_ROOT / "evals/corpus" / case / "model.json"
    model = SystemModel.model_validate_json(path.read_text())
    asked = question_set(
        model,
        None,
        {"asvs": {"level": 2}},
        [],
        waiting=True,
        answered=[],
        answered_links=[],
        final=False,
        shown=[],
    )
    keys = {q.key for q in asked.early}
    counted = [
        q for q in asked.early if q.kind == "capability" and q.parent not in keys
    ]
    capabilities = [list(q.key) for q in asked.early if q.kind == "capability"]
    steps = f"""
await ids.analyze.listeners.submit({{ preventDefault() {{}} }}); await settle();
streams[0].listeners.questions({{ data: JSON.stringify({{ run: "r1", questions: [],
  facts: {json.dumps(early_rows(asked))}, remaining: {{}}, answered: [],
  answered_links: [], revision: 0 }}) }});
const walk = (n, out = []) => {{ for (const c of n.children || [])
  if (typeof c === "object") {{ out.push(c); walk(c, out); }} return out; }};
const asked = new Set({json.dumps(capabilities)}.map(k => JSON.stringify(k)));
const shown = walk(ids.questions).filter(n => n.tag === "p" && !n.hidden
  && walk(n).some(k => k.dataset && asked.has(k.dataset.key)));
calls.push({{ shown: shown.length }});
"""
    seen = _run_form_script(steps)["calls"]

    assert seen[-1]["shown"] == len(counted)
    assert len(counted) < len(capabilities), "a control: the round holds a part"


class TestEarlierAnswers:
    """A first report takes a new answer to a fact the pause answered (#1289, F2)."""

    def first_report(self, tiers, value, links=()):
        """A client, the runner, and the report the pause's answers started."""
        runner = PausingRunner()
        client = client_for(tiers, runner)
        paused = start(client, questions=True)
        shown = event(client.get(f"/events/{paused}").text, "questions")
        key = shown["facts"][0]["key"]
        started = client.post(
            f"/answer/{paused}",
            json={
                "links": list(links),
                "facts": [{"key": key, "value": value}],
                "revision": 0,
            },
            headers=SAME_ORIGIN,
        )
        assert started.status_code == 200, started.text
        report = started.json()["run"]
        client.get(f"/events/{report}")
        return client, runner, report, key

    def earlier(self, client, run):
        page = client.get(f"/report/{run}").text
        match = re.search(r'id="earlier"[^>]*>(.*?)</script>', page, re.DOTALL)
        return json.loads(match.group(1))

    def follow_up(self, client, run, links=(), facts=()):
        return client.post(
            f"/answer/{run}",
            json={"links": list(links), "facts": list(facts)},
            headers=SAME_ORIGIN,
        )

    @pytest.mark.parametrize(("before", "after"), [("yes", "no"), ("unknown", "yes")])
    def test_the_page_carries_an_earlier_answer_and_its_follow_up_reads_a_new_one(
        self, tiers, before, after
    ):
        client, runner, report, key = self.first_report(tiers, before)

        (row,) = self.earlier(client, report)["answers"]
        assert row["key"] == key
        assert row["answer"]["value"] == before

        response = self.follow_up(client, report, facts=[{"key": key, "value": after}])
        assert response.status_code == 200, response.text
        client.get(f"/events/{response.json()['run']}")
        assert [fact.value for fact in runner.resumed_facts[-1]] == [after]

    def test_the_follow_up_reads_a_new_link_answer(self, tiers):
        client, runner, report, _ = self.first_report(tiers, "yes", links=[LINK])

        (row,) = self.earlier(client, report)["links"]
        assert row["principal"] == LINK["principal"]
        assert row["answer"]["element"] == LINK["element"]
        assert LINK["element"] in row["options"]

        moved = {"principal": LINK["principal"], "element": "none"}
        response = self.follow_up(client, report, links=[moved])
        assert response.status_code == 200, response.text
        client.get(f"/events/{response.json()['run']}")
        assert [link.element for link in runner.resumed_links[-1]] == ["none"]

    def test_a_final_report_and_a_held_one_carry_none(self, tiers):
        client, _, report, key = self.first_report(tiers, "yes")
        final = self.follow_up(client, report, facts=[{"key": key, "value": "no"}])
        final = final.json()["run"]
        client.get(f"/events/{final}")

        assert self.earlier(client, report) == {}
        assert self.earlier(client, final) == {}


def test_a_fact_the_follow_up_still_asks_is_not_an_earlier_answer():
    from webapp.main import _earlier_payload

    report = sample_report([])
    answer = FactAnswer(key=FACT["key"], value="TLS")

    assert _earlier_payload(report, [answer], [], {FACT["key"]})["answers"] == []
    assert _earlier_payload(report, [answer], [], set())["answers"]


EARLIER_ROW = {
    "key": list(FACT["key"]),
    "label": "login: encryption in transit",
    "form": "control",
    "choices": [],
    "facets": [],
    "suggestions": [],
    "max_length": 200,
    "answer": dict(FACT) | {"key": list(FACT["key"])},
}
EARLIER_LINK = {
    "principal": "customer accounts",
    "options": ["entity:customer"],
    "answer": LINK,
}


def test_the_report_page_sends_a_changed_earlier_answer_with_the_follow_up():
    payloads = {
        "report": {"system_model": valid_model().model_dump(mode="json")},
        "link_questions": [],
        "fact_questions": [],
        "earlier": {"answers": [EARLIER_ROW], "links": [EARLIER_LINK]},
    }
    steps = """
calls.push(box.all("summary").map(node => node.textContent));
const buttons = box.all("button");
await buttons[1].listeners.click();
const [state] = box.all("select");
state.value = "none"; state.listeners.change();
await buttons[buttons.length - 1].listeners.click();
"""
    summaries, sent = _run_answer_block(payloads, steps)["calls"]

    assert summaries[0].startswith("Optional follow-up: 0 question(s)")
    assert "2 earlier answer(s)" in summaries[0]
    assert sent["url"] == "/answer/r1"
    assert sent["body"] == {
        "links": [],
        "facts": [{"key": list(FACT["key"]), "value": "none"}],
    }


def test_the_report_page_sends_a_changed_earlier_link():
    payloads = {
        "report": {"system_model": valid_model().model_dump(mode="json")},
        "link_questions": [],
        "fact_questions": [],
        "earlier": {"answers": [], "links": [EARLIER_LINK]},
    }
    steps = """
const buttons = box.all("button");
await buttons[0].listeners.click();
const [select] = box.all("select");
select.value = "none";
await buttons[buttons.length - 1].listeners.click();
"""
    (sent,) = _run_answer_block(payloads, steps)["calls"]

    assert sent["body"] == {
        "links": [{"principal": "customer accounts", "element": "none"}],
        "facts": [],
    }


class TestCompoundAnswers:
    """The page's facet editors, held against admission and the merge.

    The page sent a facet answer the follow-up refuses (F2a), and showed a
    retained facet blank and counted it unanswered (F2b, #1289). Each test
    serializes answers with the shipped script and hands them to production
    code.
    """

    report = sample_report([])
    store = valid_model().data_stores[0]
    key = (store.id, "", "", "", "audit-evidence", "")
    facets = tuple(f.id for f in QUESTION_KINDS["audit-evidence"].facets)

    def earlier(self, facets):
        return FactAnswer.model_validate({"key": self.key, "facets": facets})

    def question(self):
        return {
            **facet_fact(self.store, "audit-evidence"),
            "basis": "evidence",
            "cited_by": 1,
            "covered_so_far": 1,
            "findings": ["stride/R-01"],
        }

    def payloads(self, answer, *, asked):
        from webapp.main import _earlier_payload

        earlier = _earlier_payload(
            self.report, [answer], [], {self.key} if asked else set()
        )
        return {
            "report": {
                "system_model": self.report.system_model.model_dump(mode="json")
            },
            "link_questions": [],
            "fact_questions": [self.question()] if asked else [],
            "earlier": json.loads(json.dumps(earlier)),
        }

    # Every choice each list offers, set in turn: the lists, the line that
    # counts coverage, and the body the follow-up button sends.
    STEPS = """
const send = () => { const b = box.all("button"); return b[b.length - 1]; };
if (OPEN) await box.all("button")[0].listeners.click();
const selects = box.all("select");
const offered = selects.map(s => s.children.map(o => o.value));
calls.push({ offered, shown: selects.map(s => s.value) });
const combos = offered.reduce((all, values) =>
  all.flatMap(c => values.map(v => [...c, v])), [[]]);
for (const combo of combos) {
  combo.forEach((v, i) => { selects[i].value = v; selects[i].listeners.change(); });
  const tally = box.all("div").map(d => d.textContent).filter(t => t.startsWith("Your"))[0];
  await send().listeners.click();
  calls.push({ combo, tally: tally || null });
}
"""

    def drive(self, answer, *, asked):
        steps = self.STEPS.replace("OPEN", "false" if asked else "true")
        seen = _run_answer_block(self.payloads(answer, asked=asked), steps)["calls"]
        lists, rows = seen[0], seen[1:]
        # Each click pushes the fetch body before its own row.
        return lists, list(zip(rows[1::2], rows[0::2], strict=True))

    def admitted(self, answer, body):
        facts = [FactAnswer.model_validate(f) for f in body["body"]["facts"]]
        check_fact_answers(facts, self.report.system_model, None, [answer])
        return facts

    @pytest.mark.parametrize(
        "before",
        [
            {"records-actor": "yes", "record-protected": "no"},
            {"records-actor": "yes", "record-protected": "unknown"},
            {"records-actor": "unknown", "record-protected": "unknown"},
        ],
    )
    def test_every_change_the_editor_offers_is_admitted(self, before):
        """F2a: complete-known to all-unknown was offered and refused."""
        answer = self.earlier(before)
        lists, rows = self.drive(answer, asked=False)

        assert lists["shown"] == [before[f] for f in self.facets]
        for facet, offered in zip(self.facets, lists["offered"], strict=True):
            assert "" not in offered, "a facet answered before keeps its answer"
            assert ("unknown" in offered) is (before[facet] == "unknown")
        for row, body in rows:
            facts = self.admitted(answer, body)
            changed = {
                f: v
                for f, v in zip(self.facets, row["combo"], strict=True)
                if v != before[f]
            }
            assert [f.facets for f in facts] == ([changed] if changed else [])

    def test_a_retained_facet_is_shown_and_counted_as_the_merge_reads_it(self):
        """F2b: a saved facet showed blank, and the count read only new lists."""
        answer = self.earlier({"records-actor": "yes"})
        lists, rows = self.drive(answer, asked=True)

        assert lists["shown"] == ["yes", ""]
        assert lists["offered"][0] == ["yes", "no", "not applicable"]
        assert lists["offered"][1] == ["", *FACET_ANSWERS, "unknown"]
        for row, body in rows:
            facts = self.admitted(answer, body)
            (merged,) = merged_facts([answer], facts)
            assert merged.facets == {
                f: v for f, v in zip(self.facets, row["combo"], strict=True) if v
            }
            covered = row["tally"].startswith("Your answers cover every question for 1")
            assert covered is merged.settles, row["combo"]
            assert len(facts) <= 1, "one editor, one answer a key"

    def test_a_refused_follow_up_keeps_what_was_entered(self):
        """A refusal re-enables the button and leaves every list as it was."""
        answer = self.earlier({"records-actor": "yes"})
        steps = """
const [, protectedList] = box.all("select");
protectedList.value = "no"; protectedList.listeners.change();
const send = box.all("button")[box.all("button").length - 1];
globalThis.fetch = async (url, init) => {
  calls.push({ body: JSON.parse(init.body) });
  return { ok: false, json: async () => ({ message: "refused here" }) };
};
await send.listeners.click();
calls.push({ note: box.all("div").map(d => d.textContent).includes("refused here"),
  disabled: send.disabled, shown: box.all("select").map(s => s.value) });
await send.listeners.click();
"""
        payloads = self.payloads(answer, asked=True)
        first, after, second = _run_answer_block(payloads, steps)["calls"]

        assert after == {"note": True, "disabled": False, "shown": ["yes", "no"]}
        assert first == second
        assert first["body"]["facts"] == [
            {"key": list(self.key), "facets": {"record-protected": "no"}}
        ]


def test_the_follow_up_starts_a_question_from_its_retained_answer():
    from webapp.main import _earlier_payload

    key = TestCompoundAnswers.key
    answer = FactAnswer.model_validate({"key": key, "facets": {"records-actor": "yes"}})
    payload = _earlier_payload(sample_report([]), [answer], [], {key})

    assert payload["answers"] == []
    assert payload["retained"] == [answer.model_dump(mode="json")]

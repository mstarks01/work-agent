"""A refusal names the field and the rule, never the value the caller sent.

Each case sends one marker, shaped like an API key, in the part of a body
that a refusal answers, through ``/v1`` or a first-run app route, and reads
the whole response. A submitter can paste a secret into any free-text part,
so a refusal that repeats what it refused hands the secret back to whatever
logs or renders the response.
"""

from __future__ import annotations

import asyncio

import pytest

from analysis_service.claims import UnknownRef
from analysis_service.jobs import JobRecord
from analysis_service.question_kinds import QUESTION_KINDS
from tests import test_webapp
from tests.factories import asking_threat, sample_report, sample_selection, valid_model
from tests.test_api import admit, auth, make_client
from tests.test_questions import DESCRIPTION
from tests.test_sitting_app import (
    CASE,
    OWN_LIST,
    browser,
    build_tree,
    every_mark,
    read_and_record,
    session_for,
)
from tests.test_webapp import SAME_ORIGIN, posted
from tests.test_webapp_questions import (
    FACT,
    PausingRunner,
    client_for,
    ran,
    start,
)

#: The shipped tier config, as the webapp tests build it.
tiers = test_webapp.tiers

MARKER = "sk_ant_api03_EchoMarker7f3a9"

#: A process the sample model holds, and an open capability with a closed set.
PROCESS = valid_model().processes[0].id
CAPABILITY = ("", "", "", "", "", "browser-frontend")
FACETED = next(name for name, kind in QUESTION_KINDS.items() if kind.facets)


def key(**parts: str) -> list[str]:
    """A six-part fact key with ``parts`` set and every other part blank."""
    fields = ("element_id", "attribute", "assertion", "subject", "question")
    return [parts.get(name, "") for name in fields] + [parts.get("capability", "")]


def fact(fact_key: list[str], value: str = "unknown", **more) -> dict:
    return {"key": fact_key, "value": value} | more


SOURCE = {"kind": "description", "label": "a", "text": "b"}
STRIDE = {"name": "stride", "options": {}}

#: Each refusal of ``/v1``: (route, body). ``{job}`` is a finished report.
V1_CASES = {
    "an invented body key": (
        "/v1/jobs",
        {"sources": [SOURCE], "frameworks": [STRIDE], MARKER: 1},
    ),
    "an invented option key": (
        "/v1/jobs",
        {
            "sources": [SOURCE],
            "frameworks": [{"name": "stride", "options": {MARKER: 1}}],
        },
    ),
    "a repeated source label": (
        "/v1/jobs",
        {
            "sources": [SOURCE | {"label": MARKER}, SOURCE | {"label": MARKER}],
            "frameworks": [STRIDE],
        },
    ),
    "a label in an over-budget job": (
        "/v1/jobs",
        {
            "sources": [SOURCE | {"label": MARKER, "text": "x" * 600}],
            "frameworks": [STRIDE],
        },
    ),
    "an unknown source kind": (
        "/v1/jobs",
        {"sources": [SOURCE | {"kind": MARKER}], "frameworks": [STRIDE]},
    ),
    "a value outside a closed set": (
        "/v1/jobs/{job}/answers",
        {"facts": [fact(key(capability=CAPABILITY[-1]), MARKER)]},
    ),
    "an unknown element": (
        "/v1/jobs/{job}/answers",
        {"facts": [fact(key(element_id=MARKER, attribute="exposure"))]},
    ),
    "an unknown attribute": (
        "/v1/jobs/{job}/answers",
        {"facts": [fact(key(element_id=PROCESS, attribute=MARKER))]},
    ),
    "an unknown capability": (
        "/v1/jobs/{job}/answers",
        {"facts": [fact(key(capability=MARKER))]},
    ),
    "an unknown assertion row": (
        "/v1/jobs/{job}/answers",
        {"facts": [fact(key(assertion=MARKER))]},
    ),
    "an unknown question kind": (
        "/v1/jobs/{job}/answers",
        {"facts": [fact(key(element_id=MARKER, question=MARKER))]},
    ),
    "a key in two spellings": (
        "/v1/jobs/{job}/answers",
        {
            "facts": [
                fact(key(element_id=MARKER, attribute="exposure", assertion=MARKER))
            ]
        },
    ),
    "an unasked subject": (
        "/v1/jobs/{job}/answers",
        {"facts": [fact(key(subject=MARKER), "x")]},
    ),
    "an unknown facet": (
        "/v1/jobs/{job}/answers",
        {
            "facts": [
                {
                    "key": key(element_id=PROCESS, question=FACETED),
                    "facets": {MARKER: "yes"},
                }
            ]
        },
    ),
    "a facet answer outside its set": (
        "/v1/jobs/{job}/answers",
        {
            "facts": [
                {
                    "key": key(element_id=PROCESS, question=FACETED),
                    "facets": {QUESTION_KINDS[FACETED].facets[0].id: MARKER},
                }
            ]
        },
    ),
}


def _finished(store) -> str:
    """A finished report that asks a free-text fact and a closed one, in ``store``."""
    record = JobRecord.create(
        owner_id="alice", sources=[DESCRIPTION], frameworks=sample_selection()
    )
    record.transition("running")
    transport = UnknownRef(
        element_id=valid_model().data_flows[1].id, attribute="encryption_in_transit"
    )
    capability = UnknownRef(capability=CAPABILITY[-1])
    record.report = sample_report([asking_threat(transport, capability)])
    record.transition("completed")
    asyncio.run(admit(store, record))
    return record.id


#: Each refusal of the first-run app: (run, route, body). The run is
#: ``paused``, ``finished`` or ``final``; ``{run}`` is its ID.
APP_CASES = {
    "an invented option key": (
        None,
        "/analyze",
        posted("x") | {"frameworks": [{"name": "stride", "options": {MARKER: 1}}]},
    ),
    "a repeated source label": (
        None,
        "/analyze",
        posted("x") | {"sources": [SOURCE | {"label": MARKER}] * 2},
    ),
    "a link to an unasked principal": (
        "finished",
        "/answer/{run}",
        {"links": [{"principal": MARKER, "element": "none"}], "facts": []},
    ),
    "a link principal twice": (
        "finished",
        "/answer/{run}",
        {"links": [{"principal": MARKER, "element": "none"}] * 2, "facts": []},
    ),
    "an answer to an unasked fact": (
        "finished",
        "/answer/{run}",
        {"links": [], "facts": [fact(key(subject=MARKER))]},
    ),
    "an unknown element": (
        "finished",
        "/answer/{run}",
        {"links": [], "facts": [fact(key(element_id=MARKER, attribute="exposure"))]},
    ),
    "an unknown facet": (
        "finished",
        "/answer/{run}",
        {
            "links": [],
            "facts": [
                {
                    "key": key(element_id=PROCESS, question=FACETED),
                    "facets": {MARKER: "yes"},
                }
            ],
        },
    ),
    "a link to an element the model does not hold": (
        "finished",
        "/answer/{run}",
        {
            "links": [
                {
                    "principal": "customer accounts",
                    "element": "process:sk-ant-echomarker-7f3a9",
                }
            ],
            "facts": [],
        },
    ),
    "a skip of an unshown link question": (
        "paused",
        "/answer/{run}",
        {"links": [], "facts": [], "save": True, "skip": [MARKER], "revision": 0},
    ),
    "a skip of an unshown fact": (
        "paused",
        "/answer/{run}",
        {
            "links": [],
            "facts": [],
            "save": True,
            "skip": [key(subject=MARKER)],
            "revision": 0,
        },
    ),
    "a presented key the list does not hold": (
        "paused",
        "/answer/{run}",
        {
            "links": [],
            "facts": [],
            "save": True,
            "skip": [key(subject=MARKER)],
            "presented": [key(subject=MARKER)],
            "revision": 0,
        },
    ),
    "a correction of an unread answer": (
        "final",
        "/correct/{run}",
        {"facts": [fact(key(element_id=MARKER, attribute="exposure"))], "revision": 0},
    ),
}


def _run(client, state: str | None) -> str:
    """A run of the first-run app in ``state``."""
    if state is None:
        return ""
    if state == "paused":
        run = start(client, questions=True)
        assert ran(client, run) == "awaiting-answers"
        return run
    finished = start(client, questions=False)
    assert ran(client, finished) == "completed"
    if state == "finished":
        return finished
    final = client.post(
        f"/answer/{finished}",
        json={"links": [], "facts": [dict(FACT) | {"key": list(FACT["key"])}]},
        headers=SAME_ORIGIN,
    ).json()["run"]
    client.get(f"/events/{final}")
    return final


@pytest.mark.parametrize(
    ("surface", "case"),
    [("v1", name) for name in V1_CASES] + [("app", name) for name in APP_CASES],
)
def test_a_refusal_never_repeats_what_the_caller_sent(surface, case, tiers):
    """Each refusal answers 4xx, and its body holds no part of the marker."""
    if surface == "v1":
        client, store = make_client()
        route, body = V1_CASES[case]
        route = route.format(job=_finished(store))
        response = client.post(route, json=body, headers=auth())
    else:
        state, route, body = APP_CASES[case]
        catalog = state != "final"
        client = client_for(tiers, PausingRunner(catalog=catalog), catalog=catalog)
        route = route.format(run=_run(client, state))
        response = client.post(route, json=body, headers=SAME_ORIGIN)
    assert 400 <= response.status_code < 500, response.text
    assert "echomarker" not in response.text.lower(), response.text


def test_a_refused_amendment_never_prints_pydantic_s_input(tiers):
    """A blank amendment fails the source's own rule, whose text quotes its input."""
    client = client_for(tiers, PausingRunner())
    run = _run(client, "paused")
    response = client.post(
        f"/amend/{run}", json={"text": " ", "revision": 0}, headers=SAME_ORIGIN
    )
    assert response.status_code == 400, response.text
    assert "input_value" not in response.text, response.text


def test_a_sitting_refusal_names_a_mark_by_its_position(tmp_path):
    """``POST /api/finish`` names a mark it refuses by its place in ``marks``."""
    app = browser(session_for(build_tree(tmp_path), "ada", CASE))
    app.post("/api/own-list", json={"case": CASE, "items": OWN_LIST})
    marks = every_mark(app) | {MARKER: "agree"}

    response = app.post(
        "/api/finish",
        json={"case": CASE, "marks": marks, "missing": [], "notes": ""},
    )

    assert response.status_code == 409, response.text
    assert "echomarker" not in response.text.lower(), response.text
    assert f"marks[{len(marks) - 1}]" in response.json()["detail"]


def test_a_package_refusal_names_a_saved_mark_by_its_position(tmp_path):
    """A recorded draft can take a mark later, and the package check refuses it."""
    app = browser(session_for(build_tree(tmp_path), "ada", CASE))
    assert read_and_record(app).status_code == 200
    marks = every_mark(app) | {MARKER: "agree"}
    app.post(
        "/api/draft",
        json={"case": CASE, "marks": marks, "missing": [], "notes": ""},
    )

    response = app.post("/api/contribution-preview", json={"author": "ada"})

    assert response.status_code == 409, response.text
    assert "echomarker" not in response.text.lower(), response.text
    assert f"marks[{len(marks) - 1}]" in response.json()["detail"]

"""The web app's JSON routes and /v1 read each body by the same rules.

Each test posts one body to both entry points and holds the two answers to
each other, so a rule that one entry point reads differently fails here.
"""

from __future__ import annotations

import pytest

from analysis_service.answer_state import MAX_SKIPS
from analysis_service.api import AnswersSubmission
from analysis_service.fact_answers import MAX_FACT_ANSWERS, MAX_HELD_FACTS
from analysis_service.links import MAX_LINK_ANSWERS
from tests import test_webapp
from tests.test_api import auth, make_client
from tests.test_webapp import CARRIED, SAME_ORIGIN, posted
from tests.test_webapp_questions import (
    FACT,
    PausingRunner,
    client_for,
    ran,
    start,
)

tiers = test_webapp.tiers

TEXT = "A web app talks to a database."


def v1_refusal(body: dict) -> tuple[int, str]:
    """The status /v1 answers ``body`` with, and its refusal as one sentence."""
    client, _ = make_client(frameworks=CARRIED)
    answer = client.post("/v1/jobs", json=body, headers=auth())
    problem = answer.json()
    errors = problem.get("errors")
    if not errors:
        return answer.status_code, problem.get("detail", "")
    return answer.status_code, "; ".join(
        f"{error['loc'].removeprefix('body').removeprefix('.')}: {error['msg']}"
        for error in errors
    )


def web_refusal(client, body: dict) -> tuple[int, str]:
    answer = client.post("/analyze", json=body, headers=SAME_ORIGIN)
    return answer.status_code, answer.json().get("message", "")


@pytest.mark.parametrize("questions", ["yes", "on", "true", 1, 0])
def test_a_questions_flag_that_is_not_a_boolean_is_refused_by_both(tiers, questions):
    body = posted(TEXT) | {"questions": questions}

    api_status, api_message = v1_refusal(body)
    web_status, web_message = web_refusal(client_for(tiers, PausingRunner()), body)

    assert api_status == 422, api_message
    assert web_status == 400, web_message
    assert web_message == api_message


def _source(**change) -> dict:
    body = posted(TEXT)
    body["sources"][0] |= change
    return body


def _framework(**change) -> dict:
    body = posted(TEXT)
    body["frameworks"][0] |= change
    return body


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(_source(text="   "), id="blank-text"),
        pytest.param(_source(label="a\nb"), id="label-line-break"),
        pytest.param(_source(colour="red"), id="source-extra-key"),
        pytest.param(_source(kind="answers"), id="answers-kind-plain-label"),
        pytest.param(_framework(name="nope"), id="unknown-framework"),
        pytest.param(_framework(colour="red"), id="framework-extra-key"),
        pytest.param(posted(TEXT) | {"frameworks": "stride"}, id="frameworks-not-list"),
        pytest.param(posted(TEXT) | {"answers_from": 7}, id="answers-from-not-text"),
    ],
)
def test_the_web_app_names_the_field_and_the_rule_v1_names(tiers, body):
    api_status, api_message = v1_refusal(body)
    web_status, web_message = web_refusal(client_for(tiers, PausingRunner()), body)

    assert api_status in (400, 422), api_message
    assert web_status == 400, web_message
    assert web_message == api_message


def test_a_refusal_never_repeats_the_value_it_refused(tiers):
    body = _source(label="secret\nvalue")

    _, message = web_refusal(client_for(tiers, PausingRunner()), body)

    assert "secret" not in message
    assert message.startswith("sources.0.label: ")


def _paused(tiers):
    client = client_for(tiers, PausingRunner())
    run = start(client, questions=True)
    assert ran(client, run) == "awaiting-answers"
    return client, run


def _final(tiers):
    client = client_for(tiers, PausingRunner(catalog=False), catalog=False)
    finished = start(client, questions=False)
    assert ran(client, finished) == "completed"
    final = client.post(
        f"/answer/{finished}",
        json={"links": [], "facts": [dict(FACT)]},
        headers=SAME_ORIGIN,
    ).json()["run"]
    assert ran(client, final) == "completed"
    return client, final


FACT_BODY = {"key": list(FACT["key"]), "value": "unknown"}


@pytest.mark.parametrize(
    ("state", "route", "body"),
    [
        pytest.param(_paused, "answer", {"links": [], "facts": [], "revision": 0}),
        pytest.param(_paused, "amend", {"text": "A CDN fronts it.", "revision": 0}),
        pytest.param(_final, "correct", {"facts": [FACT_BODY], "revision": 0}),
    ],
)
def test_each_answer_route_refuses_a_key_it_does_not_read(tiers, state, route, body):
    """As the /v1 models refuse one (``extra="forbid"``)."""
    client, run = state(tiers)

    refused = client.post(
        f"/{route}/{run}", json=body | {"colour": "red"}, headers=SAME_ORIGIN
    )

    assert refused.status_code == 400, refused.text
    assert "colour" not in refused.json()["message"]
    assert (
        client.post(f"/{route}/{run}", json=body, headers=SAME_ORIGIN).status_code
        == 200
    )


def test_analyze_refuses_a_key_it_does_not_read(tiers):
    client = client_for(tiers, PausingRunner())
    body = posted(TEXT) | {"colour": "red"}

    api_status, _ = v1_refusal(body)
    web_status, web_message = web_refusal(client, body)

    assert api_status == 422
    assert web_status == 400, web_message
    assert "colour" not in web_message


def test_a_correction_over_the_limit_is_refused_with_the_limit(tiers):
    client, final = _final(tiers)
    body = {"facts": [FACT_BODY] * (MAX_FACT_ANSWERS + 1), "revision": 0}

    refused = client.post(f"/correct/{final}", json=body, headers=SAME_ORIGIN)

    assert refused.status_code == 400
    assert str(MAX_FACT_ANSWERS) in refused.json()["message"]


@pytest.mark.parametrize(
    ("field", "answer", "bound"),
    [
        ("links", {"principal": "the team", "element": "web-app"}, MAX_LINK_ANSWERS),
        ("facts", FACT_BODY, MAX_FACT_ANSWERS),
        ("skip", "web-app", MAX_SKIPS),
        ("presented", FACT_BODY["key"], MAX_HELD_FACTS),
    ],
)
def test_answers_over_the_limit_are_refused_with_the_v1_bound(
    tiers, field, answer, bound
):
    """``/answer`` holds each list to the bound ``AnswersSubmission`` declares,
    and answers 400 where /v1 answers 422 (the web app's own status rule)."""
    client, final = _final(tiers)
    body = {"links": [], "facts": [], field: [answer] * (bound + 1), "revision": 0}

    refused = client.post(f"/answer/{final}", json=body, headers=SAME_ORIGIN)

    assert refused.status_code == 400
    assert str(bound) in refused.json()["message"]
    assert AnswersSubmission.model_fields[field].metadata[0].max_length == bound

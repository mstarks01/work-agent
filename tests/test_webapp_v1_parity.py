"""The web app's JSON routes and /v1 read each body by the same rules.

Each test posts one body to both entry points and holds the two answers to
each other, so a rule that one entry point reads differently fails here.
"""

from __future__ import annotations

import pytest

from tests import test_webapp
from tests.test_api import auth, make_client
from tests.test_webapp import CARRIED, SAME_ORIGIN, posted
from tests.test_webapp_questions import PausingRunner, client_for

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


def test_analyze_refuses_a_key_it_does_not_read(tiers):
    client = client_for(tiers, PausingRunner())
    body = posted(TEXT) | {"colour": "red"}

    api_status, _ = v1_refusal(body)
    web_status, web_message = web_refusal(client, body)

    assert api_status == 422
    assert web_status == 400, web_message
    assert "colour" not in web_message

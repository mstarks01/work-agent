"""A shared answer is stored with the parts it covers (#2029 package 2, ADR 0114)."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from pydantic import TypeAdapter, ValidationError

from analysis_service.answer_bodies import BodyRefused, read_answers
from analysis_service.answer_sets import (
    AnswerSet,
    HeldAnswerSet,
    HeldSentAnswers,
    SentAnswers,
)
from analysis_service.answer_shares import SharedAnswer, check_shares
from analysis_service.answer_state import Answers, SavedAnswers
from analysis_service.fact_answers import MAX_FACT_ANSWERS, MAX_HELD_FACTS, FactAnswer
from analysis_service.jobs import JobRecord
from analysis_service.system_model import SystemModel
from evals.harness.early_rounds import paused_state
from tests.factories import sample_report, sample_selection, sample_threat, valid_model
from tests.test_api import admit, auth, make_client
from tests.test_questions import DESCRIPTION

AUTH = ("", "authentication", "", "", "", "")


def _model() -> SystemModel:
    model = valid_model()
    extra = model.data_flows[0].model_copy(
        update={
            "id": "flow:process:web-app>store:orders-db>read-order",
            "name": "read order",
        }
    )
    model.data_flows.append(extra)
    model = SystemModel.model_validate(model.model_dump())
    for flow in model.data_flows:
        flow.authentication = "unknown"
    return model


MODEL = _model()
STORE, LOGIN, READ = (
    "flow:process:web-app>store:orders-db>store-order",
    "flow:entity:customer>process:web-app>login",
    "flow:process:web-app>store:orders-db>read-order",
)


def _key(element: str) -> tuple[str, ...]:
    return (element, *AUTH[1:])


def _share(value: str, *covers: str) -> SharedAnswer:
    return SharedAnswer(answer=FactAnswer(key=AUTH, value=value), covers=covers)


def _save(held: AnswerSet, given: SentAnswers) -> AnswerSet:
    state = replace(paused_state(MODEL, {"stride": {}}, (), ()), answers=held)
    presented = [q.key for q in state.questions.early]
    saved = state.answer(
        Answers(given=given, save=True, presented=presented, revision=0), limits=None
    )
    assert isinstance(saved, SavedAnswers)
    return saved.answers


def _values(answers: AnswerSet) -> dict[str, str]:
    return {fact.key[0]: fact.value for fact in answers.facts}


def test_one_answer_covers_two_parts_and_leaves_a_third_unknown():
    held = _save(AnswerSet(), SentAnswers(shares=(_share("mutual TLS", STORE, READ),)))
    assert _values(held) == {STORE: "mutual TLS", READ: "mutual TLS"}
    assert held.shares == (_share("mutual TLS", STORE, READ),)


def test_a_later_shared_edit_keeps_an_exception():
    held = _save(AnswerSet(), SentAnswers(shares=(_share("mutual TLS", STORE, READ),)))
    own = FactAnswer(key=_key(READ), value="none; accepted by network position")
    held = _save(held, SentAnswers(facts=(own,)))
    assert held.shares == (_share("mutual TLS", STORE),)
    held = _save(held, SentAnswers(shares=(_share("API key", STORE),)))
    assert _values(held) == {
        STORE: "API key",
        READ: "none; accepted by network position",
    }


def test_taking_a_part_out_takes_back_only_the_shared_answer():
    own = FactAnswer(key=_key(LOGIN), value="password and session cookie")
    held = _save(
        AnswerSet(),
        SentAnswers(facts=(own,), shares=(_share("mutual TLS", STORE, READ),)),
    )
    held = _save(held, SentAnswers(shares=(_share("mutual TLS", STORE),)))
    assert _values(held) == {
        STORE: "mutual TLS",
        LOGIN: "password and session cookie",
    }
    held = _save(held, SentAnswers(shares=(_share("mutual TLS"),)))
    assert _values(held) == {LOGIN: "password and session cookie"}
    assert held.shares == ()


def test_a_share_writes_only_its_own_question():
    """An authentication share writes no other attribute of the parts it
    covers, such as who may do what at a service."""
    held = _save(AnswerSet(), SentAnswers(shares=(_share("company SSO", STORE, READ),)))
    assert {fact.key[1] for fact in held.facts} == {"authentication"}
    assert {fact.key[4] for fact in held.facts} == {""}


def test_a_part_takes_a_shared_answer_or_its_own_not_both():
    own = FactAnswer(key=_key(STORE), value="API key")
    with pytest.raises(ValueError, match="not both"):
        _save(AnswerSet(), SentAnswers(facts=(own,), shares=(_share("mTLS", STORE),)))
    with pytest.raises(ValueError, match="once"):
        _save(
            AnswerSet(),
            SentAnswers(shares=(_share("mTLS", STORE), _share("API key", READ))),
        )


def test_a_share_names_a_shape_and_each_part_once():
    with pytest.raises(ValidationError, match="leaves the element empty"):
        SharedAnswer(answer=FactAnswer(key=_key(STORE), value="mTLS"), covers=(READ,))
    with pytest.raises(ValidationError, match="each element once"):
        _share("mTLS", STORE, STORE)


def test_a_share_survives_the_store_and_the_request_body():
    held = _save(AnswerSet(), SentAnswers(shares=(_share("mutual TLS", STORE, READ),)))
    adapter: TypeAdapter[AnswerSet] = TypeAdapter(HeldAnswerSet)
    assert adapter.validate_json(adapter.dump_json(held)) == held
    body = {
        "shares": [held.shares[0].model_dump(mode="json")],
        "save": True,
        "revision": 0,
    }
    assert read_answers(body).given.shares == held.shares


def test_one_request_counts_each_covered_part_toward_its_bound():
    half = MAX_FACT_ANSWERS * 3 // 4
    shares = [
        {
            "answer": {"key": list(AUTH), "value": "mTLS"},
            "covers": [f"process:{side}{n}" for n in range(half)],
        }
        for side in "ab"
    ]
    with pytest.raises(BodyRefused, match="each part"):
        read_answers({"shares": shares, "save": True, "revision": 0})


def test_the_questions_route_returns_the_held_shares():
    """A reload and an API client read the coverage back (ADR 0114)."""
    held = _save(AnswerSet(), SentAnswers(shares=(_share("mutual TLS", STORE, READ),)))
    client, store = make_client()
    record = JobRecord.create(
        owner_id="alice",
        sources=[DESCRIPTION],
        frameworks=sample_selection(),
        answers=held,
    )
    record.transition("running")
    record.report = sample_report([sample_threat()])
    record.transition("completed")
    asyncio.run(admit(store, record))
    served = client.get(f"/v1/jobs/{record.id}/questions", headers=auth()).json()
    assert served["answered_shares"] == [held.shares[0].model_dump(mode="json")]
    assert served["draft_shares"] == []


def test_a_later_share_replaces_an_earlier_answer_of_a_part_it_covers():
    """A report's draft composes each save over the last (ADR 0070): a part
    answered on its own, then ticked, takes the share and is no clash."""
    own = SentAnswers(facts=(FactAnswer(key=_key(STORE), value="API key"),))
    later = SentAnswers(shares=(_share("mutual TLS", STORE, READ),)).over(own)
    assert later.facts == ()
    check_shares(later.shares, later.facts)


def test_a_draft_share_reaches_what_a_new_analysis_carries():
    """``held_answers`` writes a draft's shares out, so a job that carries
    this one's answers reads each covered part (ADR 0079)."""
    record = JobRecord.create(
        owner_id="alice",
        sources=[DESCRIPTION],
        frameworks=sample_selection(),
    )
    record = record.model_copy(
        update={"draft": SentAnswers(shares=(_share("mutual TLS", STORE, READ),))}
    )
    assert _values(record.held_answers()) == {STORE: "mutual TLS", READ: "mutual TLS"}


def test_a_draft_counts_each_covered_part_toward_the_held_limit():
    attributes = (
        "authentication",
        "encryption_in_transit",
        "protocol",
        "encryption_at_rest",
        "trust_zone",
        "exposure",
    )
    covers = tuple(f"process:p{n}" for n in range(MAX_FACT_ANSWERS))
    draft = SentAnswers(
        shares=tuple(
            SharedAnswer(
                answer=FactAnswer(key=("", name, "", "", "", ""), value="x"),
                covers=covers,
            )
            for name in attributes
        )
    )
    assert len(attributes) * MAX_FACT_ANSWERS > MAX_HELD_FACTS
    with pytest.raises(ValueError, match="each part"):
        TypeAdapter(HeldSentAnswers).validate_python(draft)

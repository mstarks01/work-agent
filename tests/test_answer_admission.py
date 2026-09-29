"""What a submission's fact answers may name, and how long a line they may write.

Each harness here is the one run 11 of the security audit measured with
(#1294): an answer at the documented limit that failed the resumed job, two
spellings of one fact that both landed, and answers to questions the service
never asked.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from analysis_service.answer_round import question_set
from analysis_service.assertions import MAX_QUOTE_CHARS, AssertionCatalog
from analysis_service.links import (
    LinkAnswer,
    NoCatalogError,
    apply_answers,
    check_answers,
    merged_facts,
)
from analysis_service.question_kinds import QUESTION_KINDS
from analysis_service.questions import FactAnswer, check_fact_answers, fact_line
from tests.factories import valid_model
from tests.test_api import auth
from tests.test_links import catalog_client
from tests.test_pause import held, waiting

FLOW = valid_model().data_flows[1].id


def open_description():
    model = valid_model()
    model.data_flows[1].data_description = "unknown"
    return model


def sized(key, length):
    """An answer to ``key`` whose line is ``length`` characters."""
    overhead = len(fact_line(FactAnswer(key=key, value="x"))) - 1
    return FactAnswer(key=key, value="x" * (length - overhead))


@pytest.mark.parametrize(
    "key",
    [
        ("", "", "", "who rotates the signing keys?", "", ""),
        ("", "", "", "s" * 300, "", ""),
        (FLOW, "data_description", "", "", "", ""),
    ],
    ids=["subject", "longest-subject", "attribute"],
)
class TestTheLineAnAnswerWrites:
    def test_the_longest_admitted_line_builds_its_span(self, key):
        """A 915-character answer completed and a 916-character one failed the
        resumed job: the check bounded the value, and the span quotes the line.
        """
        model = open_description()
        answer = sized(key, MAX_QUOTE_CHARS)
        check_fact_answers([answer], model, None)
        apply_answers(AssertionCatalog(subjects=[], entries=[]), model, [], [answer])

    def test_one_character_more_is_refused_before_admission(self, key):
        with pytest.raises(ValueError, match="line may hold"):
            check_fact_answers(
                [sized(key, MAX_QUOTE_CHARS + 1)], open_description(), None
            )


def test_a_subject_longer_than_a_reference_holds_is_refused():
    with pytest.raises(ValidationError):
        FactAnswer(key=("", "", "", "s" * 301, ""), value="yes")


class TestOneFactHasOneSpelling:
    @pytest.mark.parametrize(
        ("sent", "kept"),
        [
            (
                (FLOW, "", " ", " ", "code-execution", ""),
                (FLOW, "", "", "", "code-execution", ""),
            ),
            ((FLOW, "exposure", " ", "", "", ""), (FLOW, "exposure", "", "", "", "")),
            (("", "", "", "who?", "", ""), ("", "", "", "who?", "", "")),
            (("", "", " ", "", "", "oauth"), ("", "", "", "", "", "oauth")),
        ],
        ids=["question", "attribute", "subject", "capability"],
    )
    def test_a_part_the_spelling_does_not_read_is_blank(self, sent, kept):
        assert FactAnswer(key=sent, value="yes").key == kept

    def test_two_spellings_of_one_fact_merge_to_one_answer(self):
        """Both answers landed, and every lane prompt carried both lines."""
        first = FactAnswer(key=(FLOW, "", "", "", "code-execution", ""), value="yes")
        second = FactAnswer(key=(FLOW, "", " ", "", "code-execution", ""), value="no")
        assert merged_facts([first], [second]) == [second]

    def test_a_later_facet_answer_keeps_the_earlier_facets(self):
        """A size answer in round two lost the rate answer of round one
        (#1289, F2)."""
        key = (FLOW, "", "", "", "capacity-limits", "")
        rate = FactAnswer(key=key, facets={"rate": "yes"})
        size = FactAnswer(key=key, facets={"size": "yes"})
        both = FactAnswer(key=key, facets={"rate": "yes", "size": "yes"})

        assert merged_facts([rate], [size]) == [both]
        assert merged_facts([size], [rate]) == [both]
        assert merged_facts([both], [size]) == [both]
        changed = FactAnswer(key=key, facets={"rate": "no"})
        assert merged_facts([both], [changed]) == [
            FactAnswer(key=key, facets={"rate": "no", "size": "yes"})
        ]

    def test_a_key_with_two_spellings_is_still_refused(self):
        answer = FactAnswer(
            key=(FLOW, "exposure", "", "who?", "", ""), value="internal"
        )
        with pytest.raises(ValueError, match="names one fact"):
            check_fact_answers([answer], valid_model(), None)


class TestOnlyAnAskedFactTakesAnAnswer:
    """54 of 54 unasked element and kind pairs were admitted, and an invented
    subject reached every lane prompt."""

    def asked(self):
        checkpoint = held()
        return question_set(
            checkpoint.system_model,
            checkpoint.assertions.catalog,
            {"stride": {}},
            [],
            waiting=True,
        ).asked

    def unasked_kind(self):
        asked = self.asked()
        for element in valid_model().elements():
            for name, kind in QUESTION_KINDS.items():
                key = (element.id, "", "", "", name, "")
                if key not in asked and kind.answer == "yes-no":
                    return key
        pytest.fail("every yes-no kind is asked of every element")

    def test_the_route_serves_exactly_the_facts_the_check_admits(self):
        """Two readers of "what did this job ask", held against each other."""
        client, store = catalog_client()
        job = waiting(store)
        body = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
        served = {
            tuple(question["key"])
            for question in (*body["early_questions"], *body["fact_questions"])
        }
        assert served == self.asked()
        assert served, "a control: the waiting job asks something"

    @pytest.mark.parametrize(
        "key", ["unasked-kind", ("", "", "", "an invented question", "", "")]
    )
    def test_an_unasked_fact_is_refused_at_the_route(self, key):
        client, store = catalog_client()
        job = waiting(store)
        key = self.unasked_kind() if key == "unasked-kind" else key
        response = client.post(
            f"/v1/jobs/{job}/answers",
            json={"facts": [{"key": list(key), "value": "yes"}]},
            headers=auth(),
        )
        assert response.status_code == 400
        assert "asked no question" in response.json()["detail"]

    def test_an_asked_fact_is_admitted_at_the_route(self):
        client, store = catalog_client()
        job = waiting(store)
        body = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
        question = next(q for q in body["early_questions"] if q["choices"])
        response = client.post(
            f"/v1/jobs/{job}/answers",
            json={"facts": [{"key": question["key"], "value": question["choices"][0]}]},
            headers=auth(),
        )
        assert response.status_code == 201, response.text
        child = asyncio.run(store.get(response.json()["job_id"]))
        assert [list(fact.key) for fact in child.facts] == [question["key"]]

    def test_an_earlier_answer_may_be_answered_again(self):
        """A stated attribute is no longer asked, and the round that stated it
        may change it."""
        checkpoint = held()
        earlier = FactAnswer(key=(FLOW, "", "", "", "code-execution", ""), value="yes")
        again = FactAnswer(key=earlier.key, value="no")
        check_answers(
            [],
            [again],
            checkpoint.system_model,
            checkpoint.assertions.catalog,
            frozenset(),
            [earlier],
        )


def asked_of(catalog, *, waiting):
    return question_set(valid_model(), catalog, {"stride": {}}, [], waiting=waiting)


def admit(questions, links=(), facts=()):
    return questions.admit(
        sources=[], earlier_links=[], earlier_facts=[], links=links, facts=facts
    )


class TestOneRoundHasOneAdmissionRule:
    """The HTTP route, the first-run app and the engine each kept their own
    copy of these two rules, and two of the copies disagreed."""

    def test_a_waiting_job_continues_without_answers(self):
        admitted = admit(asked_of(held().assertions.catalog, waiting=True))
        assert (admitted.links, admitted.facts) == ([], [])

    def test_a_finished_job_has_nothing_to_continue(self):
        with pytest.raises(ValueError, match="no answers were sent"):
            admit(asked_of(held().assertions.catalog, waiting=False))

    def test_a_link_answer_without_a_catalog_is_refused(self):
        link = LinkAnswer(principal="customer", element="entity:customer")
        with pytest.raises(NoCatalogError):
            admit(asked_of(None, waiting=True), links=[link])

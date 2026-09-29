"""What a submission's fact answers may name, and how long a line they may write.

Each harness here is the one run 11 of the security audit measured with
(#1294): an answer at the documented limit that failed the resumed job, two
spellings of one fact that both landed, and answers to questions the service
never asked.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

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
from analysis_service.questions import (
    FactAnswer,
    answer_choices,
    answer_limit,
    answered_keys,
    check_fact_answers,
    fact_line,
)
from analysis_service.system_model import attribute_names
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


def _admits_text(model, key):
    try:
        check_fact_answers([FactAnswer(key=key, value="x")], model, None)
    except ValueError:
        return False
    return True


def _free_text_facts():
    """Every attribute of the model that takes an answer in text, opened."""
    for element in valid_model().elements():
        for attribute in attribute_names(element):
            model = valid_model()
            opened = model.get(element.id)
            if not isinstance(getattr(opened, attribute), str):
                continue
            setattr(opened, attribute, "unknown")
            key = (element.id, attribute, "", "", "", "")
            if not answer_choices(key, model, None) and _admits_text(model, key):
                yield pytest.param(model, key, id=f"{element.id}.{attribute}")
    subject = ("", "", "", "who rotates the signing keys?", "", "")
    yield pytest.param(valid_model(), subject, id="subject")


@pytest.mark.parametrize(("model", "key"), list(_free_text_facts()))
def test_the_limit_a_page_shows_is_the_limit_the_check_admits(model, key):
    """A text box took 1,000 characters where the field held 200 (#1289).

    The page's limit and the admission check are two readers of one bound,
    so each is asked at the limit and one character over it.
    """
    limit = answer_limit(key, model)
    check_fact_answers([FactAnswer(key=key, value="x" * limit)], model, None)
    with pytest.raises((ValueError, ValidationError)):
        check_fact_answers([FactAnswer(key=key, value="x" * (limit + 1))], model, None)


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
            answered=[],
            answered_links=[],
            final=False,
            shown=[],
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
        question = body["early_questions"][0]
        response = client.post(
            f"/v1/jobs/{job}/answers",
            json={"facts": [{"key": question["key"], "value": "unknown"}]},
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
    return question_set(
        valid_model(),
        catalog,
        {"stride": {}},
        [],
        waiting=waiting,
        answered=[],
        answered_links=[],
        final=False,
        shown=[],
    )


def admit(questions, links=(), facts=(), save=False):
    return questions.admit(
        sources=[],
        earlier_links=[],
        earlier_facts=[],
        links=links,
        facts=facts,
        save=save,
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


def _asked_after(answered, *, waiting=True, final=False, shown=()):
    checkpoint = held()
    return question_set(
        checkpoint.system_model,
        checkpoint.assertions.catalog,
        {"stride": {}},
        [],
        waiting=waiting,
        answered=answered,
        answered_links=[],
        final=final,
        shown=shown,
    )


CAPACITY = ("process:web-app", "", "", "", "capacity-limits", "")


class TestTheRoundsEnd:
    """A later round asked "I don't know" facts again, and the rounds after a
    report had no end."""

    def test_i_do_not_know_is_not_asked_again(self):
        first = _asked_after([]).early[0].key
        again = _asked_after([FactAnswer(key=first, value="unknown")])
        assert first not in {question.key for question in again.early}

    def test_a_facet_left_out_is_asked_again(self):
        partial = FactAnswer(key=CAPACITY, facets={"rate": "yes"})
        assert CAPACITY in {q.key for q in _asked_after([partial]).early}

    def test_every_facet_answered_is_not_asked_again(self):
        facets = {"rate": "yes", "size": "unknown", "concurrency": "no", "quota": "no"}
        full = FactAnswer(key=CAPACITY, facets=facets)
        assert CAPACITY not in {q.key for q in _asked_after([full]).early}

    def test_a_final_report_asks_nothing_and_admits_nothing(self):
        final = _asked_after([], waiting=False, final=True)
        assert (final.early, final.facts, final.links) == ((), (), ())
        assert final.to_json()["final"] is True
        with pytest.raises(ValueError, match="this report is final"):
            admit(final, facts=[FactAnswer(key=CAPACITY, value="unknown")])

    def test_a_submitter_who_never_knows_reaches_the_end(self):
        """Every round at the pause answers every question "I don't know"."""
        answered: list[FactAnswer] = []
        for _ in range(20):
            asked = _asked_after(answered)
            if not asked.early:
                break
            given = [FactAnswer(key=q.key, value="unknown") for q in asked.early]
            answered = merged_facts(answered, given)
        assert not asked.early

    def test_the_pause_carries_what_it_showed_and_the_report_says_so(self):
        asked = _asked_after([])
        admitted = admit(asked)
        assert set(admitted.shown) == {q.key for q in asked.early}

    def test_the_route_marks_a_report_s_follow_up_and_its_report_is_final(self):
        from tests.test_questions import TestTheRoutes

        client, store = catalog_client()
        waited = waiting(store)
        response = client.post(
            f"/v1/jobs/{waited}/answers", json={"links": []}, headers=auth()
        )
        assert response.status_code == 201, response.text
        started = asyncio.run(store.get(response.json()["job_id"]))
        assert started.resumption.follow_up is False

        finished = TestTheRoutes().completed(store)
        body = client.get(f"/v1/jobs/{finished}/questions", headers=auth()).json()
        assert body["final"] is False
        facts = [{"key": body["fact_questions"][0]["key"], "value": "unknown"}]
        response = client.post(
            f"/v1/jobs/{finished}/answers", json={"facts": facts}, headers=auth()
        )
        assert response.status_code == 201, response.text
        follow_up = asyncio.run(store.get(response.json()["job_id"]))
        assert follow_up.resumption.follow_up is True


class TestTheBoundedRounds:
    """A waiting job asks in rounds (ADR 0053)."""

    def test_a_round_shows_at_most_its_size_of_each_kind_above_the_floor(self):
        from analysis_service.answer_round import EARLY_RULES, ROUND_SIZE

        asked = _asked_after([])
        for kind, rule in EARLY_RULES.items():
            shown = [
                q
                for q in asked.early
                if (q.kind == "capability") == (kind == "capability")
            ]
            assert len(shown) <= ROUND_SIZE
            assert all(q.score >= rule.floor for q in shown)
        assert asked.early, "a control: the waiting job asks something"

    def test_a_saved_answer_leaves_the_round_and_is_listed_with_its_answer(self):
        first = _asked_after([]).early[0]
        answer = FactAnswer(key=first.key, value="unknown")
        after = _asked_after([answer])
        assert first.key not in {q.key for q in after.early}
        assert [(q.key, a) for q, a in after.answered_early] == [(first.key, answer)]

    def test_each_kind_stops_at_its_limit(self):
        from analysis_service.answer_round import _round

        listed = _asked_after([]).early
        held = {
            ("", "", "", f"subject {n}", "", ""): FactAnswer(
                key=("", "", "", f"subject {n}", "", ""), value="unknown"
            )
            for n in range(30)
        }
        shown, remaining, withheld = _round(listed, frozenset(held), held)
        assert not [q for q in shown if q.kind != "capability"]
        assert remaining["field"] == 0
        assert withheld == len([q for q in listed if q.kind != "capability"])

    def test_a_question_answered_in_part_comes_back_outside_the_limit(self):
        """Thirty partial answers hid every question and started the analysis
        as if nothing were left (#1289, B3)."""
        from analysis_service.answer_round import ROUND_SIZE, _round

        def capacity_of(n):
            key = (f"process:p{n}", "", "", "", "capacity-limits", "")
            return SimpleNamespace(key=key, kind="question", score=5.0)

        listed = [capacity_of(n) for n in range(31)]
        held = {
            q.key: FactAnswer(key=q.key, facets={"rate": "yes"}) for q in listed[:30]
        }
        shown, remaining, withheld = _round(listed, answered_keys(held.values()), held)
        assert [q.key for q in shown] == [q.key for q in listed[:ROUND_SIZE]]
        assert remaining["field"] == 30
        assert withheld == 1

    def test_a_stop_says_whether_the_limits_held_questions_back(self):
        asked = _asked_after([])
        assert asked.stop is None
        ended = replace(asked, early=(), links=(), withheld=0)
        assert ended.stop == "nothing-left"
        held_back = replace(ended, withheld=3)
        assert held_back.stop == "budget-exhausted"
        assert held_back.to_json()["early_stop"] == "budget-exhausted"
        assert held_back.to_json()["early_withheld"] == 3

    def test_a_submitter_who_answers_one_facet_a_round_reaches_the_end(self):
        answered: list[FactAnswer] = []
        for _ in range(40):
            asked = _asked_after(answered)
            if not asked.early:
                break
            given = []
            for question in asked.early:
                if not question.facets:
                    given.append(FactAnswer(key=question.key, value="unknown"))
                    continue
                before = next((a for a in answered if a.key == question.key), None)
                said = before.facets if before else {}
                facet = next(f.id for f in question.facets if f.id not in said)
                given.append(FactAnswer(key=question.key, facets={facet: "unknown"}))
            answered = merged_facts(answered, given)
        assert not asked.early
        assert asked.links, "a control: the link questions still stand"
        assert asked.stop is None, "a job that still asks a link has not stopped"

    def test_a_saved_round_must_answer_something_and_only_a_waiting_job_saves(self):
        with pytest.raises(ValueError, match="at least one question"):
            admit(_asked_after([]), facts=[], save=True)
        finished = _asked_after([], waiting=False)
        answer = FactAnswer(key=CAPACITY, value="unknown")
        with pytest.raises(ValueError, match="only a job waiting"):
            admit(finished, facts=[answer], save=True)

    def test_the_route_saves_a_round_and_starts_when_nothing_is_left(self):
        from analysis_service.sources import SourceLimits

        client, store = catalog_client()
        # Room for the answers source every round's answers compose.
        client.app.state.limits = SourceLimits(max_total_bytes=100_000, max_sources=3)
        job = waiting(store)
        for _ in range(20):
            body = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
            facts = [
                {"key": q["key"], "value": "unknown"} for q in body["early_questions"]
            ]
            links = [
                {"principal": q["principal"], "element": "none"}
                for q in body["link_questions"]
            ]
            response = client.post(
                f"/v1/jobs/{job}/answers",
                json={"facts": facts, "links": links, "save": True},
                headers=auth(),
            )
            if response.status_code == 201:
                break
            assert response.status_code == 200, response.text
            assert response.json() == {"job_id": job, "saved": True}
            assert asyncio.run(store.get(job)).checkpoint is not None
        assert response.status_code == 201, "the rounds never ended"
        child = asyncio.run(store.get(response.json()["job_id"]))
        assert child.resumption.follow_up is False
        assert child.facts, "the saved answers reach the analysis"
        assert child.shown_early, "the report can say what the pause showed"

"""What a submission's fact answers may name, and how long a line they may write.

Each harness here comes from run 11 of the security audit (#1294). The
tests pin three rules: an answer at the documented limit resumes the job, two
spellings of one fact merge to one answer, and the service refuses an answer
to a question it did not ask.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from analysis_service.answer_forms import answer_choices, answer_limit
from analysis_service.answer_round import question_set
from analysis_service.answer_sets import NO_ANSWERS, AnswerSet
from analysis_service.assertions import MAX_QUOTE_CHARS, AssertionCatalog
from analysis_service.early_questions import early_questions
from analysis_service.fact_answers import FactAnswer, answered_keys, fact_line
from analysis_service.fact_writes import check_fact_answers
from analysis_service.links import (
    LinkAnswer,
    NoCatalogError,
    apply_answers,
    check_answers,
    merged_facts,
)
from analysis_service.open_facts import open_attribute
from analysis_service.question_kinds import QUESTION_KINDS
from analysis_service.system_model import attribute_names
from tests import test_webapp
from tests.factories import valid_model
from tests.test_api import auth
from tests.test_links import catalog_client
from tests.test_pause import held, waiting

#: The shipped tier config, as the webapp tests build it.
tiers = test_webapp.tiers

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
        """An answer at the longest admitted line builds its span, because the
        check bounds the line that the span quotes, not only the value.
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
        """Two spellings of one fact give one answer, so a lane prompt carries
        one line."""
        first = FactAnswer(key=(FLOW, "", "", "", "code-execution", ""), value="yes")
        second = FactAnswer(key=(FLOW, "", " ", "", "code-execution", ""), value="no")
        assert merged_facts([first], [second]) == [second]

    def test_a_later_facet_answer_keeps_the_earlier_facets(self):
        """A size answer in round two keeps the rate answer of round one
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
    """The service refuses an answer to an element and kind pair that it did
    not ask, so an invented subject cannot reach a lane prompt."""

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
            for question in (
                *body["early_questions"],
                *body["early_held_back"],
                *body["early_below_floor"],
                *body["fact_questions"],
            )
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
            json={"facts": [{"key": list(key), "value": "yes"}], "revision": 0},
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
            json={
                "facts": [{"key": question["key"], "value": "unknown"}],
                "revision": 0,
            },
            headers=auth(),
        )
        assert response.status_code == 201, response.text
        child = asyncio.run(store.get(response.json()["job_id"]))
        assert [list(fact.key) for fact in child.answers.facts] == [question["key"]]

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


def admit(questions, links=(), facts=(), save=False, skips=()):
    return questions.admit(
        sources=[],
        earlier=NO_ANSWERS,
        given=AnswerSet(links=tuple(links), facts=tuple(facts)),
        save=save,
        skips=skips,
    )


class TestOneRoundHasOneAdmissionRule:
    """The HTTP route, the first-run app and the engine each kept their own
    copy of these two rules, and two of the copies disagreed."""

    def test_a_waiting_job_continues_without_answers(self):
        admitted = admit(asked_of(held().assertions.catalog, waiting=True))
        assert admitted.answers.empty

    def test_a_finished_job_has_nothing_to_continue(self):
        with pytest.raises(ValueError, match="no answers were sent"):
            admit(asked_of(held().assertions.catalog, waiting=False))

    def test_a_link_answer_without_a_catalog_is_refused(self):
        link = LinkAnswer(principal="customer", element="entity:customer")
        with pytest.raises(NoCatalogError):
            admit(asked_of(None, waiting=True), links=[link])


def _asked_after(answered, *, waiting=True, final=False, shown=(), skipped=()):
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
        skipped=skipped,
    )


CAPACITY = ("process:web-app", "", "", "", "capacity-limits", "")


def _known_answer(question: dict) -> dict:
    """An answer that states something, in the form the question takes."""
    if question["form"] == "facets":
        return {"key": question["key"], "facets": {question["facets"][0]["id"]: "yes"}}
    if question["choices"]:
        return {"key": question["key"], "value": question["choices"][0]}
    value = "none" if question["form"] == "control" else "the admins"
    return {"key": question["key"], "value": value}


class TestTheRoundsEnd:
    """A later round does not ask an "I don't know" fact again, and the
    rounds after a report end."""

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
            f"/v1/jobs/{waited}/answers",
            json={"links": [], "revision": 0},
            headers=auth(),
        )
        assert response.status_code == 201, response.text
        started = asyncio.run(store.get(response.json()["job_id"]))
        assert started.resumption.follow_up is False

        finished = TestTheRoutes().completed(store)
        body = client.get(f"/v1/jobs/{finished}/questions", headers=auth()).json()
        assert body["final"] is False
        facts = [_known_answer(body["fact_questions"][0])]
        response = client.post(
            f"/v1/jobs/{finished}/answers", json={"facts": facts}, headers=auth()
        )
        assert response.status_code == 201, response.text
        follow_up = asyncio.run(store.get(response.json()["job_id"]))
        assert follow_up.resumption.follow_up is True


class TestTheBoundedRounds:
    """A waiting job asks in rounds (ADR 0053)."""

    def test_a_round_asks_at_most_its_questions_of_each_kind_above_the_floor(self):
        from analysis_service.answer_round import EARLY_RULES, passes_floor
        from analysis_service.early_questions import early_questions

        asked = _asked_after([])
        checkpoint = held()
        listed = early_questions(
            asked.model, {"stride": {}}, checkpoint.assertions.catalog
        )
        for kind in EARLY_RULES:
            shown = [
                q
                for q in asked.early
                if (q.kind == "capability") == (kind == "capability")
            ]
            assert len(shown) <= EARLY_RULES[kind].per_round
            assert all(passes_floor(q, listed) for q in shown)
        assert asked.early, "a control: the waiting job asks something"

    def test_a_question_counts_as_one_whatever_its_facets(self):
        from analysis_service.answer_round import next_round

        wide = SimpleNamespace(
            key=CAPACITY,
            kind="question",
            score=5.0,
            decisions=11,
            parent=None,
            frameworks=("stride",),
            gates=(),
        )
        shown, _, _ = next_round([wide], frozenset(), {})
        assert shown == (wide,)

    def test_a_saved_answer_leaves_the_round_and_is_listed_with_its_answer(self):
        first = _asked_after([]).early[0]
        answer = FactAnswer(key=first.key, value="unknown")
        after = _asked_after([answer])
        assert first.key not in {q.key for q in after.early}
        assert [(q.key, a) for q, a in after.answered_early] == [(first.key, answer)]

    def test_each_kind_stops_at_its_limit(self):
        from analysis_service.answer_round import next_round

        listed = _asked_after([]).early
        held = {("", "", "", f"subject {n}", "", ""): ("stride",) for n in range(30)}
        shown, remaining, withheld = next_round(listed, frozenset(held), held)
        assert not [q for q in shown if q.kind != "capability"]
        assert remaining["field"] == 0
        assert len(withheld) == len([q for q in listed if q.kind != "capability"])

    def test_a_second_framework_takes_no_place_from_the_first(self):
        """Each framework has its own limit, so a joint pause asks STRIDE what
        it asks alone (QA-2026-10-09-01-E1)."""
        from analysis_service.answer_round import EARLY_RULES, next_round

        def field(n, framework):
            key = (f"process:{framework}{n}", "authentication", "", "", "", "")
            return SimpleNamespace(
                key=key,
                kind="attribute",
                score=5.0,
                decisions=1,
                parent=None,
                frameworks=(framework,),
                gates=(),
            )

        limit = EARLY_RULES["field"].limit
        stride = [field(n, "stride") for n in range(limit)]
        asvs = [field(n, "asvs") for n in range(5)]
        _, remaining, withheld = next_round([*stride, *asvs], frozenset(), {})
        assert remaining["field"] == limit + len(asvs)
        assert withheld == ()

        answered = {question.key: ("stride",) for question in stride[:limit]}
        rest = [field(limit + n, "stride") for n in range(3)]
        _, remaining, withheld = next_round(
            [*rest, *asvs], frozenset(answered), answered
        )
        assert remaining["field"] == len(asvs)
        assert [q.key for q in withheld] == [q.key for q in rest]

    def test_a_question_answered_in_part_comes_back_outside_the_limit(self):
        """A question with a partial answer stays outside the limit, so thirty
        partial answers do not hide every question (#1289, B3)."""
        from analysis_service.answer_round import EARLY_RULES, next_round

        facets = QUESTION_KINDS["capacity-limits"].facets

        def capacity_of(n):
            key = (f"process:p{n}", "", "", "", "capacity-limits", "")
            return SimpleNamespace(
                key=key,
                kind="question",
                score=5.0,
                facets=facets,
                decisions=4,
                parent=None,
                frameworks=("stride",),
                gates=(),
            )

        listed = [capacity_of(n) for n in range(31)]
        answers = [FactAnswer(key=q.key, facets={"rate": "yes"}) for q in listed[:30]]
        held = {q.key: q.frameworks for q in listed[:30]}
        shown, remaining, withheld = next_round(listed, answered_keys(answers), held)
        per_round = EARLY_RULES["field"].per_round
        assert [q.key for q in shown] == [q.key for q in listed[:per_round]]
        assert remaining["field"] == 30
        assert [q.key for q in withheld] == [listed[-1].key]

    def test_a_stop_says_whether_the_limits_held_questions_back(self):
        asked = _asked_after([])
        assert asked.stop is None
        ended = replace(asked, early=(), links=(), held_back=(), below_floor=())
        assert ended.stop == "nothing-left"
        low = replace(ended, below_floor=asked.early[:2])
        assert low.stop == "below-floor"
        held_back = replace(low, held_back=asked.early[:3])
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

    def test_a_saved_round_must_answer_something_and_a_final_report_saves_nothing(
        self,
    ):
        with pytest.raises(ValueError, match="answers or skips at least one"):
            admit(_asked_after([]), facts=[], save=True)
        final = _asked_after([], waiting=False, final=True)
        answer = FactAnswer(key=CAPACITY, value="unknown")
        with pytest.raises(ValueError, match="this report is final"):
            admit(final, facts=[answer], save=True)

    def test_the_route_saves_every_round_and_only_a_continue_starts(self):
        """The last save started the analysis by itself (#1289, item 7)."""
        from analysis_service.sources import SourceLimits

        client, store = catalog_client()
        # Room for the answers source every round's answers compose.
        client.app.state.limits = SourceLimits(max_total_bytes=100_000, max_sources=3)
        job = waiting(store)
        for _ in range(20):
            body = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
            if body["early_stop"] is not None:
                break
            facts = [
                {"key": q["key"], "value": "unknown"} for q in body["early_questions"]
            ]
            links = [
                {"principal": q["principal"], "element": "none"}
                for q in body["link_questions"]
            ]
            response = client.post(
                f"/v1/jobs/{job}/answers",
                json={
                    "facts": facts,
                    "links": links,
                    "save": True,
                    "revision": body["revision"],
                },
                headers=auth(),
            )
            assert response.status_code == 200, response.text
            assert response.json() == {"job_id": job, "saved": True}
            assert asyncio.run(store.get(job)).checkpoint is not None
        else:
            pytest.fail("the rounds never ended")
        # Every answer was "I don't know", so the questions under the floor
        # are what is left, and the stop says so.
        assert body["early_stop"] == "below-floor"
        assert body["early_below_floor"] and not body["early_held_back"]

        response = client.post(
            f"/v1/jobs/{job}/answers",
            json={"links": [], "revision": body["revision"]},
            headers=auth(),
        )
        assert response.status_code == 201, response.text
        child = asyncio.run(store.get(response.json()["job_id"]))
        assert child.resumption.follow_up is False
        assert child.answers.facts, "the saved answers reach the analysis"
        assert child.shown_early, "the report can say what the pause showed"


class TestSkipForNow:
    """A skipped question leaves the rounds, and a wholly blank round can be
    saved, so a submitter who cannot answer can go on (#1289)."""

    def test_a_skipped_question_leaves_the_rounds_and_is_listed(self):
        first = _asked_after([]).early[0]
        admitted = admit(_asked_after([]), save=True, skips=[first.key])
        after = _asked_after([], skipped=admitted.skipped)
        assert admitted.skipped == (first.key,)
        assert first.key not in {q.key for q in after.early}
        assert [q.key for q in after.skipped] == [first.key]
        assert after.to_json()["skipped_early"][0]["key"] == list(first.key)

    def test_a_skip_writes_no_answer_and_leaves_one_question_fewer(self):
        first = _asked_after([]).early[0]
        after = _asked_after([], skipped=[first.key])
        admitted = admit(after, save=True, skips=[after.early[0].key])
        assert admitted.answers.facts == ()
        assert after.remaining == {
            kind: count
            - (kind == ("capability" if first.kind == "capability" else "field"))
            for kind, count in _asked_after([]).remaining.items()
        }

    def test_a_skipped_question_can_still_be_answered_and_leaves_the_list(self):
        first = _asked_after([]).early[0]
        after = _asked_after([], skipped=[first.key])
        answer = FactAnswer(key=first.key, value="unknown")
        admitted = admit(after, facts=[answer], save=True)
        assert admitted.skipped == ()
        assert admitted.answers.facts == (answer,)

    def test_a_submitter_who_skips_every_round_reaches_the_end(self):
        skipped: tuple = ()
        for _ in range(40):
            asked = _asked_after([], skipped=skipped)
            if not asked.early:
                break
            skipped = admit(
                asked, save=True, skips=[q.key for q in asked.early]
            ).skipped
        assert not asked.early
        assert len(asked.skipped) == len(skipped)

    @pytest.mark.parametrize(
        ("save", "which", "answer", "refusal"),
        [
            (False, "shown", False, "only a saved round skips"),
            (True, "unshown", False, "only a question this round shows"),
            (True, "shown", True, "answered or skipped, not both"),
        ],
    )
    def test_a_skip_is_refused(self, save, which, answer, refusal):
        asked = _asked_after([])
        key = asked.early[0].key if which == "shown" else CAPACITY[:4] + ("x", "")
        facts = [FactAnswer(key=key, value="unknown")] if answer else []
        with pytest.raises(ValueError, match=refusal):
            admit(asked, facts=facts, save=save, skips=[key])

    def test_a_link_only_round_is_saved_as_a_skip_and_places_nothing(self):
        """The "Skip the rest" button sent an empty save for a link round (#1289, A2)."""
        asked = _asked_after([])
        (link,) = asked.links
        admitted = admit(asked, save=True, skips=[link.key])
        assert admitted.answers.empty
        after = _asked_after([], skipped=admitted.skipped)
        assert after.links == ()
        assert after.skipped_links == (link,)
        assert after.to_json()["skipped_links"][0]["key"] == link.key

    def test_a_skipped_link_can_still_be_answered_and_leaves_the_list(self):
        (link,) = _asked_after([]).links
        after = _asked_after([], skipped=[link.key])
        answer = LinkAnswer(principal=link.principal, element="none")
        admitted = admit(after, links=[answer], save=True)
        assert admitted.skipped == ()
        assert admitted.answers.links == (answer,)

    def test_a_link_is_answered_or_skipped_not_both(self):
        asked = _asked_after([])
        (link,) = asked.links
        answer = LinkAnswer(principal=link.principal, element="none")
        with pytest.raises(ValueError, match="answered or skipped, not both"):
            admit(asked, links=[answer], save=True, skips=[link.key])

    def test_the_route_keeps_a_link_skip_on_the_job(self):
        from analysis_service.sources import SourceLimits

        client, store = catalog_client()
        client.app.state.limits = SourceLimits(max_total_bytes=100_000, max_sources=3)
        job = waiting(store)
        body = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
        key = body["link_questions"][0]["key"]
        response = client.post(
            f"/v1/jobs/{job}/answers",
            json={"save": True, "skip": [key], "revision": 0},
            headers=auth(),
        )
        assert response.status_code == 200, response.text
        after = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
        assert after["link_questions"] == []
        assert [q["key"] for q in after["skipped_links"]] == [key]

    def test_the_route_keeps_a_skip_on_the_job(self):
        from analysis_service.sources import SourceLimits

        client, store = catalog_client()
        client.app.state.limits = SourceLimits(max_total_bytes=100_000, max_sources=3)
        job = waiting(store)
        body = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
        key = body["early_questions"][0]["key"]
        response = client.post(
            f"/v1/jobs/{job}/answers",
            json={"save": True, "skip": [key], "revision": 0},
            headers=auth(),
        )
        assert response.status_code == 200, response.text
        after = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
        assert [q["key"] for q in after["skipped_early"]] == [key]
        assert key not in [q["key"] for q in after["early_questions"]]
        assert asyncio.run(store.get(job)).skipped_early == [tuple(key)]


class TestTheRoundRevision:
    """A page left open on an earlier round saved over a later one (#1289)."""

    def post(self, client, job, **body):
        return client.post(f"/v1/jobs/{job}/answers", json=body, headers=auth())

    def test_of_two_saves_that_read_one_revision_only_the_first_lands(self):
        client, store = catalog_client()
        job = waiting(store)
        body = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
        first, second = (q["key"] for q in body["early_questions"][:2])
        landed = self.post(client, job, save=True, skip=[first], revision=0)
        stale = self.post(client, job, save=True, skip=[second], revision=0)
        assert landed.status_code == 200, landed.text
        assert stale.status_code == 409
        after = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
        assert after["revision"] == 1
        assert [q["key"] for q in after["skipped_early"]] == [first]

    def test_a_stale_continue_is_refused(self):
        client, store = catalog_client()
        job = waiting(store)
        body = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
        key = body["early_questions"][0]["key"]
        self.post(client, job, save=True, skip=[key], revision=0)
        assert self.post(client, job, links=[], revision=0).status_code == 409

    def test_a_waiting_job_requires_a_revision(self):
        client, store = catalog_client()
        job = waiting(store)
        assert self.post(client, job, links=[]).status_code == 400

    def test_the_store_refuses_a_save_against_an_old_revision(self):
        _, store = catalog_client()
        job = waiting(store)
        record = asyncio.run(store.get(job))

        def save(revision):
            return asyncio.run(
                store.save_round(
                    job, record.owner_subject, NO_ANSWERS, [], [], revision
                )
            )

        assert save(0)
        assert not save(0)
        assert save(1)


class TestTakingAnAnswerBack:
    """A guessed answer could be changed to anything but "I don't know" (#1289)."""

    def attribute_question(self):
        asked = _asked_after([])
        question = next((q for q in asked.early if q.kind == "attribute"), None)
        if question is None:
            pytest.skip("the test model asks no attribute early")
        return question

    def known(self, question):
        value = question.choices[0] if question.choices else "TLS 1.3"
        return FactAnswer(key=question.key, value=value)

    def test_the_pause_takes_an_answer_back_and_the_fact_is_open_again(self):
        question = self.attribute_question()
        earlier = [self.known(question)]
        back = FactAnswer(key=question.key, value="unknown")
        admitted = _asked_after(earlier).admit(
            sources=[],
            earlier=AnswerSet(facts=tuple(earlier)),
            given=AnswerSet(facts=(back,)),
            save=True,
        )
        assert admitted.answers.facts == (back,)
        after = _asked_after(admitted.answers.facts)
        element_id, attribute = question.key[:2]
        assert open_attribute(after.model, element_id, attribute)
        assert [a for q, a in after.answered_early if q.key == question.key] == [back]

    def test_the_pause_takes_a_facet_answer_back(self):
        earlier = [
            FactAnswer(
                key=CAPACITY,
                facets=dict.fromkeys(("rate", "size", "concurrency", "quota"), "yes"),
            )
        ]
        back = FactAnswer(
            key=CAPACITY,
            facets={
                "rate": "unknown",
                "size": "unknown",
                "concurrency": "unknown",
                "quota": "unknown",
            },
        )
        check_fact_answers([back], valid_model(), None, earlier, reopen=True)
        with pytest.raises(ValueError, match="earlier answer settled"):
            check_fact_answers([back], valid_model(), None, earlier)

    def test_a_report_s_follow_up_still_refuses_it(self):
        question = self.attribute_question()
        earlier = [self.known(question)]
        finished = _asked_after(earlier, waiting=False)
        with pytest.raises(ValueError, match="earlier answer settled"):
            finished.admit(
                sources=[],
                earlier=AnswerSet(facts=tuple(earlier)),
                given=AnswerSet(facts=(FactAnswer(key=question.key, value="unknown"),)),
            )


def _facets(facets: dict[str, str]) -> FactAnswer:
    return FactAnswer.model_validate({"key": CAPACITY, "facets": facets})


class TestAFollowUpMustAddSomething:
    """An all-"I don't know" follow-up spent the report's only rerun (#1289)."""

    FACT = (FLOW, "data_description", "", "", "", "")
    LINK = LinkAnswer(principal="customer", element="entity:customer")

    @pytest.mark.parametrize(
        ("earlier", "sent", "adds"),
        [
            ([], [FactAnswer(key=FACT, value="unknown")], False),
            (
                [FactAnswer(key=FACT, value="orders")],
                [FactAnswer(key=FACT, value="orders")],
                False,
            ),
            (
                [FactAnswer(key=FACT, value="orders")],
                [FactAnswer(key=FACT, value="refunds")],
                True,
            ),
            (
                [],
                [
                    FactAnswer(key=FACT, value="unknown"),
                    _facets({"rate": "no"}),
                ],
                True,
            ),
            (
                [_facets({"rate": "yes"})],
                [_facets({"size": "unknown"})],
                False,
            ),
            (
                [_facets({"rate": "yes"})],
                [_facets({"size": "no"})],
                True,
            ),
        ],
        ids=[
            "unknown",
            "repeat",
            "changed",
            "one-known",
            "facet-unknown",
            "facet-known",
        ],
    )
    def test_only_known_content_that_changes_is_information(self, earlier, sent, adds):
        from analysis_service.answer_round import _adds_information

        assert (
            _adds_information(
                AnswerSet(facts=tuple(earlier)), AnswerSet(facts=tuple(sent))
            )
            is adds
        )

    @pytest.mark.parametrize(("earlier", "adds"), [([], True), ([LINK], False)])
    def test_a_link_is_information_where_it_places_anew(self, earlier, adds):
        from analysis_service.answer_round import _adds_information

        assert (
            _adds_information(
                AnswerSet(links=tuple(earlier)), AnswerSet(links=(self.LINK,))
            )
            is adds
        )

    def test_the_route_refuses_it_and_keeps_the_follow_up(self):
        from tests.test_questions import TestTheRoutes

        client, store = catalog_client()
        finished = TestTheRoutes().completed(store)
        body = client.get(f"/v1/jobs/{finished}/questions", headers=auth()).json()
        question = body["fact_questions"][0]
        unknown = client.post(
            f"/v1/jobs/{finished}/answers",
            json={"facts": [{"key": question["key"], "value": "unknown"}]},
            headers=auth(),
        )
        assert unknown.status_code == 400
        assert "still available" in unknown.json()["detail"]
        known = client.post(
            f"/v1/jobs/{finished}/answers",
            json={"facts": [_known_answer(question)]},
            headers=auth(),
        )
        assert known.status_code == 201, known.text


class TestSkippingAPartAnswer:
    """A question answered in part could not be skipped, and came back first
    in every round (#1289)."""

    def capacity(self, asked):
        question = next((q for q in asked.early if q.key == CAPACITY), None)
        if question is None:
            pytest.skip("the test model asks no capacity question early")
        return question

    def test_a_part_answer_and_a_skip_of_the_rest_land_together(self):
        asked = _asked_after([])
        self.capacity(asked)
        part = _facets({"rate": "yes"})
        admitted = admit(asked, facts=[part], save=True, skips=[CAPACITY])
        assert admitted.answers.facts == (part,)
        assert admitted.skipped == (CAPACITY,)
        after = _asked_after(admitted.answers.facts, skipped=admitted.skipped)
        assert CAPACITY not in {q.key for q in after.early}

    def test_a_full_answer_is_still_not_skipped(self):
        asked = _asked_after([])
        self.capacity(asked)
        full = _facets(dict.fromkeys(("rate", "size", "concurrency", "quota"), "no"))
        with pytest.raises(ValueError, match="answered or skipped, not both"):
            admit(asked, facts=[full], save=True, skips=[CAPACITY])


class TestARefusalNamesItsFactByLabel:
    """Refusals printed a six-part key, which means nothing on a page (#1289)."""

    def assert_readable(self, excinfo, label):
        message = str(excinfo.value)
        assert f'"{label}"' in message
        assert "('" not in message, message

    def test_a_line_too_long(self):
        from analysis_service.fact_answers import fact_label

        key = (FLOW, "data_description", "", "", "", "")
        with pytest.raises(ValueError, match="line may hold") as excinfo:
            check_fact_answers(
                [sized(key, MAX_QUOTE_CHARS + 1)], open_description(), None
            )
        self.assert_readable(excinfo, fact_label(key, open_description()))

    def test_a_skip_of_a_question_not_shown_and_of_an_answered_one(self):
        from analysis_service.fact_answers import fact_label

        asked = _asked_after([])
        shown = asked.early[0].key
        unshown = CAPACITY[:4] + ("x", "")
        with pytest.raises(ValueError, match="only a question this round") as excinfo:
            admit(asked, save=True, skips=[unshown])
        self.assert_readable(excinfo, fact_label(unshown, asked.model))
        with pytest.raises(ValueError, match="not both") as excinfo:
            admit(
                asked,
                facts=[FactAnswer(key=shown, value="unknown")],
                save=True,
                skips=[shown],
            )
        self.assert_readable(excinfo, fact_label(shown, asked.model))

    def test_an_unasked_fact(self):
        from analysis_service.fact_answers import fact_label

        key = (FLOW, "data_description", "", "", "", "")
        model = open_description()
        with pytest.raises(ValueError, match="asked no question") as excinfo:
            check_answers(
                [], [FactAnswer(key=key, value="orders")], model, None, frozenset()
            )
        self.assert_readable(excinfo, fact_label(key, model))


class TestOneResumedJob:
    """A job's answers started any number of resumed jobs, so one report took
    many follow-ups (#1289, ADR 0054)."""

    def answer(self, client, job_id, body):
        return client.post(f"/v1/jobs/{job_id}/answers", json=body, headers=auth())

    def test_a_report_takes_one_follow_up(self):
        from tests.test_questions import TestTheRoutes

        client, store = catalog_client()
        finished = TestTheRoutes().completed(store)
        body = client.get(f"/v1/jobs/{finished}/questions", headers=auth()).json()
        facts = {"facts": [_known_answer(body["fact_questions"][0])]}

        assert self.answer(client, finished, facts).status_code == 201
        again = self.answer(client, finished, facts)

        assert again.status_code == 409
        assert "already started job" in again.json()["detail"]

    def test_a_waiting_job_starts_one_analysis(self):
        client, store = catalog_client()
        waited = waiting(store)
        body = {"links": [], "revision": 0}

        assert self.answer(client, waited, body).status_code == 201
        assert self.answer(client, waited, body).status_code == 409

    @pytest.mark.parametrize("status", ["failed", "rejected"])
    def test_a_resumed_job_that_read_nothing_frees_its_parent(self, status):
        client, store = catalog_client()
        waited = waiting(store)
        body = {"links": [], "revision": 0}
        first = self.answer(client, waited, body).json()["job_id"]
        spent = asyncio.run(store.get(first))
        asyncio.run(store.save(spent.model_copy(update={"status": status})))

        assert self.answer(client, waited, body).status_code == 201


def test_a_save_after_the_start_is_refused():
    """The service refuses a save after the start, because no job reads it."""
    client, store = catalog_client()
    waited = waiting(store)
    body = client.get(f"/v1/jobs/{waited}/questions", headers=auth()).json()
    question = body["early_questions"][0]
    started = client.post(
        f"/v1/jobs/{waited}/answers",
        json={"links": [], "revision": 0},
        headers=auth(),
    )
    assert started.status_code == 201

    saved = client.post(
        f"/v1/jobs/{waited}/answers",
        json={"facts": [_known_answer(question)], "save": True, "revision": 0},
        headers=auth(),
    )

    assert saved.status_code == 409


@pytest.mark.parametrize(
    "odd", [{"revision": True}, {"revision": "0"}, {"save": "yes"}, {"save": 1}]
)
def test_both_routes_refuse_one_set_of_odd_fields(odd, tiers):
    """/v1 read ``revision: true`` as 1 and ``save: "yes"`` as a save, where
    the first-run app refused both."""
    from tests.test_webapp_questions import (
        SAME_ORIGIN,
        PausingRunner,
        client_for,
        event,
        start,
    )

    client, store = catalog_client()
    waited = waiting(store)
    body = {"links": [], "revision": 0} | odd
    api = client.post(f"/v1/jobs/{waited}/answers", json=body, headers=auth())

    app = client_for(tiers, PausingRunner(catalog=False), catalog=False)
    paused = start(app, questions=True)
    event(app.get(f"/events/{paused}").text, "questions")
    page = app.post(f"/answer/{paused}", json=body | {"facts": []}, headers=SAME_ORIGIN)

    assert (api.status_code, page.status_code) == (422, 400)


@pytest.mark.parametrize("save", [True, False], ids=["save", "start"])
def test_answers_that_break_the_input_limits_are_refused_at_once(save):
    """The service refuses a save over the input limits at once, so a later
    start cannot fail on it."""
    from analysis_service.sources import SourceLimits, total_bytes

    client, store = catalog_client()
    waited = waiting(store)
    record = asyncio.run(store.get(waited))
    client.app.state.limits = SourceLimits(
        max_total_bytes=total_bytes(record.sources) + 10, max_sources=10
    )
    body = client.get(f"/v1/jobs/{waited}/questions", headers=auth()).json()
    facts = [_known_answer(body["early_questions"][0])]

    sent = client.post(
        f"/v1/jobs/{waited}/answers",
        json={"facts": facts, "save": save, "revision": 0},
        headers=auth(),
    )

    assert sent.status_code == 413, sent.text
    assert asyncio.run(store.get(waited)).answers.empty


#: The selections the round test runs over every corpus model, and the answer
#: given to a yes-or-no question. STRIDE asks none, so one answer covers it;
#: ASVS level 1 puts no part in its parent's round, so levels 2 and 3 do.
_ROUND_RUNS = {
    "stride": ({"stride": {}}, "yes"),
    **{
        f"asvs-{level}-{choice}": ({"asvs": {"level": level}}, choice)
        for level in (2, 3)
        for choice in ("yes", "no")
    },
}


def _answer_as_a_person(question, choice):
    """What a person answers on the page: every facet, or ``choice`` where it is
    one of the question's choices, else the first choice, else a mechanism."""
    if question.facets:
        return FactAnswer(
            key=question.key, facets={facet.id: "yes" for facet in question.facets}
        )
    if question.choices:
        value = choice if choice in question.choices else question.choices[0]
        return FactAnswer(key=question.key, value=value)
    return FactAnswer(key=question.key, value="none")


@pytest.mark.parametrize("run", sorted(_ROUND_RUNS))
@pytest.mark.parametrize(
    "case", sorted(path.name for path in Path("evals/corpus").iterdir())
)
def test_no_round_opens_with_more_questions_than_the_one_before(case, run):
    """A round bounded by choices asked 4 questions, then 6, then 5: a facet
    table cost a choice per facet (2026-10-01). And at ASVS level 2 a part
    hidden until its parent's "yes" took a place, so round 1 opened with 8
    questions and round 2 with 10. Nothing a question depends on requires
    either, so the questions a round opens with, of each kind, never grow."""
    from analysis_service.system_model import SystemModel

    path = Path("evals/corpus") / case / "model.json"
    model = SystemModel.model_validate_json(path.read_text())
    selection, choice = _ROUND_RUNS[run]
    answered: list[FactAnswer] = []
    opened: dict[bool, list[int]] = {True: [], False: []}
    for _ in range(40):
        asked = question_set(
            model,
            None,
            selection,
            [],
            waiting=True,
            answered=answered,
            answered_links=[],
            final=False,
            shown=[],
        )
        if not asked.early:
            break
        keys = {q.key for q in asked.early}
        # A question that decides a framework's precondition is asked outside
        # the limits (ADR 0067), so a round that holds one counts no kind.
        gated = any(q.gates for q in asked.early)
        for capability, counts in opened.items():
            if gated:
                break
            counts.append(
                sum(
                    1
                    for q in asked.early
                    if (q.kind == "capability") == capability and q.parent not in keys
                )
            )
        given: dict = {}
        for q in asked.early:
            # The page shows a part only once its parent in the round is "yes".
            if q.parent in keys and given.get(q.parent, None) is None:
                continue
            if q.parent in keys and given[q.parent].value != "yes":
                continue
            given[q.key] = _answer_as_a_person(q, choice)
        answered = merged_facts(answered, list(given.values()))
    if asked.gates and set(asked.gates.values()) == {"refuted"}:
        # A selection whose one framework will not run asks nothing.
        assert not any(opened[True]) and not any(opened[False])
        return
    assert any(opened[True]) or any(opened[False]), "a control: it asks something"
    for counts in opened.values():
        assert counts == sorted(counts, reverse=True), counts


class TestAJobItsAnswersResumed:
    """After a job's answers start a job, the questions route names the job
    that holds them (#1369, checkpoint round over reviewed/2026-09-30)."""

    def test_its_questions_name_the_job_that_holds_it(self):
        from tests.test_questions import TestTheRoutes

        client, store = catalog_client()
        finished = TestTheRoutes().completed(store)
        body = client.get(f"/v1/jobs/{finished}/questions", headers=auth()).json()
        facts = {"facts": [_known_answer(body["fact_questions"][0])]}
        child = client.post(
            f"/v1/jobs/{finished}/answers", json=facts, headers=auth()
        ).json()["job_id"]

        held = client.get(f"/v1/jobs/{finished}/questions", headers=auth()).json()

        assert held["resumed_by"] == child
        assert (held["fact_questions"], held["link_questions"]) == ([], [])

    def test_a_failed_resumed_job_gives_its_questions_back(self):
        client, store = catalog_client()
        waited = waiting(store)
        child = client.post(
            f"/v1/jobs/{waited}/answers",
            json={"links": [], "revision": 0},
            headers=auth(),
        ).json()["job_id"]
        spent = asyncio.run(store.get(child))
        asyncio.run(store.save(spent.model_copy(update={"status": "failed"})))

        body = client.get(f"/v1/jobs/{waited}/questions", headers=auth()).json()

        assert body["resumed_by"] is None
        assert body["early_questions"]


BOTH = {"stride": {}, "asvs": {"level": 2}}


class TestTheFrameworksTakeTurns:
    """With ASVS and STRIDE selected, the frameworks take turns in a round. So
    an owner who stops after ten choices still completes a STRIDE finding
    (#1289, QA-2026-09-26-03-E29)."""

    def round_of(self, frameworks):
        return question_set(
            valid_model(),
            None,
            frameworks,
            [],
            waiting=True,
            answered=[],
            answered_links=[],
            final=False,
            shown=[],
        ).early

    def test_the_first_choices_serve_every_selected_framework(self):
        shown = self.round_of(BOTH)
        first, spent = [], 0
        for question in shown:
            if spent >= 10:
                break
            first.append(question)
            spent += question.decisions
        assert {name for q in first for name in q.frameworks} == set(BOTH)

    def test_a_part_still_follows_its_parent(self):
        keys = [q.key for q in self.round_of(BOTH)]
        for at, question in enumerate(self.round_of(BOTH)):
            if question.parent in keys:
                assert keys.index(question.parent) < at

    def test_the_selection_order_changes_nothing(self):
        backward = dict(reversed(BOTH.items()))
        assert self.round_of(BOTH) == self.round_of(backward)

    def test_one_framework_keeps_the_list_s_order(self):
        from analysis_service.answer_round import by_turn

        shown = self.round_of({"stride": {}})
        assert list(shown) == by_turn(shown)
        listed = [
            q.key
            for q in early_questions(valid_model(), {"stride": {}}, None)
            if q.key in {s.key for s in shown}
        ]
        assert [q.key for q in shown] == listed

    def test_a_shared_question_is_charged_to_each_framework_it_serves(self):
        from analysis_service.answer_round import by_turn

        def question(key, *frameworks):
            return SimpleNamespace(
                key=(key,), frameworks=frameworks, decisions=1, parent=None
            )

        shared = question("shared", "asvs", "stride")
        asvs, stride = question("a", "asvs"), question("s", "stride")
        assert [q.key for q in by_turn([shared, asvs, stride])] == [
            ("shared",),
            ("a",),
            ("s",),
        ]
        assert [q.key for q in by_turn([asvs, stride, shared])] == [
            ("a",),
            ("s",),
            ("shared",),
        ]


AUDIT = (valid_model().data_stores[0].id, "", "", "", "audit-evidence", "")


@pytest.mark.parametrize(
    ("before", "after", "refused"),
    [
        # A known facet sent back to "I don't know", alone or beside a known one.
        (
            {"records-actor": "yes", "record-protected": "no"},
            dict.fromkeys(("records-actor", "record-protected"), "unknown"),
            True,
        ),
        (
            {"records-actor": "yes", "record-protected": "no"},
            {"records-actor": "unknown", "record-protected": "yes"},
            True,
        ),
        ({"records-actor": "yes"}, {"records-actor": "unknown"}, True),
        # "I don't know" for a facet no earlier answer made known.
        ({"records-actor": "yes"}, {"record-protected": "unknown"}, False),
        (
            {"records-actor": "yes", "record-protected": "unknown"},
            {"record-protected": "unknown"},
            False,
        ),
        # A known answer, whatever was before.
        (
            {"records-actor": "yes", "record-protected": "no"},
            {"records-actor": "no"},
            False,
        ),
        (
            dict.fromkeys(("records-actor", "record-protected"), "unknown"),
            {"records-actor": "yes", "record-protected": "not applicable"},
            False,
        ),
        ({"records-actor": "yes"}, {"record-protected": "no"}, False),
    ],
)
def test_a_follow_up_refuses_to_reopen_each_known_facet(before, after, refused):
    """The follow-up refused an all-"I don't know" facet answer and took a
    mixed one that reopened a facet (#1289, F2a)."""
    earlier = [FactAnswer(key=AUDIT, facets=before)]
    answer = FactAnswer(key=AUDIT, facets=after)
    check_fact_answers([answer], valid_model(), None, earlier, reopen=True)
    if refused:
        with pytest.raises(ValueError, match="earlier answer settled '"):
            check_fact_answers([answer], valid_model(), None, earlier)
    else:
        check_fact_answers([answer], valid_model(), None, earlier)

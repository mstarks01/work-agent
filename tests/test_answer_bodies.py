"""The one reader of each answer body, at its interface.

Each accepted shape yields the typed value, and each refused shape raises a
:class:`BodyRefused` that names the Question of the refused fact answer where
the body carries one. The reader reads no job, so no test here needs one.
"""

from __future__ import annotations

import pytest

from analysis_service.answer_bodies import (
    AmendmentSubmission,
    BodyRefused,
    CorrectionsSubmission,
    read_amendment,
    read_answers,
    read_corrections,
)
from analysis_service.answer_state import MAX_SKIPS, Answers
from analysis_service.fact_answers import MAX_FACT_ANSWERS, MAX_HELD_FACTS, FactAnswer
from analysis_service.links import MAX_LINK_ANSWERS, LinkAnswer

KEY = ("flow:web-app>database", "encryption_in_transit", "", "", "", "")
OTHER = ("component:web-app", "", "", "rate_limits", "", "")
FACT = {"key": list(KEY), "value": "unknown"}
LINK = {"principal": "customer accounts", "element": "entity:customer"}


def refusal(read, body) -> BodyRefused:
    with pytest.raises(BodyRefused) as raised:
        read(body)
    return raised.value


class TestAnAcceptedBody:
    def test_every_field_of_an_answers_body_reaches_the_typed_value(self):
        body = {
            "links": [LINK],
            "facts": [FACT],
            "save": True,
            "skip": [list(OTHER), "web-app"],
            "presented": [list(KEY), list(OTHER)],
            "revision": 3,
        }

        answers = read_answers(body)

        assert answers == Answers(
            given=type(answers.given)(
                links=(LinkAnswer.model_validate(LINK),),
                facts=(FactAnswer.model_validate(FACT),),
            ),
            save=True,
            skips=(OTHER, "web-app"),
            presented=(KEY, OTHER),
            revision=3,
        )

    def test_an_answers_body_may_leave_every_field_out(self):
        assert read_answers({}) == Answers()

    def test_an_amendment(self):
        assert read_amendment({"text": "A CDN fronts it.", "revision": 0}) == (
            AmendmentSubmission(text="A CDN fronts it.", revision=0)
        )

    def test_corrections(self):
        assert read_corrections({"facts": [FACT], "revision": 1}) == (
            CorrectionsSubmission(facts=[FactAnswer.model_validate(FACT)], revision=1)
        )


class TestARefusedFactAnswerNamesItsQuestion:
    def test_a_value_over_its_bound(self):
        refused = refusal(
            read_answers, {"facts": [{"key": list(KEY), "value": "x" * 1001}]}
        )

        assert refused.question == KEY
        assert refused.reason == "String should have at most 1000 characters"

    def test_a_rule_s_own_words_lose_the_pydantic_lead(self):
        refused = refusal(
            read_answers, {"facts": [{"key": list(KEY), "facets": {"a": "yes"}}]}
        )

        assert refused.question == KEY
        assert refused.reason == "facets answer only a question kind that has facets"

    def test_the_second_fact_is_named_where_the_first_is_sound(self):
        bad = {"key": list(OTHER), "value": ""}

        refused = refusal(read_corrections, {"facts": [FACT, bad], "revision": 0})

        assert refused.question == OTHER

    @pytest.mark.parametrize(
        "key",
        [None, "a", ["a"] * 5, ["a"] * 7, ["a", "b", "c", "d", "e", 6]],
        ids=["missing", "text", "five", "seven", "not-text"],
    )
    def test_a_fact_with_a_malformed_key_names_no_question(self, key):
        fact = {"value": "x"} if key is None else {"key": key, "value": "x"}

        refused = refusal(read_answers, {"facts": [fact]})

        assert refused.question is None

    def test_a_refused_link_before_a_refused_fact_names_no_question(self):
        """The links are read first, as the body lists them."""
        bad_link = {"principal": "customer accounts", "element": "flow:x>y>z"}
        bad_fact = {"key": list(KEY), "value": ""}

        refused = refusal(read_answers, {"links": [bad_link], "facts": [bad_fact]})

        assert refused.question is None
        assert str(refused).startswith("links.0.element: ")


class TestARefusedBody:
    @pytest.mark.parametrize(
        "body", [None, [], "x", 7], ids=["null", "list", "text", "int"]
    )
    def test_a_body_that_is_not_an_object(self, body):
        for read in (read_answers, read_amendment, read_corrections):
            assert refusal(read, body).question is None

    @pytest.mark.parametrize(
        ("read", "body"),
        [
            (read_answers, {"links": [], "facts": []}),
            (read_amendment, {"text": "A CDN fronts it.", "revision": 0}),
            (read_corrections, {"facts": [FACT], "revision": 0}),
        ],
        ids=["answers", "amendment", "corrections"],
    )
    def test_a_key_the_body_does_not_take_is_refused_and_never_repeated(
        self, read, body
    ):
        refused = refusal(read, body | {"colour": "red"})

        assert "colour" not in str(refused)
        assert refused.over_a_bound is False

    @pytest.mark.parametrize(
        "skip", ["x", [["a", "b"]], [[1, 2, 3, 4, 5, 6]], [""], ["x" * 201], [7]]
    )
    def test_a_malformed_skip(self, skip):
        refused = refusal(read_answers, {"facts": [FACT], "save": True, "skip": skip})

        assert str(refused).startswith("skip")
        assert refused.question is None

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("save", "yes"),
            ("save", 1),
            ("revision", True),
            ("revision", "0"),
            ("revision", -1),
            ("presented", [["a"] * 5]),
            ("links", {}),
            ("facts", "x"),
        ],
    )
    def test_each_field_is_read_strictly(self, field, value):
        refused = refusal(read_answers, {field: value})

        assert str(refused).startswith(field)

    def test_an_amendment_needs_a_text_and_a_revision(self):
        assert str(refusal(read_amendment, {})) == (
            "text: Field required; revision: Field required"
        )
        assert str(refusal(read_amendment, {"text": 7, "revision": 0})).startswith(
            "text: "
        )

    def test_corrections_need_a_revision(self):
        assert str(refusal(read_corrections, {"facts": [FACT]})) == (
            "revision: Field required"
        )


class TestABound:
    @pytest.mark.parametrize(
        ("field", "item", "bound"),
        [
            ("links", LINK, MAX_LINK_ANSWERS),
            ("facts", FACT, MAX_FACT_ANSWERS),
            ("skip", "web-app", MAX_SKIPS),
            ("presented", list(KEY), MAX_HELD_FACTS),
        ],
    )
    def test_each_list_of_an_answers_body_is_refused_with_its_bound(
        self, field, item, bound
    ):
        assert read_answers({field: [item] * bound})

        refused = refusal(read_answers, {field: [item] * (bound + 1)})

        assert refused.over_a_bound is True
        assert str(bound) in str(refused)
        assert str(refused).startswith(f"{field}: ")

    def test_corrections_are_refused_with_their_bound(self):
        body = {"facts": [FACT] * (MAX_FACT_ANSWERS + 1), "revision": 0}

        refused = refusal(read_corrections, body)

        assert refused.over_a_bound is True
        assert str(MAX_FACT_ANSWERS) in str(refused)

    def test_a_value_over_its_bound_is_not_a_list_over_a_bound(self):
        refused = refusal(
            read_answers, {"facts": [{"key": list(KEY), "value": "x" * 1001}]}
        )

        assert refused.over_a_bound is False

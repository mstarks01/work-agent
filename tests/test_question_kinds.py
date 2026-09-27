"""Question kinds: a closed table the critic picks from, about one element.

The critic's free-text questions level off as kinds and never as texts
(``QA-2026-09-26-03-E8``), so a kind from this table and an element of the
model name a question the same way whichever critic sample asks it. These
tests hold the table, the provider schema, the review seam, the questions a
report asks, the answers, and the fallback count to one another.
"""

from __future__ import annotations

import re

import pytest

from analysis_service.claims import ProposedVerdict, UnknownRef
from analysis_service.critic import review_issues
from analysis_service.links import with_link_answers
from analysis_service.open_facts import element_names, label_of
from analysis_service.question_kinds import QUESTION_KINDS
from analysis_service.questions import (
    FactAnswer,
    check_fact_answers,
    question_fallback,
)
from analysis_service.sources import Source
from tests.factories import sample_draft, sample_ruling, valid_model

STORE = "store:orders-db"


class TestTheTable:
    @pytest.mark.parametrize("name", sorted(QUESTION_KINDS))
    def test_a_kind_is_a_slug_with_one_element_slot(self, name):
        assert re.fullmatch(r"[a-z]+(-[a-z]+)*", name)
        assert QUESTION_KINDS[name].template.count("{element}") == 1

    def test_the_maintainer_reviewed_every_row_and_the_table_says_so(self):
        """Provenance in a field the code reads, never in a sentence."""
        assert {kind.reviewed_by for kind in QUESTION_KINDS.values()} == {"mstarks01"}

    def test_the_provider_schema_lists_every_kind(self):
        schema = UnknownRef.model_json_schema()["properties"]["question"]
        assert schema["enum"] == ["", *QUESTION_KINDS]


def rulings(ref: UnknownRef):
    return [
        sample_ruling(
            "S-01",
            verdict=ProposedVerdict(reason="rests on it", related_unknowns=[ref]),
        )
    ]


class TestTheSeam:
    def issues(self, ref):
        problems = review_issues([sample_draft("S-01")], rulings(ref), valid_model())
        return [m for m in problems.messages if "question" in m]

    def test_a_kind_about_an_element_of_the_model_passes(self):
        assert (
            self.issues(UnknownRef(element_id=STORE, question="audit-evidence")) == []
        )

    def test_a_kind_about_an_element_the_model_lacks_is_sent_back(self):
        ref = UnknownRef(element_id="store:nowhere", question="audit-evidence")
        assert self.issues(ref)

    def test_a_kind_the_table_lacks_is_sent_back(self):
        ref = UnknownRef.model_construct(
            element_id=STORE, attribute="", subject="", assertion="", question="vibes"
        )
        assert self.issues(ref)


def test_a_kind_is_its_own_spelling_and_not_an_attribute():
    ref = UnknownRef(element_id=STORE, question="capacity-limits")
    assert not ref.names_an_element
    assert ref.key != UnknownRef(element_id=STORE, question="audit-evidence").key


def test_the_label_is_the_kind_s_question_about_the_element_s_name():
    ref = UnknownRef(element_id=STORE, question="stored-copy-access")
    label = label_of(ref, element_names(valid_model()))
    assert label == QUESTION_KINDS["stored-copy-access"].template.format(
        element="Orders DB"
    )


class TestTheAnswer:
    KEY = (STORE, "", "", "", "audit-evidence")

    def test_it_is_free_text_about_an_element_that_exists(self):
        check_fact_answers(
            [FactAnswer(key=self.KEY, value="Postgres audit log")], valid_model(), None
        )

    def test_it_is_refused_about_an_element_that_does_not(self):
        wrong = FactAnswer(
            key=("store:nowhere", "", "", "", "audit-evidence"), value="x"
        )
        with pytest.raises(ValueError, match="no question"):
            check_fact_answers([wrong], valid_model(), None)

    def test_its_line_asks_the_kind_s_question(self):
        answer = FactAnswer(key=self.KEY, value="Postgres audit log")
        (_, answers) = with_link_answers([Source.description("an app")], [], [answer])
        asked = QUESTION_KINDS["audit-evidence"].template.format(element=STORE)
        assert f'Asked "{asked}"' in answers.text


def test_the_fallback_counts_typed_and_free_text_facts():
    from tests.factories import sample_report

    report = sample_report()
    counted = question_fallback(report.analyses)
    assert counted.typed == 0
    assert counted.rate is None or 0 <= counted.rate <= 1

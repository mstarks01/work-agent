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

from analysis_service.assertions import AssertionCatalog, AssertionRecord
from analysis_service.claims import ProposedVerdict, UnknownRef
from analysis_service.critic import review_issues, snap_rulings
from analysis_service.fact_answers import STORED, FactAnswer
from analysis_service.fact_writes import check_fact_answers
from analysis_service.jobs import Checkpoint
from analysis_service.links import with_link_answers
from analysis_service.open_facts import element_names, label_of
from analysis_service.prompts import compose_critic_prompt, compose_recritic_prompt
from analysis_service.question_kinds import QUESTION_KINDS
from analysis_service.questions import question_fallback
from analysis_service.sources import Source
from analysis_service.system_model import UNKNOWN
from tests import test_critic_review_replay
from tests.factories import sample_draft, sample_report, sample_ruling, valid_model
from tests.test_questions import resume

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


class TestTheSeam:
    def issues(self, ref):
        problems = review_issues([sample_draft("S-01")], rulings_of(ref), valid_model())
        return [m for m in problems.messages if "question" in m]

    def test_a_kind_about_an_element_of_the_model_passes(self):
        assert (
            self.issues(UnknownRef(element_id=STORE, question="audit-evidence")) == []
        )

    def test_a_kind_about_an_element_the_model_lacks_is_sent_back(self):
        ref = UnknownRef(element_id="store:nowhere", question="audit-evidence")
        assert self.issues(ref)

    def test_a_kind_about_a_mistyped_element_is_snapped_and_passes(self):
        ref = UnknownRef(element_id="store:Orders-DB", question="audit-evidence")
        (ruling,) = snap_rulings(rulings_of(ref), {STORE})
        assert ruling.verdict.related_unknowns[0].element_id == STORE
        assert self.issues(ref) == []

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
    KEY = (STORE, "", "", "", "audit-evidence", "")

    def test_it_is_answered_in_its_facets(self):
        answer = FactAnswer(key=self.KEY, facets={"records-actor": "yes"})
        check_fact_answers([answer], valid_model(), None)
        assert answer.value == "Does it record who performed each action? yes"

    def test_free_text_is_refused_where_the_kind_has_facets(self):
        answer = FactAnswer(key=self.KEY, value="Postgres audit log")
        with pytest.raises(ValueError, match="answered in its facets"):
            check_fact_answers([answer], valid_model(), None)

    def test_it_is_refused_about_an_element_that_does_not(self):
        wrong = FactAnswer(
            key=("store:nowhere", "", "", "", "audit-evidence", ""),
            facets={"records-actor": "yes"},
        )
        with pytest.raises(ValueError, match="no question"):
            check_fact_answers([wrong], valid_model(), None)

    def test_its_line_asks_the_kind_s_question(self):
        answer = FactAnswer(key=self.KEY, facets={"records-actor": "yes"})
        (_, answers) = with_link_answers([Source.description("an app")], [], [answer])
        asked = QUESTION_KINDS["audit-evidence"].template.format(element=STORE)
        assert f'Asked "{asked}"' in answers.text


def test_the_fallback_counts_typed_and_free_text_facts():
    report = sample_report()
    counted = question_fallback(report.analyses)
    assert counted.typed == 0
    assert counted.rate is None or 0 <= counted.rate <= 1


#: What a critic wrote on a route that treated the schema as a hint: a sound
#: attribute reference, then the same fact again with the attribute's name in
#: ``subject`` and schema punctuation in ``assertion`` (``QA-2026-09-26-03-E10``).
SOUND = UnknownRef(element_id=STORE, attribute="encryption_at_rest")
ECHO = UnknownRef(
    element_id=STORE,
    attribute="encryption_at_rest",
    subject="encryption_at_rest",
    assertion="}],",
)


class TestOneSpellingPerFact:
    @pytest.mark.parametrize(
        ("ref", "spellings"),
        [
            (SOUND, ("attribute",)),
            (UnknownRef(element_id=STORE, question="audit-evidence"), ("question",)),
            (UnknownRef(assertion="row"), ("assertion",)),
            (UnknownRef(subject="whether queries are bound"), ("subject",)),
            (ECHO, ("attribute", "assertion", "subject")),
        ],
    )
    def test_each_spelling_is_named(self, ref, spellings):
        assert ref.spellings == spellings

    def test_a_mixed_entry_with_a_sound_twin_is_dropped(self):
        (ruling,) = snap_rulings(rulings_of(SOUND, ECHO, SOUND), {STORE})
        assert ruling.verdict.related_unknowns == [SOUND]

    def test_a_mixed_entry_with_no_twin_stays_and_is_sent_back(self):
        (ruling,) = snap_rulings(rulings_of(ECHO), {STORE})
        assert ruling.verdict.related_unknowns == [ECHO]
        problems = review_issues(
            [sample_draft("S-01")], rulings_of(ECHO), valid_model()
        )
        assert any("3 ways at once" in m for m in problems.messages)

    def test_a_mixed_entry_is_neither_typed_nor_free_text(self):
        report = sample_report()
        (block,) = report.analyses[:1]
        claim = block.claims[0]
        verdict = claim.verdict.model_copy(update={"related_unknowns": [ECHO]})
        block = block.model_copy(
            update={"claims": [claim.model_copy(update={"verdict": verdict})]}
        )
        counted = question_fallback([block])
        assert (counted.typed, counted.free_text) == (0, 0)


def rulings_of(*refs: UnknownRef):
    return [
        sample_ruling(
            "S-01",
            verdict=ProposedVerdict(reason="rests on it", related_unknowns=list(refs)),
        )
    ]


@pytest.mark.parametrize("compose", [compose_critic_prompt, compose_recritic_prompt])
def test_a_review_prompt_lists_every_kind_with_what_it_covers(compose):
    prompt = compose(test_critic_review_replay.PROMPT_LOADER)
    for name, kind in QUESTION_KINDS.items():
        assert f"- `{name}`: {kind.covers}" in prompt


class TestTheFacets:
    """A compound kind is answered in short parts (#1289)."""

    def test_a_kind_has_facets_exactly_where_it_is_answered_in_them(self):
        for name, kind in QUESTION_KINDS.items():
            assert bool(kind.facets) == (kind.answer == "facets"), name

    def test_facet_ids_are_distinct_within_a_kind(self):
        for kind in QUESTION_KINDS.values():
            ids = [facet.id for facet in kind.facets]
            assert len(ids) == len(set(ids))

    def test_every_facet_is_a_question(self):
        for kind in QUESTION_KINDS.values():
            for facet in kind.facets:
                assert facet.question.endswith("?")

    def test_the_value_lists_the_facets_in_the_kind_s_order(self):
        key = (STORE, "", "", "", "capacity-limits", "")
        answer = FactAnswer(key=key, facets={"quota": "no", "rate": "yes"})
        assert answer.value == (
            "Does it limit the rate of requests? yes;"
            " Does it set a quota per user or tenant? no"
        )

    def test_only_unknown_facets_are_an_unknown_answer(self):
        key = (STORE, "", "", "", "capacity-limits", "")
        answer = FactAnswer(key=key, facets={"rate": UNKNOWN})
        assert not answer.known
        assert answer.value == UNKNOWN

    @pytest.mark.parametrize(
        ("facets", "error"),
        [
            ({"nope": "yes"}, "no facet"),
            ({"rate": "sometimes"}, "is not one of"),
            ({}, "at least one facet"),
        ],
    )
    def test_a_bad_facet_answer_is_refused(self, facets, error):
        with pytest.raises(ValueError, match=error):
            FactAnswer(key=(STORE, "", "", "", "capacity-limits", ""), facets=facets)

    def test_facets_are_refused_for_a_kind_without_them(self):
        with pytest.raises(ValueError, match="only a question kind that has facets"):
            FactAnswer(
                key=(STORE, "", "", "", "code-execution", ""), facets={"x": "yes"}
            )

    def test_a_value_beside_facets_must_be_the_written_one(self):
        key = (STORE, "", "", "", "capacity-limits", "")
        with pytest.raises(ValueError, match="writes a facet answer"):
            FactAnswer(key=key, facets={"rate": "yes"}, value="something else")
        written = FactAnswer(key=key, facets={"rate": "yes"})
        again = FactAnswer.model_validate(written.model_dump())
        assert again == written

    def test_a_stored_answer_keeps_the_value_the_service_wrote(self):
        """A reworded facet changes no stored answer, and a submission still
        never writes the value."""
        key = (STORE, "", "", "", "capacity-limits", "")
        stored = FactAnswer(key=key, facets={"rate": "yes"}).model_dump()
        stored["value"] = "An earlier wording of the rate facet? yes"
        read = FactAnswer.model_validate(stored, context=STORED)
        assert read.value == stored["value"]
        with pytest.raises(ValueError, match="writes a facet answer"):
            FactAnswer.model_validate(stored)

    def test_a_resumed_job_runs_with_an_answer_under_another_facet_wording(self):
        """The run seeds each answer into the graph state and ``prepare`` reads
        it back, so that read keeps the stored value too."""
        key = (STORE, "", "", "", "capacity-limits", "")
        stored = FactAnswer(key=key, facets={"rate": "yes"}).model_dump(mode="json")
        stored["value"] = "An earlier wording of the rate facet? yes"
        earlier = FactAnswer.model_validate(stored, context=STORED)
        held = Checkpoint(
            system_model=valid_model(),
            assertions=AssertionRecord(proposed=0, catalog=AssertionCatalog()),
        )

        report = resume(held, earlier=[earlier])

        assert report.system_model is not None


def test_a_blank_assertion_names_no_open_row_on_any_surface():
    """``spellings`` reads a field of blank padding as unused, and the label and
    the re-ask sentence read the spelling through it, so all three name the
    attribute (#1745 read ``fact_kind`` the same way)."""
    from analysis_service.claims import name_unknown

    ref = UnknownRef(element_id="e1", attribute="authentication", assertion=" ")

    assert ref.spellings == ("attribute",)
    assert label_of(ref, {"e1": "Gateway"}) == label_of(
        UnknownRef(element_id="e1", attribute="authentication"), {"e1": "Gateway"}
    )
    assert name_unknown(ref) == "`authentication` on `e1`"


#: The fields of one sound reference of each kind.
_ONE_KIND: dict[str, dict[str, str]] = {
    "capability": {"capability": "browser-frontend"},
    "question": {"element_id": "e1", "question": "capacity-limits"},
    "assertion": {"assertion": "assertion:a~b~any~c"},
    "attribute": {"element_id": "e1", "attribute": "authentication"},
    "subject": {"subject": "Who signs in?"},
}


@pytest.mark.parametrize(
    ("first", "second"),
    [(a, b) for a in _ONE_KIND for b in _ONE_KIND if a < b],
)
def test_every_surface_names_the_kind_that_fact_kind_reads(first, second):
    """A reference with two spellings names one kind, and every surface asks it.

    ``fact_kind`` decides the kind, so the label, the group and the re-ask
    sentence each equal those of the sound reference of that kind alone.
    """
    from analysis_service.claims import name_unknown
    from analysis_service.fact_answers import fact_kind
    from analysis_service.open_facts import group_of

    ref = UnknownRef(**(_ONE_KIND[first] | _ONE_KIND[second]))
    sound = UnknownRef(**_ONE_KIND[fact_kind(ref.key)])
    names = {"e1": "Gateway"}

    assert len(ref.spellings) == 2
    assert label_of(ref, names) == label_of(sound, names)
    assert group_of(ref) == group_of(sound)
    assert name_unknown(ref) == name_unknown(sound)

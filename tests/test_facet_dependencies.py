"""A finding can wait on some facets of a question, and an answer can carry a detail (#1542 F6, ADR 0073).

At 2fae7d3 a finding referenced a whole question kind, so an answer covered
it only once every facet was known: a finding that needed only who acted
stayed uncovered while the log's protection was unknown. A yes or no answer
also had no place for an exception. These tests drive the follow-up's
coverage, a report's conditions, the critic's prompt and the answer's line.
"""

from __future__ import annotations

from analysis_service.answer_round import question_set
from analysis_service.answer_sets import AnswerSet
from analysis_service.claims import UnknownRef
from analysis_service.fact_answers import (
    FactAnswer,
    covers,
    fact_line,
    merged_facts,
    needed_facets,
    needs_of,
)
from analysis_service.fact_writes import answered_model
from analysis_service.prompts import render_question_kinds
from analysis_service.questions import fact_questions, follow_up_needs
from analysis_service.report_conditions import conditions
from tests.factories import asking_threat, sample_analysis, sample_threat, valid_model

STORE = "store:orders-db"
ACTOR = UnknownRef(
    element_id=STORE, question="audit-evidence", facets=["records-actor"]
)
WHOLE = UnknownRef(element_id=STORE, question="audit-evidence")
KEY = WHOLE.key


def _finding(threat_id, ref):
    threat = asking_threat(ref)
    return threat.model_copy(update={"id": threat_id})


def _analyses():
    return [sample_analysis([_finding("S-01", ACTOR), _finding("S-02", WHOLE)])]


class TestTheFacetsAFindingNeeds:
    def test_a_reference_names_some_facets_of_its_kind(self):
        assert needed_facets(ACTOR) == ("records-actor",)

    def test_a_reference_that_names_none_of_its_kind_s_facets_needs_them_all(self):
        stray = WHOLE.model_copy(update={"facets": ["rate"]})
        assert needed_facets(stray) == ("records-actor", "record-protected")
        assert needed_facets(WHOLE) == ("records-actor", "record-protected")

    def test_a_fact_answered_in_one_value_has_no_facets(self):
        attribute = UnknownRef(element_id=STORE, attribute="encryption_at_rest")
        assert needed_facets(attribute) == ()

    def test_a_fact_named_twice_needs_the_facets_of_both(self):
        assert needs_of([ACTOR, WHOLE]) == {KEY: ("records-actor", "record-protected")}
        protected = WHOLE.model_copy(update={"facets": ["record-protected"]})
        assert needs_of([ACTOR, protected]) == {
            KEY: ("records-actor", "record-protected")
        }

    def test_an_answer_covers_the_facets_it_knows(self):
        answer = FactAnswer(
            key=KEY, facets={"records-actor": "yes", "record-protected": "unknown"}
        )
        assert covers(answer, ("records-actor",))
        assert not covers(answer, ("records-actor", "record-protected"))
        assert not covers(None, ("records-actor",))


class TestTheFollowUp:
    def test_a_finding_that_needs_who_acted_is_covered_without_the_log_s_protection(
        self,
    ):
        """The issue's done criterion: covered by the facet it needs, while
        the finding that needs the whole question still waits."""
        actor_only = FactAnswer(key=KEY, facets={"records-actor": "yes"})
        needs = follow_up_needs(_analyses(), valid_model(), None, [actor_only])
        assert ("stride", "S-01") not in needs.waiting
        assert needs.waiting[("stride", "S-02")] == frozenset({KEY})
        [question] = [
            q
            for q in fact_questions(_analyses(), valid_model(), None, [actor_only])
            if q.key == KEY
        ]
        assert question.findings == ("stride/S-02",)

    def test_a_question_says_which_findings_need_only_some_facets(self):
        [question] = [
            q for q in fact_questions(_analyses(), valid_model(), None) if q.key == KEY
        ]
        assert question.findings == ("stride/S-01", "stride/S-02")
        assert question.needs == {"stride/S-01": ("records-actor",)}
        assert question.to_json()["needs"] == {"stride/S-01": ["records-actor"]}

    def test_a_report_reads_the_facets_each_finding_needs(self):
        answer = FactAnswer(
            key=KEY, facets={"records-actor": "yes", "record-protected": "unknown"}
        )
        found = conditions(_analyses(), valid_model(), [answer], [KEY])
        assert [status for _, _, status in found["stride/S-01"]] == ["answered"]
        assert [status for _, _, status in found["stride/S-02"]] == ["partial"]

    def test_the_report_question_set_carries_the_needs(self):
        asked = question_set(
            valid_model(),
            None,
            {},
            _analyses(),
            waiting=False,
            answered=AnswerSet(),
            final=False,
            shown=[],
        )
        [question] = [q for q in asked.facts if q.key == KEY]
        assert question.needs == {"stride/S-01": ("records-actor",)}


class TestTheCritic:
    def test_its_kinds_list_names_each_kind_s_parts(self):
        text = render_question_kinds()
        assert "`audit-evidence`" in text and "`records-actor`" in text
        assert "Its parts:" in text

    def test_its_schema_offers_the_facets_as_a_closed_set(self):
        schema = UnknownRef.model_json_schema()["properties"]["facets"]
        assert "records-actor" in schema["items"]["enum"]
        assert "rate" in schema["items"]["enum"]

    def test_a_report_written_before_the_field_still_loads(self):
        threat = sample_threat()
        dumped = threat.model_dump(mode="json")
        for ref in dumped["verdict"]["related_unknowns"]:
            ref.pop("facets", None)
        assert type(threat).model_validate(dumped) is not None


class TestTheDetail:
    def test_it_follows_the_answer_on_its_line(self):
        answer = FactAnswer(
            key=("", "", "", "", "", "authentication"),
            value="yes",
            detail="except the public status endpoint",
        )
        assert fact_line(answer).endswith(
            'The submitter adds: "except the public status endpoint".'
        )

    def test_a_later_facet_answer_keeps_the_earlier_detail_unless_it_gives_one(self):
        first = FactAnswer(
            key=KEY, facets={"records-actor": "yes"}, detail="only writes"
        )
        more = FactAnswer(key=KEY, facets={"record-protected": "no"})
        [merged] = merged_facts([first], [more])
        assert merged.detail == "only writes"
        again = FactAnswer(key=KEY, facets={"record-protected": "no"}, detail="all")
        [merged] = merged_facts([first], [again])
        assert merged.detail == "all"

    def test_a_blank_detail_is_none(self):
        answer = FactAnswer(
            key=("", "", "", "", "", "authentication"), value="yes", detail="   "
        )
        assert answer.detail == ""

    def test_it_reaches_the_capability_the_model_states(self):
        answer = FactAnswer(
            key=("", "", "", "", "", "authentication"),
            value="yes",
            detail="except the status endpoint",
        )
        [statement] = answered_model(valid_model(), [answer]).capabilities
        assert "except the status endpoint" in statement.source_excerpt

    def test_a_changed_detail_is_new_information_for_the_follow_up(self):
        from analysis_service.answer_round import _adds_information

        before = FactAnswer(key=("", "", "", "", "", "authentication"), value="yes")
        after = before.model_copy(update={"detail": "except the status endpoint"})
        earlier = AnswerSet(facts=(before,))
        assert _adds_information(earlier, AnswerSet(facts=(after,)))
        assert not _adds_information(earlier, earlier)


class TestAFacetTwoFrameworksShare:
    """One question can serve a STRIDE finding and an ASVS ruling that each
    need a different facet of it (#1542 Package E, checkpoint review a4)."""

    def analyses(self):
        from analysis_service.claims import Verdict
        from tests.test_asvs import _block, sample_asvs_claim

        protected = WHOLE.model_copy(update={"facets": ["record-protected"]})
        ruling = sample_asvs_claim().model_copy(
            update={
                "verdict": Verdict(
                    status="needs-info",
                    reason="The sources do not state this.",
                    related_unknowns=[protected],
                )
            }
        )
        return [sample_analysis([_finding("S-01", ACTOR)]), _block(1, [ruling])]

    def test_one_question_names_each_framework_s_facets(self):
        [question] = [
            q
            for q in fact_questions(self.analyses(), valid_model(), None)
            if q.key == KEY
        ]
        assert question.findings == ("asvs/v5.0.0-6.2.1", "stride/S-01")
        assert question.needs == {
            "asvs/v5.0.0-6.2.1": ("record-protected",),
            "stride/S-01": ("records-actor",),
        }

    def test_an_answer_covers_only_the_framework_whose_facet_it_knows(self):
        actor_only = FactAnswer(key=KEY, facets={"records-actor": "yes"})
        needs = follow_up_needs(self.analyses(), valid_model(), None, [actor_only])
        assert ("stride", "S-01") not in needs.waiting
        assert needs.waiting[("asvs", "v5.0.0-6.2.1")] == frozenset({KEY})

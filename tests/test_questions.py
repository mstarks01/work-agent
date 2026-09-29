"""The open facts a report's findings rest on, asked and answered (#1225).

Held against real archived reports for the ranking, because the shapes that
matter — a finding waiting on several facts, a free-text subject, an open
assertion row — are the ones the critic writes. The answers are held against
the gate and against the real graph from ``prepare``.
"""

from __future__ import annotations

import asyncio

import pytest

from analysis_service import graph
from analysis_service.analysis import CONTROL_ATTRIBUTES, control_state
from analysis_service.assertions import (
    UNKNOWN,
    Assertion,
    AssertionCatalog,
    AssertionRecord,
    Quarantined,
    Subject,
    SupportSpan,
    assertion_id,
)
from analysis_service.claims import UnknownRef
from analysis_service.jobs import (
    Checkpoint,
    JobRecord,
    PipelineCompleted,
    Resumption,
)
from analysis_service.links import (
    LinkAnswer,
    apply_answers,
    check_answers,
    resumed_sources,
    with_link_answers,
)
from analysis_service.pipeline import AdkPipelineRunner
from analysis_service.questions import (
    ANSWERS_LABEL,
    CONTROL_SUGGESTIONS,
    FactAnswer,
    _greedy,
    answer_choices,
    answer_form,
    answer_suggestions,
    answered_model,
    check_fact_answers,
    fact_kind,
    fact_questions,
    open_attribute,
)
from analysis_service.sources import Source, text_digest
from analysis_service.system_model import ZONE_ATTRIBUTE, Assumption
from tests import test_open_facts
from tests.factories import (
    DESCRIPTION_TEXT,
    sample_selection,
    scripted_pipeline,
    valid_model,
)

DESCRIPTION = Source.description(DESCRIPTION_TEXT)

#: The archived reports and the fixture the open-facts tests read: one of each.
REPORTS = test_open_facts.REPORTS
report = test_open_facts.report


def ask(report):
    catalog = None if report.assertions is None else report.assertions.catalog
    return fact_questions(report.analyses, report.system_model, catalog)


class TestTheRanking:
    def test_answering_every_question_covers_every_waiting_finding(self, report):
        """A finding waits on the open facts its grounds cite, and its verdict's.

        A rejected draft is not counted, and an attribute the model states is
        not an open fact.
        """
        model = report.system_model

        def open_refs(refs):
            return [
                ref
                for ref in refs
                if fact_kind(ref.key) != "attribute"
                or open_attribute(model, ref.element_id, ref.attribute)
            ]

        waiting = sum(
            1
            for block in report.analyses
            for claim in block.all_claims()
            if claim.verdict.status != "rejected"
            and (
                open_refs(claim.unknown_grounds())
                or (
                    claim.verdict.status == "needs-info"
                    and open_refs(claim.verdict.related_unknowns)
                )
            )
        )
        questions = ask(report)
        assert questions[-1].covered_so_far == waiting if questions else waiting == 0

    def test_each_question_settles_at_least_as_many_as_the_last(self, report):
        settled = [question.covered_so_far for question in ask(report)]
        assert settled == sorted(settled)

    def test_the_first_question_completes_the_most_findings(self):
        """Ten findings need A, B and C together, nine need D alone (#1289).

        Asking the most cited fact first asks A and completes none.
        """
        key = lambda name: ("", "", "", name, "")
        waiting = {
            ("stride", f"T-{i}"): {key("A"), key("B"), key("C")} for i in range(10)
        }
        waiting.update({("stride", f"S-{i}"): {key("D")} for i in range(9)})

        order = _greedy(waiting)

        assert order[0] == key("D")
        assert order[1:] == [key("A"), key("B"), key("C")]

    def test_every_fact_is_asked_once(self, report):
        keys = [question.key for question in ask(report)]
        assert len(keys) == len(set(keys))


def flow_id():
    return valid_model().data_flows[0].id


def open_model():
    """The shared model with the facts these tests answer left open."""
    model = valid_model()
    model.data_flows[0].authentication = "unknown"
    model.data_flows[0].encryption_in_transit = "unknown"
    model.processes[0].exposure = "unknown"
    return model


class TestTheCriticCannotReorderTheEvidence:
    """The evidence section reads grounds only (``QA-2026-09-26-03-E7``)."""

    def rewritten(self, report):
        """The same drafts under a critic that confirmed every one of them."""
        blocks = []
        for block in report.analyses:
            claims = [
                claim.model_copy(
                    update={
                        "verdict": claim.verdict.model_copy(
                            update={"status": "confirmed", "related_unknowns": []}
                        )
                    }
                )
                for claim in block.claims
            ]
            blocks.append(block.model_copy(update={"claims": claims}))
        return report.model_copy(update={"analyses": blocks})

    def evidence(self, report):
        return [q.key for q in ask(report) if q.basis == "evidence"]

    def test_the_evidence_section_is_the_same_whatever_the_verdicts(self, report):
        assert self.evidence(self.rewritten(report)) == self.evidence(report)

    def test_the_evidence_section_comes_first(self, report):
        bases = [q.basis for q in ask(report)]
        assert bases == sorted(bases, key=lambda basis: basis != "evidence")

    def test_a_free_text_fact_is_only_ever_the_critic_s(self, report):
        assert all(q.basis == "critic" for q in ask(report) if q.kind == "subject")


class TestTheAnswerForms:
    def question_for(self, key):
        """A one-finding report is overkill here; the forms read the key alone."""
        from analysis_service.questions import answer_choices

        return answer_choices(key, valid_model(), None)

    def test_a_closed_attribute_offers_its_values_but_unknown(self):
        process = valid_model().processes[0].id
        assert self.question_for((process, "exposure", "", "", "")) == (
            "internet-facing",
            "internal",
        )

    def test_a_zone_is_answered_by_a_boundary(self):
        store = valid_model().data_stores[0].id
        boundaries = tuple(b.id for b in valid_model().trust_boundaries)
        assert self.question_for((store, "trust_zone", "", "", "")) == boundaries

    def test_a_mechanism_is_answered_in_words(self):
        assert self.question_for((flow_id(), "encryption_in_transit", "", "", "")) == ()


class TestTheChecks:
    def test_an_attribute_the_element_lacks_is_refused(self):
        wrong = FactAnswer(key=(flow_id(), "exposure", "", "", ""), value="internal")
        with pytest.raises(ValueError, match="no attribute"):
            check_fact_answers([wrong], valid_model(), None)

    def test_a_value_outside_the_choices_is_refused(self):
        process = valid_model().processes[0].id
        wrong = FactAnswer(key=(process, "exposure", "", "", ""), value="everywhere")
        with pytest.raises(ValueError, match="not one of"):
            check_fact_answers([wrong], open_model(), None)

    def test_an_open_row_the_catalog_lacks_is_refused(self):
        wrong = FactAnswer(key=("", "", "missing-row", "", ""), value="required")
        with pytest.raises(ValueError, match="no open assertion row"):
            check_fact_answers([wrong], valid_model(), AssertionCatalog())

    @pytest.mark.parametrize("field", ["id", "name", "notes", "source_excerpt"])
    def test_a_field_that_says_what_an_element_is_is_refused(self, field):
        process = valid_model().processes[0].id
        wrong = FactAnswer(key=(process, field, "", "", ""), value="process:other")
        with pytest.raises(ValueError, match="no attribute"):
            check_fact_answers([wrong], valid_model(), None)

    @pytest.mark.parametrize("field", ["source", "destination"])
    def test_a_flow_endpoint_is_refused(self, field):
        wrong = FactAnswer(key=(flow_id(), field, "", "", ""), value="entity:other")
        with pytest.raises(ValueError, match="no attribute"):
            check_fact_answers([wrong], valid_model(), None)

    def test_a_row_the_sources_settled_is_refused(self):
        settled = Assertion(
            subject=PRINCIPAL,
            predicate="mfa-requirement",
            value="required",
            basis="inferred",
            explanation="the description names two factors",
        )
        wrong = FactAnswer(key=("", "", assertion_id(settled), "", ""), value="absent")
        with pytest.raises(ValueError, match="no open assertion row"):
            check_fact_answers([wrong], valid_model(), open_catalog(settled))

    def test_a_row_an_earlier_answer_settled_can_be_answered_again(self):
        first = FactAnswer(key=("", "", assertion_id(OPEN_ROW), "", ""), value="absent")
        written, _ = apply_answers(open_catalog(), valid_model(), [], [first])
        again = first.model_copy(update={"value": "required"})
        check_fact_answers([again], valid_model(), written)

    @pytest.mark.parametrize(
        ("value", "rule"),
        [
            ("no authentication at all", "opens with 'no'"),
            ("   ", "is empty"),
            ("x" * 201, "at most 200"),
        ],
    )
    def test_a_value_the_validity_gate_refuses_is_refused(self, value, rule):
        wrong = FactAnswer(key=(flow_id(), "authentication", "", "", ""), value=value)
        with pytest.raises(ValueError, match=rule):
            check_fact_answers([wrong], open_model(), None)

    def test_a_subject_answer_is_free_text(self):
        fine = FactAnswer(
            key=("", "", "", "whether queries are bound", ""), value="yes"
        )
        check_fact_answers([fine], valid_model(), None)

    def test_an_answer_is_one_line(self):
        with pytest.raises(ValueError):
            FactAnswer(key=("", "", "", "q", ""), value="two\nlines")


def test_an_attribute_answer_is_written_onto_the_model_and_noted():
    answer = FactAnswer(
        key=(flow_id(), "encryption_in_transit", "", "", ""), value="TLS 1.3"
    )
    model = answered_model(valid_model(), [answer])
    flow = model.data_flows[0]
    assert flow.encryption_in_transit == "TLS 1.3"
    assert "TLS 1.3" in flow.notes


def test_an_attribute_answer_is_noted_once_however_many_rounds_carry_it():
    answer = FactAnswer(
        key=(flow_id(), "encryption_in_transit", "", "", ""), value="TLS 1.3"
    )
    once = answered_model(valid_model(), [answer])
    twice = answered_model(once, [answer])

    assert twice.data_flows[0].notes == once.data_flows[0].notes


def test_an_assertion_answer_replaces_its_open_row_and_passes_the_gate():
    subject = "principal:customer-accounts"
    open_row = Assertion(
        subject=subject,
        predicate="mfa-requirement",
        value=UNKNOWN,
        basis="stated",
        reason="silent",
    )
    catalog = AssertionCatalog(
        subjects=[Subject(id=subject, type="principal", label="customer accounts")],
        entries=[open_row],
    )
    answer = FactAnswer(key=("", "", assertion_id(open_row), "", ""), value="absent")
    written, issues = apply_answers(catalog, valid_model(), [], [answer])
    sources = with_link_answers([DESCRIPTION], [], [answer])
    record = AssertionRecord.over(
        written,
        valid_model(),
        {source.label: source.text for source in sources},
        proposed=1,
    )

    assert issues == []
    assert record.issues == []
    (row,) = record.catalog.entries
    assert (row.value, row.basis) == ("absent", "stated")


class TestTheResumedRunWithoutACatalog:
    """A deployment that builds no catalog can still take fact answers."""

    def test_an_attribute_answer_reaches_the_report(self):
        answer = FactAnswer(
            key=(flow_id(), "encryption_in_transit", "", "", ""), value="TLS 1.3"
        )
        record = JobRecord.create(
            owner_subject="idp|user-1",
            sources=with_link_answers([DESCRIPTION], [], [answer]),
            frameworks=sample_selection(),
            facts=[answer],
            resumption=Resumption(
                parent_id="p",
                checkpoint=Checkpoint(system_model=valid_model(), assertions=None),
            ),
        )
        record.transition("running")
        pipeline, _ = scripted_pipeline({}, entry=graph.ENTRY_RESUME)

        async def on_node(node: str) -> None:
            return None

        outcome = asyncio.run(AdkPipelineRunner(pipeline).run(record, on_node))

        assert isinstance(outcome, PipelineCompleted)
        assert outcome.report.system_model.data_flows[0].encryption_in_transit == (
            "TLS 1.3"
        )
        assert outcome.report.assertions is None


class TestTheRoutes:
    """Fact answers need no catalog, so they work on a default deployment."""

    def completed(self, store):
        from tests.factories import asking_threat, sample_report
        from tests.test_api import admit

        record = JobRecord.create(
            owner_subject="alice", sources=[DESCRIPTION], frameworks=sample_selection()
        )
        record.transition("running")
        asked = UnknownRef(
            element_id=valid_model().data_flows[1].id,
            attribute="encryption_in_transit",
        )
        record.report = sample_report([asking_threat(asked)])
        record.transition("completed")
        asyncio.run(admit(store, record))
        return record.id

    def test_a_fact_answer_resumes_a_job_where_no_catalog_is_built(self):
        from tests.test_api import auth, make_client

        client, store = make_client()
        job = self.completed(store)
        answer = {
            "key": [
                valid_model().data_flows[1].id,
                "encryption_in_transit",
                "",
                "",
                "",
            ],
            "value": "TLS 1.3",
        }
        response = client.post(
            f"/v1/jobs/{job}/answers", json={"facts": [answer]}, headers=auth()
        )
        assert response.status_code == 201, response.text
        child = asyncio.run(store.get(response.json()["job_id"]))
        assert child.facts == [FactAnswer.model_validate(answer)]
        assert child.resumption.checkpoint.assertions is None

    def test_a_fact_the_report_does_not_hold_is_refused(self):
        from tests.test_api import auth, make_client

        client, store = make_client()
        job = self.completed(store)
        wrong = {"key": [flow_id(), "exposure", "", "", ""], "value": "internal"}
        response = client.post(
            f"/v1/jobs/{job}/answers", json={"facts": [wrong]}, headers=auth()
        )
        assert response.status_code == 400

    def test_the_questions_route_lists_the_fact_questions(self):
        from tests.test_api import auth, make_client

        client, store = make_client()
        job = self.completed(store)
        body = client.get(f"/v1/jobs/{job}/questions", headers=auth()).json()
        assert "fact_questions" in body
        assert isinstance(body["fact_questions"], list)


PRINCIPAL = "principal:customer-accounts"
OPEN_ROW = Assertion(
    subject=PRINCIPAL,
    predicate="mfa-requirement",
    value=UNKNOWN,
    basis="stated",
    reason="silent",
)


def open_catalog(*entries: Assertion) -> AssertionCatalog:
    return AssertionCatalog(
        subjects=[Subject(id=PRINCIPAL, type="principal", label="customer accounts")],
        entries=list(entries or [OPEN_ROW]),
    )


def resume(checkpoint, facts=(), earlier=(), given=(DESCRIPTION,)):
    """One resumed job on the real graph from ``prepare``, and its report."""
    sources, links, answered = resumed_sources(list(given), [], [], earlier, facts)
    job = JobRecord.create(
        owner_subject="idp|user-1",
        sources=sources,
        frameworks=sample_selection(),
        links=links,
        facts=answered,
        resumption=Resumption(parent_id="job-parent", checkpoint=checkpoint),
    )
    job.transition("running")
    pipeline, _ = scripted_pipeline({}, entry=graph.ENTRY_RESUME)

    async def on_node(node: str) -> None:
        del node

    outcome = asyncio.run(AdkPipelineRunner(pipeline).run(job, on_node))
    assert isinstance(outcome, PipelineCompleted)
    return outcome.report


class TestASecondRound:
    """A report of a resumed job, answered again.

    Its checkpoint holds the first round's answered rows, and their quotes read
    the answers Source that round composed. The second round writes that
    Source again from the merged answers.
    """

    FIRST = FactAnswer(key=("", "", assertion_id(OPEN_ROW), "", ""), value="required")
    LATER = FactAnswer(key=("", "", "", "who rotates the keys?", ""), value="ops")

    def first_round(self):
        return resume(
            Checkpoint(
                system_model=valid_model(),
                assertions=AssertionRecord(proposed=1, catalog=open_catalog()),
            ),
            [self.FIRST],
        )

    def second_round(self, facts=()):
        report = self.first_round()
        checkpoint = Checkpoint(
            system_model=report.system_model, assertions=report.assertions
        )
        return resume(checkpoint, facts, earlier=[self.FIRST])

    def test_a_later_answer_keeps_the_earlier_answer_s_row(self):
        record = self.second_round([self.LATER]).assertions

        (row,) = record.catalog.entries
        assert (row.value, row.basis) == ("required", "stated")
        assert record.quarantined == []
        assert record.issues == []

    def test_the_earlier_row_quotes_this_round_s_answers_source(self):
        report = self.second_round([self.LATER])
        (source,) = [s for s in report.input.sources if s.label == ANSWERS_LABEL]
        (row,) = report.assertions.catalog.entries

        assert row.support[0].digest == source.sha256

    def test_no_new_answer_reports_no_unmatched_answer(self):
        record = self.second_round().assertions

        assert [issue.code for issue in record.issues] == []

    def test_a_later_answer_to_the_same_row_replaces_the_earlier_one(self):
        record = self.second_round(
            [self.FIRST.model_copy(update={"value": "absent"})]
        ).assertions

        (row,) = record.catalog.entries
        assert row.value == "absent"


def test_a_resumed_job_keeps_the_rows_its_parent_s_gate_removed():
    removed = Quarantined(identity="assertion:gone", codes=["unsupported-assertion"])
    parent = AssertionRecord(proposed=2, catalog=open_catalog(), quarantined=[removed])

    record = resume(
        Checkpoint(system_model=valid_model(), assertions=parent)
    ).assertions

    assert record.quarantined == [removed]
    assert record.refused_rows() == parent.refused_rows() == 1


def test_a_corrected_link_answer_leaves_no_issue_from_the_earlier_round():
    wrong = LinkAnswer(principal="customer accounts", element="process:ghost")
    right = LinkAnswer(principal="customer accounts", element="entity:customer")
    first = resume_links(
        Checkpoint(
            system_model=valid_model(),
            assertions=AssertionRecord(proposed=1, catalog=open_catalog()),
        ),
        [wrong],
    )
    assert [issue.code for issue in first.assertions.issues] == ["unknown-link-element"]

    second = resume_links(
        Checkpoint(system_model=first.system_model, assertions=first.assertions),
        [right],
        earlier=[wrong],
    )

    assert second.assertions.issues == []


def resume_links(checkpoint, links, earlier=()):
    """One resumed job with link answers, on the real graph from ``prepare``."""
    sources, merged, facts = resumed_sources([DESCRIPTION], earlier, links)
    job = JobRecord.create(
        owner_subject="idp|user-1",
        sources=sources,
        frameworks=sample_selection(),
        links=merged,
        facts=facts,
        resumption=Resumption(parent_id="job-parent", checkpoint=checkpoint),
    )
    job.transition("running")
    pipeline, _ = scripted_pipeline({}, entry=graph.ENTRY_RESUME)

    async def on_node(node: str) -> None:
        del node

    outcome = asyncio.run(AdkPipelineRunner(pipeline).run(job, on_node))
    assert isinstance(outcome, PipelineCompleted)
    return outcome.report


PROOF = Source(
    kind="description",
    label="Transport facts",
    text="Transport encryption is absent on this flow.",
)


class TestAnAttributeAnswerOverACatalogRow:
    """The answer settles the attribute, even where a catalog row says otherwise.

    The row projects into the same attribute, so it would write over the
    answer before the lanes read the model (#1289, Q1).
    """

    TLS = FactAnswer(
        key=(flow_id(), "encryption_in_transit", "", "", ""), value="TLS 1.3"
    )

    def checkpoint(self):
        row = Assertion(
            subject=flow_id(),
            predicate="transport-encryption",
            value="absent",
            basis="stated",
            support=[
                SupportSpan(
                    source_label=PROOF.label,
                    digest=text_digest(PROOF.text),
                    start=0,
                    end=len(PROOF.text),
                    quote=PROOF.text,
                )
            ],
        )
        catalog = AssertionCatalog(
            subjects=[Subject(id=flow_id(), type="interaction", label="flow")],
            entries=[row],
        )
        return Checkpoint(
            system_model=valid_model(),
            assertions=AssertionRecord(proposed=1, catalog=catalog),
        )

    def encryption(self, report):
        return report.system_model.data_flows[0].encryption_in_transit

    def test_without_an_answer_the_row_projects(self):
        assert self.encryption(
            resume(self.checkpoint(), given=[DESCRIPTION, PROOF])
        ) == ("none")

    def test_the_answer_reaches_the_model_and_removes_the_row(self):
        report = resume(self.checkpoint(), [self.TLS], given=[DESCRIPTION, PROOF])

        assert self.encryption(report) == "TLS 1.3"
        assert report.assertions.catalog.entries == []
        assert [i.code for i in report.assertions.issues] == ["superseded-by-answer"]
        assert report.assertions.refused_rows() == 0

    def test_a_later_round_keeps_the_answer_and_the_issue(self):
        first = resume(self.checkpoint(), [self.TLS], given=[DESCRIPTION, PROOF])
        second = resume(
            Checkpoint(system_model=first.system_model, assertions=first.assertions),
            earlier=[self.TLS],
            given=[DESCRIPTION, PROOF],
        )

        assert self.encryption(second) == "TLS 1.3"
        assert [i.code for i in second.assertions.issues] == ["superseded-by-answer"]

    def test_a_revised_answer_replaces_the_first(self):
        first = resume(self.checkpoint(), [self.TLS], given=[DESCRIPTION, PROOF])
        second = resume(
            Checkpoint(system_model=first.system_model, assertions=first.assertions),
            [self.TLS.model_copy(update={"value": "none"})],
            earlier=[self.TLS],
            given=[DESCRIPTION, PROOF],
        )

        assert self.encryption(second) == "none"


class TestAnAnsweredZone:
    """An answered zone is no longer an inference (#1289, Q3)."""

    PROCESS = "process:web-app"

    def model(self):
        model = valid_model()
        model.assumptions.append(
            Assumption(
                assumption="the web app sits in the internal network",
                element_id=self.PROCESS,
                attribute=ZONE_ATTRIBUTE,
                basis="the sources are silent",
            )
        )
        return model

    @pytest.mark.parametrize("confirm", [True, False])
    def test_confirming_or_changing_it_removes_the_assumption(self, confirm):
        model = self.model()
        zones = [boundary.id for boundary in model.trust_boundaries]
        held = model.get(self.PROCESS).trust_zone
        value = held if confirm else next(zone for zone in zones if zone != held)
        answer = FactAnswer(key=(self.PROCESS, ZONE_ATTRIBUTE, "", "", ""), value=value)

        answered = answered_model(model, [answer])

        assert self.PROCESS not in answered.assumed_zone_elements()
        assert answered.get(self.PROCESS).trust_zone == value


class TestTheAdmissionCheck:
    """A submission that would place nothing is refused before admission (Q5)."""

    @pytest.mark.parametrize(
        "key",
        [
            (flow_id(), "authentication", "", "", "capacity-limits"),
            (flow_id(), "authentication", "", "who rotates keys?", ""),
            ("", "", "", "", ""),
        ],
    )
    def test_a_key_that_names_two_facts_or_none_is_refused(self, key):
        with pytest.raises(ValueError, match="names one fact"):
            check_fact_answers([FactAnswer(key=key, value="x")], valid_model(), None)

    @pytest.mark.parametrize(
        "link",
        [
            LinkAnswer(principal="customer accounts", element="process:ghost"),
            LinkAnswer(principal="nobody at all", element="entity:customer"),
        ],
    )
    def test_a_link_that_would_place_nothing_is_refused(self, link):
        with pytest.raises(ValueError):
            check_answers([link], [], valid_model(), open_catalog(), ())

    def test_a_link_that_places_is_admitted(self):
        link = LinkAnswer(principal="customer accounts", element="entity:customer")
        check_answers([link], [], valid_model(), open_catalog(), ())


def test_the_same_answers_twice_give_the_same_catalog():
    """A repeated submission changes nothing (#1289, Q2)."""
    rounds = TestASecondRound()
    first = rounds.first_round().assertions
    again = rounds.second_round([rounds.FIRST]).assertions

    assert again.catalog.entries == first.catalog.entries
    assert again.issues == first.issues == []


class TestAnAnswerBindsToAnOpenFact:
    """A submission answers what is open, or what an earlier round answered (D2)."""

    STATED = FactAnswer(
        key=(flow_id(), "encryption_in_transit", "", "", ""), value="plaintext"
    )

    def test_an_attribute_the_model_states_is_refused(self):
        with pytest.raises(ValueError, match="is stated"):
            check_fact_answers([self.STATED], valid_model(), None)

    def test_an_attribute_an_earlier_round_answered_may_be_answered_again(self):
        earlier = self.STATED.model_copy(update={"value": "TLS 1.3"})
        check_fact_answers([self.STATED], valid_model(), None, [earlier])

    def test_an_inferred_zone_is_open(self):
        model = TestAnAnsweredZone().model()
        zone = model.get(TestAnAnsweredZone.PROCESS).trust_zone
        answer = FactAnswer(
            key=(TestAnAnsweredZone.PROCESS, ZONE_ATTRIBUTE, "", "", ""), value=zone
        )
        check_fact_answers([answer], model, None)

    def test_the_report_does_not_ask_what_it_refuses(self, report):
        """Every attribute question the report lists takes an answer."""
        model = report.system_model
        for question in ask(report):
            if question.kind == "attribute":
                assert open_attribute(model, question.key[0], question.key[1])


class TestAnUnknownAnswer:
    """``unknown`` says the submitter does not know, and the fact stays open (D3)."""

    DONT_KNOW = FactAnswer(
        key=(valid_model().data_flows[1].id, "encryption_in_transit", "", "", ""),
        value=UNKNOWN,
    )

    def test_it_is_admitted_for_a_closed_attribute_too(self):
        process = valid_model().processes[0].id
        answer = FactAnswer(key=(process, "exposure", "", "", ""), value=UNKNOWN)
        check_fact_answers([answer], open_model(), None)

    def test_it_writes_only_a_note(self):
        model = answered_model(valid_model(), [self.DONT_KNOW])
        flow = model.data_flows[1]

        assert flow.encryption_in_transit == UNKNOWN
        assert "does not know encryption_in_transit" in flow.notes

    def test_it_leaves_an_open_row_open(self):
        answer = FactAnswer(key=("", "", assertion_id(OPEN_ROW), "", ""), value=UNKNOWN)
        written, issues = apply_answers(open_catalog(), valid_model(), [], [answer])

        assert written.entries == [OPEN_ROW]
        assert issues == []

    @pytest.mark.parametrize("typed", ["I don't know", "I don\u2019t know", "TBD"])
    def test_a_typed_doubt_about_a_control_is_refused(self, typed):
        """The page's choice sends ``unknown``; typed words read as a control."""
        answer = FactAnswer(key=self.DONT_KNOW.key, value=typed)
        model = valid_model()
        model.data_flows[1].authentication = UNKNOWN
        key = (model.data_flows[1].id, "authentication", "", "", "")
        with pytest.raises(ValueError, match="opens with"):
            check_fact_answers([answer.model_copy(update={"key": key})], model, None)

    def test_it_is_refused_over_an_earlier_answer(self):
        earlier = self.DONT_KNOW.model_copy(update={"value": "TLS 1.3"})
        with pytest.raises(ValueError, match="earlier answer settled"):
            check_fact_answers([self.DONT_KNOW], valid_model(), None, [earlier])


def test_a_rejected_draft_ranks_the_questions_but_is_not_counted(report):
    counted = {f for question in ask(report) for f in question.findings}
    rejected = {
        f"{block.framework}/{claim.id}"
        for block in report.analyses
        for claim in block.all_claims()
        if claim.verdict.status == "rejected"
    }
    assert not counted & rejected


class TestTheControlForm:
    """A free-text control is asked as none, unknown, or a named mechanism."""

    def test_the_suggestions_answer_every_free_text_control(self):
        """Derived from the element classes, so a new control needs a row."""
        free_text = {
            attribute
            for element in valid_model().elements()
            for attribute in CONTROL_ATTRIBUTES
            if attribute in type(element).model_fields
            and not answer_choices(
                (element.id, attribute, "", "", ""), valid_model(), None
            )
        }
        assert set(CONTROL_SUGGESTIONS) == free_text

    @pytest.mark.parametrize(
        ("attribute", "value"),
        [(a, v) for a, values in CONTROL_SUGGESTIONS.items() for v in values],
    )
    def test_every_suggestion_is_an_answer_the_gate_reads_as_stated(
        self, attribute, value
    ):
        model = open_model()
        model.data_flows[0].encryption_in_transit = UNKNOWN
        store = model.data_stores[0]
        store.encryption_at_rest = UNKNOWN
        element = store.id if attribute == "encryption_at_rest" else flow_id()
        answer = FactAnswer(key=(element, attribute, "", "", ""), value=value)

        check_fact_answers([answer], model, None)
        assert control_state(value) == "stated"

    @pytest.mark.parametrize(
        ("key", "form"),
        [
            ((flow_id(), "authentication", "", "", ""), "control"),
            (("process:web-app", "exposure", "", "", ""), "choice"),
            (("process:web-app", "", "", "", "capacity-limits"), "facets"),
            (("process:web-app", "", "", "", "code-execution"), "choice"),
            (("", "", "", "who rotates the keys?", ""), "text"),
        ],
    )
    def test_each_fact_takes_its_form(self, key, form):
        assert answer_form(key, valid_model(), None) == form
        assert bool(answer_suggestions(key)) == (form == "control")

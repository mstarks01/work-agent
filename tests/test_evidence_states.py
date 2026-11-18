"""Evidence states carried from the fan-in to the assembled claim (#2270).

Each negative case sits beside a valid neighbour that differs in the one fact
the case is about, so a test that passes for the wrong reason fails its pair.
The repair that moves a negation or a number has its pair in
``tests/test_fan_in.py`` (#2297).
"""

from __future__ import annotations

import pytest

from analysis_service.assertions import (
    ABSENT,
    UNKNOWN,
    Qualifier,
    Subject,
    apply_projection,
    assertion_id,
    catalog_coverage,
    contradicted,
    contradictions,
    support_span,
)
from analysis_service.claims import Ground, ProposedVerdict, Ruling, UnknownRef, Verdict
from analysis_service.critic import assemble_claims
from analysis_service.evidence import (
    absent_evidence_ref,
    evidence_catalog,
    ground_issues,
)
from analysis_service.fan_in import DraftJoinError, JoinedDrafts, join_drafts
from analysis_service.frameworks import schemas_for
from analysis_service.frameworks.stride import STRIDE
from analysis_service.sources import ANSWERS_LABEL
from analysis_service.system_model import DataStore, SystemModel
from tests import test_assertions
from tests.factories import sample_draft, sample_ruling, valid_model
from tests.test_assertions import FLOW, stated
from tests.test_evidence import assertions, row
from tests.test_fan_in import CROSSING

SCHEMAS = schemas_for("stride")
ORDERS = "store:orders-db"
FIRST = "System description"
SECOND = "Payment notes"


def _with_encryption(value: str) -> SystemModel:
    """The sample model with the orders store's encryption at rest set to ``value``."""
    model = valid_model()
    store = model.get(ORDERS)
    assert isinstance(store, DataStore)
    store.encryption_at_rest = value
    return model


def _join(grounds, sources=None, catalog=None, model=None):
    drafts = {"spoofing": [sample_draft("S-01", grounds=grounds)]}
    return join_drafts(
        drafts, STRIDE, model or valid_model(), sources or {}, assertions=catalog
    )


def _status(
    joined: JoinedDrafts, ruling: Ruling | None = None, model: SystemModel | None = None
) -> Verdict:
    assembled = assemble_claims(
        joined.drafts,
        [ruling or sample_ruling("S-01")],
        model or valid_model(),
        SCHEMAS,
        {},
    )
    claims = [*assembled.claims, *assembled.rejected_claims]
    assert len(claims) == 1
    return claims[0].verdict


#: Two sources that say different things.
SPLIT_SOURCES = {
    FIRST: "Customers log in to the web app, which stores orders.",
    SECOND: "Payments are settled by the card processor.",
}
#: Two sources that hold one sentence.
SAME_SOURCES = {
    FIRST: "The admin console has no login.",
    SECOND: "The admin console has no login.",
}


class TestTheSourceAQuoteNames:
    def test_a_quote_from_another_source_is_unverified(self):
        """Verbatim in the second source, labelled with the first: not found."""
        joined = _join(
            [
                Ground(
                    kind="quote",
                    text="Payments are settled by the card processor.",
                    source_label=FIRST,
                ),
                Ground(kind="derived-fact", flow_id=CROSSING),
            ],
            SPLIT_SOURCES,
        )

        assert [m.index for m in joined.marks.unverified_grounds] == [0]
        assert joined.marks.repaired_quotes == []

    def test_the_same_quote_under_its_own_source_verifies(self):
        joined = _join(
            [
                Ground(
                    kind="quote",
                    text="Payments are settled by the card processor.",
                    source_label=SECOND,
                ),
                Ground(kind="derived-fact", flow_id=CROSSING),
            ],
            SPLIT_SOURCES,
        )

        assert joined.marks.unverified_grounds == []
        assert joined.drafts[0].grounds[0].source_label == SECOND

    def test_a_label_that_names_no_source_leaves_the_quote_unverified(self):
        """The text sits in a source, so only the label can refuse it."""
        joined = _join(
            [
                Ground(
                    kind="quote",
                    text="Payments are settled by the card processor.",
                    source_label="Meeting notes",
                ),
                Ground(kind="derived-fact", flow_id=CROSSING),
            ],
            SPLIT_SOURCES,
        )

        (mark,) = joined.marks.unverified_grounds
        assert mark.index == 0
        assert "not one of this job's sources" in mark.reason


class TestIdenticalTextInTwoSources:
    """One sentence in two sources: each quote keeps the source it names."""

    @pytest.mark.parametrize("label", [FIRST, SECOND])
    def test_the_quote_keeps_the_label_it_names(self, label):
        joined = _join(
            [
                Ground(
                    kind="quote",
                    text="The admin console has no login.",
                    source_label=label,
                ),
                Ground(kind="derived-fact", flow_id=CROSSING),
            ],
            SAME_SOURCES,
        )

        assert joined.marks.unverified_grounds == []
        assert joined.drafts[0].grounds[0].source_label == label


class TestAbsenceAgainstSilence:
    """A stated absence is a fact in hand; an unstated control is a question.

    That silence cannot be cited as an absence is held at the join by
    ``tests/test_fan_in.py`` (a ground naming an unknown as absent).
    """

    def test_a_claim_on_a_stated_absence_can_be_confirmed(self):
        model = _with_encryption("none")
        joined = _join(
            [
                Ground(
                    kind="absent-attribute",
                    element_id=ORDERS,
                    attribute="encryption_at_rest",
                )
            ],
            model=model,
        )

        assert _status(joined, model=model).status == "confirmed"

    def test_a_claim_on_silence_is_needs_info(self):
        model = _with_encryption("unknown")
        joined = _join(
            [
                Ground(
                    kind="unknown-attribute",
                    element_id=ORDERS,
                    attribute="encryption_at_rest",
                )
            ],
            model=model,
        )

        verdict = _status(joined, model=model)
        assert verdict.status == "needs-info"
        assert verdict.related_unknowns == [
            UnknownRef(element_id=ORDERS, attribute="encryption_at_rest")
        ]


class TestAConflictCarriedToTheClaim:
    """Two rows that disagree settle nothing, from the catalog to the verdict."""

    ABSENT_ROW = row()
    REQUIRED_ROW = row(value="required")

    def test_a_claim_on_a_disputed_row_is_needs_info(self):
        catalog = assertions(self.ABSENT_ROW, self.REQUIRED_ROW)
        ref = assertion_id(self.ABSENT_ROW)
        joined = _join(
            [Ground(kind="unknown-assertion", assertion=ref)], catalog=catalog
        )

        verdict = _status(joined)
        assert verdict.status == "needs-info"
        assert verdict.related_unknowns == [UnknownRef(assertion=ref)]

    def test_a_disputed_row_cannot_be_cited_as_settled(self):
        catalog = assertions(self.ABSENT_ROW, self.REQUIRED_ROW)

        with pytest.raises(DraftJoinError, match="leaves that row open"):
            _join(
                [Ground(kind="assertion", assertion=assertion_id(self.ABSENT_ROW))],
                catalog=catalog,
            )

    def test_the_settled_row_alone_can_be_confirmed(self):
        catalog = assertions(self.ABSENT_ROW)
        joined = _join(
            [Ground(kind="assertion", assertion=assertion_id(self.ABSENT_ROW))],
            catalog=catalog,
        )

        assert _status(joined).status == "confirmed"


class TestAConfirmationOnAnOpenFact:
    """An unresolved fact the argument needs never reads as confirmed."""

    GROUND = Ground(
        kind="unknown-attribute", element_id=ORDERS, attribute="encryption_at_rest"
    )

    def test_a_confirmation_that_dismisses_nothing_is_needs_info(self):
        assert _status(_join([self.GROUND])).status == "needs-info"

    def test_a_confirmation_that_names_the_fact_immaterial_stands(self):
        ruling = sample_ruling(
            "S-01",
            verdict=ProposedVerdict(
                immaterial_unknowns=[
                    UnknownRef(element_id=ORDERS, attribute="encryption_at_rest")
                ]
            ),
        )

        assert _status(_join([self.GROUND]), ruling).status == "confirmed"


class TestARowTheGraphContradicts:
    """A source row against an opposite graph value is an open question.

    Where a projection is not written into the graph, a row stating an absence
    beside an attribute stating a control is a contradiction the report names.
    The row is not a fact against that model, so the evidence catalog offers
    it as an open question, and a claim on it reads needs-info.
    """

    STORED = row(
        subject=ORDERS,
        predicate="storage-encryption",
        value=ABSENT,
        explanation="the description says the orders store is not encrypted",
    )

    STORE = Subject(id=ORDERS, type="component", label="orders database")

    def answer(self):
        """The stored absence, written as a submitter's answer."""
        return _as_answer(self.STORED, "Orders database: not encrypted.")

    def test_an_opposite_graph_value_is_named_and_the_row_is_open(self):
        model = _with_encryption("AES-256")
        catalog = assertions(self.STORED, subjects=(self.STORE,))

        assert [c.attribute for c in contradictions(catalog, model)] == [
            "encryption_at_rest"
        ]
        ground = evidence_catalog(model, catalog)[assertion_id(self.STORED)]
        assert ground.kind == "unknown-assertion"

    def test_a_claim_on_the_contradicted_row_is_needs_info(self):
        model = _with_encryption("AES-256")
        catalog = assertions(self.STORED, subjects=(self.STORE,))
        ref = assertion_id(self.STORED)
        joined = _join(
            [Ground(kind="unknown-assertion", assertion=ref)],
            catalog=catalog,
            model=model,
        )

        verdict = _status(joined, model=model)
        assert verdict.status == "needs-info"
        assert verdict.related_unknowns == [UnknownRef(assertion=ref)]

    def test_the_contradicted_row_cannot_be_cited_as_settled(self):
        model = _with_encryption("AES-256")
        catalog = assertions(self.STORED, subjects=(self.STORE,))

        with pytest.raises(DraftJoinError, match="leaves that row open"):
            _join(
                [Ground(kind="assertion", assertion=assertion_id(self.STORED))],
                catalog=catalog,
                model=model,
            )

    def test_an_answer_against_an_opposite_graph_value_stays_settled(self):
        """An answer settles the fact even against the sources (#1225), so the
        graph's opposite value does not hold an answered row open."""
        model = _with_encryption("AES-256")
        answer = self.answer()
        catalog = assertions(answer, subjects=(self.STORE,))

        assert [c.attribute for c in contradictions(catalog, model)] == [
            "encryption_at_rest"
        ]
        ground = evidence_catalog(model, catalog)[assertion_id(answer)]
        assert ground.kind == "assertion"

    def test_a_scoped_row_beside_an_answer_stays_settled(self):
        """The answer alone makes the projected value, so the scoped row the
        projection groups beside it is not what the graph contradicts."""
        model = _with_encryption("AES-256")
        answer = self.answer()
        backups = self.STORED.model_copy(
            update={
                "value": "AES-256",
                "scope": [Qualifier(kind="resource", value="backups")],
            }
        )
        catalog = assertions(answer, backups, subjects=(self.STORE,))

        assert contradicted(catalog, model) == ()
        ground = evidence_catalog(model, catalog)[assertion_id(backups)]
        assert ground.kind == "assertion"

    def test_an_agreeing_graph_value_carries_the_row_and_names_nothing(self):
        model = _with_encryption("none")
        catalog = assertions(self.STORED, subjects=(self.STORE,))

        offered = evidence_catalog(model, catalog)
        assert contradictions(catalog, model) == ()
        assert assertion_id(self.STORED) not in offered
        ref = absent_evidence_ref(ORDERS, "encryption_at_rest")
        assert offered[ref].kind == "absent-attribute"


def _as_answer(row, said, **update):
    """``row`` written as a submitter's answer that quotes ``said``."""
    return row.model_copy(
        update={
            "origin": "answer",
            "basis": "stated",
            "support": [support_span(said, ANSWERS_LABEL, said)],
            **update,
        }
    )


class TestARejectedAnswer:
    """An answer a reviewer set aside makes no projected value, so it neither
    exempts a contradiction nor lifts the guard on an unchecked row."""

    SAID = "Orders database: encrypted with AES-256."

    def answer(self, row, value):
        return _as_answer(row, self.SAID, value=value, assessment="unsupported")

    def test_the_source_row_it_sits_beside_is_still_held_open(self):
        source = TestARowTheGraphContradicts.STORED
        catalog = assertions(
            self.answer(source, "AES-256"),
            source,
            subjects=(TestARowTheGraphContradicts.STORE,),
        )
        model = _with_encryption("AES-256")

        assert contradicted(catalog, model) == (source,)
        ground = evidence_catalog(model, catalog)[assertion_id(source)]
        assert ground.kind == "unknown-assertion"

    def test_it_does_not_lift_the_unchecked_guard(self):
        source = stated()
        catalog = assertions(
            self.answer(source, "a hardware token"),
            source,
            subjects=(Subject(id=FLOW, type="interaction", label="place order"),),
        )
        graph = test_assertions.TestAGraphAttributeAndItsRowsStatingOpposites()
        model = graph.model("unknown")

        updated, _ = apply_projection(model, catalog)

        (flow,) = updated.data_flows
        assert flow.authentication.startswith("unknown")


def test_an_answer_of_unknown_does_not_lift_the_unchecked_guard():
    """An answer that the submitter does not know makes no value, so an
    unchecked row beside it still cannot close the lead (ADR 0041)."""
    source = stated()
    dunno = _as_answer(
        source,
        "Place order: no idea how it authenticates.",
        value=UNKNOWN,
        reason="hedged",
    )
    catalog = assertions(
        dunno,
        source,
        subjects=(Subject(id=FLOW, type="interaction", label="place order"),),
    )
    graph = test_assertions.TestAGraphAttributeAndItsRowsStatingOpposites()

    updated, _ = apply_projection(graph.model("none"), catalog)

    (flow,) = updated.data_flows
    assert flow.authentication.startswith("unknown")


class TestTheGraphSideOfAContradiction:
    """A graph absence the rows contradict is no fact a lane may cite.

    The maintainer's ruling of 2026-11-18 on the #2302 open items: the
    evidence catalog drops the ``absent-attribute`` entry of a contradicted
    attribute, and the row's ``unknown-assertion`` entry carries the question.
    """

    ENCRYPTED = TestARowTheGraphContradicts.STORED.model_copy(
        update={"value": "AES-256"}
    )
    STORE = TestARowTheGraphContradicts.STORE
    ABSENT_REF = absent_evidence_ref(ORDERS, "encryption_at_rest")

    def test_the_contradicted_absence_is_not_offered(self):
        catalog = assertions(self.ENCRYPTED, subjects=(self.STORE,))
        offered = evidence_catalog(_with_encryption("none"), catalog)

        assert self.ABSENT_REF not in offered
        assert offered[assertion_id(self.ENCRYPTED)].kind == "unknown-assertion"

    def test_an_absence_no_row_contradicts_is_still_offered(self):
        """The neighbour: rows about another store leave this absence alone."""
        offered = evidence_catalog(_with_encryption("none"), assertions())

        assert offered[self.ABSENT_REF].kind == "absent-attribute"

    def test_an_answer_the_graph_contradicts_drops_the_absence_too(self):
        """An answer settles the fact even against the sources (#1225), so the
        graph's opposite value is no fact either."""
        answer = _as_answer(self.ENCRYPTED, "Orders database: AES-256 at rest.")
        catalog = assertions(answer, subjects=(self.STORE,))
        offered = evidence_catalog(_with_encryption("none"), catalog)

        assert self.ABSENT_REF not in offered
        assert offered[assertion_id(answer)].kind == "assertion"


class TestCompatibleRowsBesideAGraphAbsence:
    """Two rows that both hold, beside an extracted ``none``, in a normal job.

    The ``compatible`` projection leaves the attribute as extraction wrote it,
    so each row's own state is compared with the graph attribute (the
    maintainer's ruling of 2026-11-18).
    """

    CREDENTIAL = Subject(id="credential:api-key", type="credential", label="api key")
    FLOW_SUBJECT = Subject(id=FLOW, type="interaction", label="place order")

    def rows(self):
        return (
            stated(),
            stated(predicate="credential-presented", value="credential:api-key"),
        )

    def catalog(self):
        return assertions(*self.rows(), subjects=(self.FLOW_SUBJECT, self.CREDENTIAL))

    def model(self, authentication):
        graph = test_assertions.TestAGraphAttributeAndItsRowsStatingOpposites()
        return graph.model(authentication)

    def test_the_rows_are_open_against_an_extracted_none(self):
        catalog = self.catalog()
        model, applied = apply_projection(self.model("none"), catalog)
        offered = evidence_catalog(model, catalog)

        assert applied == ()
        assert [c.attribute for c in contradictions(catalog, model)] == [
            "authentication"
        ]
        assert contradicted(catalog, model) == self.rows()
        assert {offered[assertion_id(entry)].kind for entry in self.rows()} == {
            "unknown-assertion"
        }
        assert absent_evidence_ref(FLOW, "authentication") not in offered

    def test_the_rows_stay_settled_beside_a_stated_control(self):
        """The neighbour: the graph states a control, which the rows agree with."""
        catalog = self.catalog()
        model = self.model("session cookie")
        offered = evidence_catalog(model, catalog)

        assert contradictions(catalog, model) == ()
        assert {offered[assertion_id(entry)].kind for entry in self.rows()} == {
            "assertion"
        }


class TestADisputedPairUnderARefusedProjection:
    """Two rows that disagree over a field the gate left as extraction wrote it.

    An applied projection shows the field as a qualified unknown, which the
    attribute enumeration offers. Where the projection was not applied, the
    field states a value, so each row of the pair is offered as an open
    question (the maintainer's ruling of 2026-11-18).
    """

    STORE = TestARowTheGraphContradicts.STORE
    ABSENCE = TestARowTheGraphContradicts.STORED
    ENCRYPTED = ABSENCE.model_copy(update={"value": "AES-256", "explanation": "b"})

    def catalog(self):
        return assertions(self.ABSENCE, self.ENCRYPTED, subjects=(self.STORE,))

    def test_each_row_is_open_where_the_projection_was_not_applied(self):
        offered = evidence_catalog(_with_encryption("none"), self.catalog())

        assert offered[assertion_id(self.ABSENCE)].kind == "unknown-assertion"
        assert offered[assertion_id(self.ENCRYPTED)].kind == "unknown-assertion"

    def test_the_field_carries_the_question_where_the_projection_applied(self):
        """The neighbour: the qualified unknown is the question."""
        catalog = self.catalog()
        model, applied = apply_projection(_with_encryption("none"), catalog)
        offered = evidence_catalog(model, catalog)

        assert [projection.reason for projection in applied] == ["several-values"]
        assert assertion_id(self.ABSENCE) not in offered
        assert assertion_id(self.ENCRYPTED) not in offered
        store = model.get(ORDERS)
        assert isinstance(store, DataStore)
        assert store.encryption_at_rest.startswith(UNKNOWN)


class TestCoverageOfAContradictedAttribute:
    """Coverage reads the rule the evidence catalog reads: a row the projected
    graph contradicts is open, and its attribute is not settled (the
    maintainer's ruling of 2026-11-18)."""

    STORE = TestARowTheGraphContradicts.STORE
    STORED = TestARowTheGraphContradicts.STORED

    def test_a_contradicted_row_counts_as_open(self):
        catalog = assertions(self.STORED, subjects=(self.STORE,))
        coverage = catalog_coverage(catalog, _with_encryption("AES-256"))

        assert (coverage.settled, coverage.open) == (0, 1)

    def test_an_agreeing_row_counts_as_settled(self):
        """The neighbour: the graph holds the absence the row states."""
        catalog = assertions(self.STORED, subjects=(self.STORE,))
        coverage = catalog_coverage(catalog, _with_encryption("none"))

        assert (coverage.settled, coverage.open) == (1, 0)

    def test_an_answer_the_graph_contradicts_counts_as_settled(self):
        """An answer settles the fact even against the sources (#1225)."""
        answer = _as_answer(self.STORED, "Orders database: not encrypted.")
        catalog = assertions(answer, subjects=(self.STORE,))
        coverage = catalog_coverage(catalog, _with_encryption("AES-256"))

        assert (coverage.settled, coverage.open) == (1, 0)


def test_a_claim_on_a_contradicted_absence_is_told_why():
    """The ground check names the contradiction, not the field's state."""
    catalog = assertions(
        TestTheGraphSideOfAContradiction.ENCRYPTED,
        subjects=(TestARowTheGraphContradicts.STORE,),
    )
    ground = Ground(
        kind="absent-attribute", element_id=ORDERS, attribute="encryption_at_rest"
    )
    claim = sample_draft("S-01", grounds=[ground])

    (issue,) = ground_issues([claim], _with_encryption("none"), catalog)
    assert "the assertion rows contradict" in issue

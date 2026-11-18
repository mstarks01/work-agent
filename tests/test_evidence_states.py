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
    Qualifier,
    Subject,
    apply_projection,
    assertion_id,
    contradicted,
    contradictions,
    support_span,
)
from analysis_service.claims import Ground, ProposedVerdict, Ruling, UnknownRef, Verdict
from analysis_service.critic import assemble_claims
from analysis_service.evidence import absent_evidence_ref, evidence_catalog
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
    Neither side settles the fact, so the evidence catalog offers the row as
    an open question, and a claim on it reads needs-info.
    """

    STORED = row(
        subject=ORDERS,
        predicate="storage-encryption",
        value=ABSENT,
        explanation="the description says the orders store is not encrypted",
    )

    STORE = Subject(id=ORDERS, type="component", label="orders database")

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
        said = "Orders database: not encrypted."
        answer = self.STORED.model_copy(
            update={
                "origin": "answer",
                "basis": "stated",
                "support": [support_span(said, ANSWERS_LABEL, said)],
            }
        )
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
        said = "Orders database: not encrypted."
        answer = self.STORED.model_copy(
            update={
                "origin": "answer",
                "basis": "stated",
                "support": [support_span(said, ANSWERS_LABEL, said)],
            }
        )
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


class TestARejectedAnswer:
    """An answer a reviewer set aside makes no projected value, so it neither
    exempts a contradiction nor lifts the guard on an unchecked row."""

    SAID = "Orders database: encrypted with AES-256."

    def answer(self, row, value):
        return row.model_copy(
            update={
                "value": value,
                "origin": "answer",
                "basis": "stated",
                "assessment": "unsupported",
                "support": [support_span(self.SAID, ANSWERS_LABEL, self.SAID)],
            }
        )

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

"""Evidence states carried from the fan-in to the assembled claim (#2270).

Each negative case sits beside a valid neighbour that differs in the one fact
the case is about, so a test that passes for the wrong reason fails its pair.
The repair that moves a negation or a number has its pair in
``tests/test_fan_in.py`` (#2297).
"""

from __future__ import annotations

import pytest

from analysis_service.assertions import ABSENT, Subject, assertion_id, contradictions
from analysis_service.claims import Ground, ProposedVerdict, Ruling, UnknownRef, Verdict
from analysis_service.critic import assemble_claims
from analysis_service.evidence import evidence_catalog
from analysis_service.fan_in import DraftJoinError, JoinedDrafts, join_drafts
from analysis_service.frameworks import schemas_for
from analysis_service.frameworks.stride import STRIDE
from analysis_service.system_model import SystemModel
from tests.factories import sample_draft, sample_ruling, valid_model
from tests.test_evidence import assertions, row

SCHEMAS = schemas_for("stride")
#: A flow the sample model derives as a decided boundary crossing.
CROSSING = "flow:entity:customer>process:web-app>login"
ORDERS = "store:orders-db"
FIRST = "System description"
SECOND = "Payment notes"


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
                )
            ],
            SAME_SOURCES,
        )

        assert joined.marks.unverified_grounds == []
        assert joined.drafts[0].grounds[0].source_label == label


class TestAbsenceAgainstSilence:
    """A stated absence is a fact in hand; an unstated control is a question."""

    def _model(self, value):
        model = valid_model()
        store = model.get(ORDERS)
        store.encryption_at_rest = value
        return model

    def test_a_claim_on_a_stated_absence_can_be_confirmed(self):
        model = self._model("none")
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
        model = self._model("unknown")
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

    def test_silence_cannot_be_cited_as_an_absence(self):
        model = self._model("unknown")

        with pytest.raises(DraftJoinError, match="reads as unverified"):
            _join(
                [
                    Ground(
                        kind="absent-attribute",
                        element_id=ORDERS,
                        attribute="encryption_at_rest",
                    )
                ],
                model=model,
            )


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
    """R2, pinned: a stated row against an opposite graph value stays citable.

    Where a projection is not written into the graph, a row stating an absence
    beside an attribute stating a control is a contradiction the report names,
    and the evidence catalog still offers the row as a settled fact. Pinned as
    today's behaviour; whether the row should read as a question is open.
    """

    STORED = row(
        subject=ORDERS,
        predicate="storage-encryption",
        value=ABSENT,
        explanation="the description says the orders store is not encrypted",
    )

    STORE = Subject(id=ORDERS, type="component", label="orders database")

    def _model(self, value):
        model = valid_model()
        model.get(ORDERS).encryption_at_rest = value
        return model

    def test_an_opposite_graph_value_is_named_and_the_row_stays_settled(self):
        model = self._model("AES-256")
        catalog = assertions(self.STORED, subjects=(self.STORE,))

        assert [c.attribute for c in contradictions(catalog, model)] == [
            "encryption_at_rest"
        ]
        ground = evidence_catalog(model, catalog)[assertion_id(self.STORED)]
        assert ground.kind == "assertion"

    def test_an_agreeing_graph_value_carries_the_row_and_names_nothing(self):
        model = self._model("none")
        catalog = assertions(self.STORED, subjects=(self.STORE,))

        assert contradictions(catalog, model) == ()
        assert assertion_id(self.STORED) not in evidence_catalog(model, catalog)

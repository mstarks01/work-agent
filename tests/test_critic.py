"""Tests for the mechanical checks around the critic: join and assemble."""

import pytest

from stride_service.critic import (
    CriticOutputError,
    DraftJoinError,
    assemble_threats,
    join_drafts,
    snap_drafts,
    snap_rulings,
)
from stride_service.report import Ground, Severity, UnknownRef, Verdict
from stride_service.sources import DEFAULT_DESCRIPTION_LABEL
from tests.factories import sample_draft, sample_ruling, valid_model

LABEL = DEFAULT_DESCRIPTION_LABEL
# The job's one source, as the executor hands it to the fan-in.
SOURCES = {LABEL: "Customers log in to the web app, which stores orders."}
# A flow the sample model really derives as a boundary crossing.
CROSSING = "flow:customer-to-web-app:login"


@pytest.fixture
def model():
    return valid_model()


def severity(likelihood="medium", impact="high"):
    return Severity(
        likelihood=likelihood, impact=impact, justification="Stated model fact."
    )


class TestJoinDrafts:
    def test_merges_in_canonical_stride_order(self, model):
        merged = join_drafts(
            {
                "tampering": [sample_draft("T-01", "tampering")],
                "spoofing": [sample_draft("S-01")],
                "repudiation": [sample_draft("R-01", "repudiation")],
            },
            model,
        )
        assert [draft.id for draft in merged.drafts] == ["S-01", "T-01", "R-01"]

    def test_absent_categories_contribute_nothing(self, model):
        joined = join_drafts({"spoofing": [sample_draft()]}, model)
        assert joined.drafts == [sample_draft()]

    def test_empty_analysis_is_legal(self, model):
        assert join_drafts({}, model).drafts == []

    def test_unresolvable_element_reference_fails_closed(self, model):
        drafts = {
            "spoofing": [sample_draft(affected_element_ids=["process:does-not-exist"])]
        }
        with pytest.raises(DraftJoinError, match="not in the system model"):
            join_drafts(drafts, model)

    def test_every_bad_reference_is_reported_at_once(self, model):
        drafts = {
            "spoofing": [sample_draft("S-01", affected_element_ids=["process:ghost"])],
            "tampering": [
                sample_draft("T-01", "tampering", affected_element_ids=["store:ghost"])
            ],
        }
        with pytest.raises(DraftJoinError) as excinfo:
            join_drafts(drafts, model)
        assert "process:ghost" in str(excinfo.value)
        assert "store:ghost" in str(excinfo.value)

    def test_a_respelled_element_reference_resolves_and_is_canonicalized(self, model):
        """The fold runs before the check, and the report carries the job's ID."""
        drafts = {"spoofing": [sample_draft(affected_element_ids=["Process:Web-App"])]}
        [joined] = join_drafts(drafts, model).drafts
        assert joined.affected_element_ids == ["process:web-app"]

    def test_duplicate_threat_ids_fail_closed(self, model):
        drafts = {"spoofing": [sample_draft("S-01"), sample_draft("S-01")]}
        with pytest.raises(DraftJoinError, match="used by 2 drafts"):
            join_drafts(drafts, model)

    def test_draft_filed_under_the_wrong_category_fails_closed(self, model):
        drafts = {"spoofing": [sample_draft("T-01", "tampering")]}
        with pytest.raises(DraftJoinError, match="filed under 'spoofing'"):
            join_drafts(drafts, model)


class TestGroundReferences:
    """Set membership at the fan-in, one branch at a time.

    Every failure here is fatal. There is no re-ask path for a category agent's
    drafts — ``repair`` is extraction-only and ``recritic`` is critic-only — so
    an unresolved reference kills the job, which is why the checks are exactly
    set membership and go no further.
    """

    def grounded(self, *grounds, threat_id="S-01"):
        return {"spoofing": [sample_draft(threat_id, grounds=list(grounds))]}

    def test_a_quote_naming_a_source_the_job_never_carried(self, model):
        drafts = self.grounded(
            Ground(kind="quote", text="anything", source_label="Never submitted")
        )
        with pytest.raises(DraftJoinError, match="not one of this job's sources"):
            join_drafts(drafts, model, SOURCES)

    def test_an_unknown_attribute_on_an_element_that_does_not_exist(self, model):
        drafts = self.grounded(
            Ground(
                kind="unknown-attribute",
                element_id="store:ghost",
                attribute="encryption_at_rest",
            )
        )
        with pytest.raises(DraftJoinError, match="not in the system model"):
            join_drafts(drafts, model, SOURCES)

    def test_an_attribute_the_element_type_does_not_have(self, model):
        """An ExternalEntity has no ``encryption_at_rest``; naming one is a guess."""
        drafts = self.grounded(
            Ground(
                kind="unknown-attribute",
                element_id="entity:customer",
                attribute="encryption_at_rest",
            )
        )
        with pytest.raises(DraftJoinError, match="does not have"):
            join_drafts(drafts, model, SOURCES)

    def test_a_pointer_spelled_attribute_resolves(self, model):
        """A field name arriving as ``/exposure`` is the field ``exposure``.

        The spelling is the provider's, not the prompt's, and it used to kill
        the job here — six lanes of drafts thrown away over a leading slash on
        a name whose element really does carry the field.
        """
        drafts = self.grounded(
            Ground(
                kind="unknown-attribute",
                element_id="process:web-app",
                attribute="/exposure",
            )
        )
        assert join_drafts(drafts, model, SOURCES).drafts

    def test_a_pointer_spelled_attribute_the_element_lacks_still_fails(self, model):
        drafts = self.grounded(
            Ground(
                kind="unknown-attribute",
                element_id="entity:customer",
                attribute="/encryption_at_rest",
            )
        )
        with pytest.raises(DraftJoinError, match="does not have"):
            join_drafts(drafts, model, SOURCES)

    def test_a_derived_fact_naming_a_flow_that_does_not_cross(self, model):
        drafts = self.grounded(
            Ground(kind="derived-fact", flow_id="flow:not-a:crossing")
        )
        with pytest.raises(DraftJoinError, match="not a derived boundary crossing"):
            join_drafts(drafts, model, SOURCES)

    def test_a_respelled_source_label_resolves_to_the_jobs_label(self, model):
        """A quote whose label differs only in case cites the caller's bytes."""
        drafts = self.grounded(
            Ground(
                kind="quote",
                text="log in to the web app",
                source_label=LABEL.upper(),
            )
        )
        joined = join_drafts(drafts, model, SOURCES)
        assert joined.drafts[0].grounds[0].source_label == LABEL
        assert joined.unverified == []

    def test_the_label_half_does_not_run_without_sources(self, model):
        """The gate's own escape: no set to check against is not a wrong citation."""
        drafts = self.grounded(
            Ground(kind="quote", text="anything", source_label="Never submitted")
        )
        assert join_drafts(drafts, model).drafts


class TestQuoteVerification:
    """Marked per entry, failed closed per threat."""

    def quoting(self, text, threat_id="S-01", extra=()):
        grounds = [Ground(kind="quote", text=text, source_label=LABEL), *extra]
        return {"spoofing": [sample_draft(threat_id, grounds=grounds)]}

    def test_a_verifying_quote_is_not_marked(self, model):
        joined = join_drafts(self.quoting("log in to the web app"), model, SOURCES)
        assert joined.unverified == []

    def test_an_unfindable_quote_beside_a_good_ground_is_marked_not_fatal(self, model):
        """One bad quote beside good ones is still a justified finding."""
        drafts = self.quoting(
            "a sentence the submitter never wrote",
            extra=[Ground(kind="derived-fact", flow_id=CROSSING)],
        )
        joined = join_drafts(drafts, model, SOURCES)

        assert len(joined.drafts) == 1
        assert [(m.threat_id, m.index) for m in joined.unverified] == [("S-01", 0)]
        assert LABEL in joined.unverified[0].reason

    def test_a_threat_whose_every_ground_fails_kills_the_job(self, model):
        drafts = self.quoting("a sentence the submitter never wrote")
        with pytest.raises(DraftJoinError, match="no ground that verifies"):
            join_drafts(drafts, model, SOURCES)

    def test_the_text_check_does_not_run_without_sources(self, model):
        joined = join_drafts(self.quoting("never written anywhere"), model)
        assert joined.unverified == []


class TestAssembleThreats:
    def test_confirmed_and_needs_info_stay_together(self, model):
        drafts = [sample_draft("S-01"), sample_draft("S-02")]
        rulings = [
            sample_ruling("S-01"),
            sample_ruling(
                "S-02",
                verdict=Verdict(
                    status="needs-info",
                    reason="encryption at rest is unknown",
                    related_unknowns=[
                        UnknownRef(
                            element_id="store:orders-db", attribute="encryption_at_rest"
                        )
                    ],
                ),
            ),
        ]
        threats, rejected = assemble_threats(drafts, rulings, model)
        assert [t.id for t in threats] == ["S-01", "S-02"]
        assert rejected == []

    def test_rejected_threats_ride_in_the_audit_array(self, model):
        drafts = [sample_draft("S-01"), sample_draft("S-02")]
        rulings = [
            sample_ruling("S-01"),
            sample_ruling(
                "S-02",
                verdict=Verdict(status="rejected", reason="duplicate of S-01"),
            ),
        ]
        threats, rejected = assemble_threats(drafts, rulings, model)
        assert [t.id for t in threats] == ["S-01"]
        assert [t.id for t in rejected] == ["S-02"]

    def test_actionable_threats_are_sorted_most_severe_first(self, model):
        drafts = [
            sample_draft("S-01", severity=severity("low", "low")),
            sample_draft("S-02", severity=severity("high", "high")),
            sample_draft("S-03", severity=severity("medium", "high")),
        ]
        rulings = [sample_ruling(f"S-0{n}") for n in (1, 2, 3)]
        threats, _ = assemble_threats(drafts, rulings, model)
        assert [t.id for t in threats] == ["S-02", "S-03", "S-01"]

    def test_ties_break_on_threat_id(self, model):
        drafts = [sample_draft("S-02"), sample_draft("S-01")]
        rulings = [sample_ruling("S-02"), sample_ruling("S-01")]
        threats, _ = assemble_threats(drafts, rulings, model)
        assert [t.id for t in threats] == ["S-01", "S-02"]

    def test_a_dropped_draft_fails_closed(self, model):
        drafts = [sample_draft("S-01"), sample_draft("S-02")]
        with pytest.raises(CriticOutputError, match="dropped draft 'S-02'"):
            assemble_threats(drafts, [sample_ruling("S-01")], model)

    def test_an_invented_threat_fails_closed(self, model):
        with pytest.raises(CriticOutputError, match="no category agent drafted"):
            assemble_threats([sample_draft("S-01")], [sample_ruling("S-02")], model)

    def test_a_duplicated_ruling_fails_closed(self, model):
        drafts = [sample_draft("S-01")]
        rulings = [sample_ruling("S-01"), sample_ruling("S-01")]
        with pytest.raises(CriticOutputError, match="used by 2 drafts"):
            assemble_threats(drafts, rulings, model)

    def test_needs_info_unknowns_must_resolve(self, model):
        drafts = [sample_draft("S-01")]
        rulings = [
            sample_ruling(
                "S-01",
                verdict=Verdict(
                    status="needs-info",
                    reason="unverified control",
                    related_unknowns=[
                        UnknownRef(element_id="store:ghost", attribute="encryption")
                    ],
                ),
            )
        ]
        with pytest.raises(CriticOutputError, match="hangs its needs-info verdict"):
            assemble_threats(drafts, rulings, model)

    def test_a_needs_info_attribute_the_element_lacks_is_a_fault(self, model):
        """Checked to the same depth as the grounds surface, and no deeper.

        A ``store:orders-db`` has no ``exposure``, so this is a hallucinated
        attribute rather than a debatable one. It routes to the bounded re-ask
        rather than killing the job, which is the whole reason its twin on the
        grounds side stops at existence too.
        """
        drafts = [sample_draft("S-01")]
        rulings = [
            sample_ruling(
                "S-01",
                verdict=Verdict(
                    status="needs-info",
                    reason="unverified control",
                    related_unknowns=[
                        UnknownRef(element_id="store:orders-db", attribute="exposure")
                    ],
                ),
            )
        ]
        with pytest.raises(CriticOutputError, match="does not have"):
            assemble_threats(drafts, rulings, model)

    def test_empty_analysis_assembles_to_empty_arrays(self, model):
        assert assemble_threats([], [], model) == ([], [])


class TestRulingsMergeOntoDrafts:
    """A ruling supplies judgement; every other field comes from the draft."""

    def test_the_agents_own_fields_survive_the_critic_untouched(self, model):
        draft = sample_draft(
            "S-01",
            title="Session cookie theft",
            description="Stolen cookies let an attacker impersonate the customer.",
            affected_element_ids=["flow:customer-to-web-app:login"],
        )
        (threat,), _ = assemble_threats([draft], [sample_ruling("S-01")], model)
        assert threat.title == draft.title
        assert threat.description == draft.description
        assert threat.affected_element_ids == draft.affected_element_ids
        assert threat.mitigations == draft.mitigations

    def test_a_ruling_without_severity_keeps_the_agents_rating(self, model):
        draft = sample_draft("S-01", severity=severity("low", "medium"))
        (threat,), _ = assemble_threats([draft], [sample_ruling("S-01")], model)
        assert threat.severity == draft.severity
        assert threat.severity.level == "low"

    def test_a_ruling_with_severity_replaces_the_rating_and_its_justification(
        self, model
    ):
        draft = sample_draft("S-01", severity=severity("low", "low"))
        corrected = Severity(
            likelihood="high",
            impact="high",
            justification="The model states the flow is unauthenticated.",
        )
        rulings = [sample_ruling("S-01", severity=corrected)]
        (threat,), _ = assemble_threats([draft], rulings, model)
        assert threat.severity.likelihood == "high"
        assert threat.severity.justification == corrected.justification
        assert threat.severity.level == "critical"

    def test_the_critics_judgements_reach_the_threat(self, model):
        rulings = [sample_ruling("S-01", confidence="medium")]
        (threat,), _ = assemble_threats([sample_draft("S-01")], rulings, model)
        assert threat.confidence == "medium"
        assert threat.verdict.status == "confirmed"

    def test_threats_are_built_in_draft_order_not_ruling_order(self, model):
        drafts = [sample_draft("S-01"), sample_draft("S-02")]
        rulings = [
            sample_ruling("S-02", verdict=Verdict(status="rejected", reason="dup")),
            sample_ruling("S-01", verdict=Verdict(status="rejected", reason="dup")),
        ]
        _, rejected = assemble_threats(drafts, rulings, model)
        assert [t.id for t in rejected] == ["S-01", "S-02"]


ELEMENT_IDS = frozenset(
    {"entity:customer", "process:web-app", "store:orders-db", "flow:a-to-b:x"}
)


class TestSnapDrafts:
    def test_affected_elements_arrive_in_the_jobs_spelling(self):
        draft = sample_draft(affected_element_ids=["Process:Web-App"])
        [snapped] = snap_drafts([draft], ELEMENT_IDS)
        assert snapped.affected_element_ids == ["process:web-app"]

    def test_an_unresolvable_reference_is_left_as_written(self):
        """The check that reports it can only name what the agent typed."""
        draft = sample_draft(affected_element_ids=["process:ghost"])
        [snapped] = snap_drafts([draft], ELEMENT_IDS)
        assert snapped.affected_element_ids == ["process:ghost"]

    @pytest.mark.parametrize(
        "ground, field, expected",
        [
            (
                Ground(
                    kind="unknown-attribute",
                    element_id="Process:Web-App",
                    attribute="exposure",
                ),
                "element_id",
                "process:web-app",
            ),
            (
                Ground(kind="derived-fact", flow_id="Flow:A-to-B:X"),
                "flow_id",
                "flow:a-to-b:x",
            ),
        ],
    )
    def test_each_ground_branch_snaps_its_own_reference(self, ground, field, expected):
        draft = sample_draft(grounds=[ground])
        [snapped] = snap_drafts([draft], ELEMENT_IDS)
        assert getattr(snapped.grounds[0], field) == expected

    def test_a_quote_label_snaps_to_the_jobs_label(self):
        draft = sample_draft(
            grounds=[
                Ground(
                    kind="quote", text="anything", source_label="system  DESCRIPTION"
                )
            ]
        )
        [snapped] = snap_drafts([draft], ELEMENT_IDS, {"System description"})
        assert snapped.grounds[0].source_label == "System description"

    def test_without_labels_a_quote_is_untouched(self):
        """The in-process engine carries no sources, so there is nothing to snap."""
        draft = sample_draft(
            grounds=[Ground(kind="quote", text="anything", source_label="Whatever")]
        )
        [snapped] = snap_drafts([draft], ELEMENT_IDS)
        assert snapped.grounds[0].source_label == "Whatever"

    def test_nothing_but_references_changes(self):
        draft = sample_draft(affected_element_ids=["Process:Web-App"])
        [snapped] = snap_drafts([draft], ELEMENT_IDS)
        assert snapped.model_dump(exclude={"affected_element_ids"}) == draft.model_dump(
            exclude={"affected_element_ids"}
        )


class TestSnapRulings:
    def test_a_needs_info_element_snaps(self):
        ruling = sample_ruling(
            verdict=Verdict(
                status="needs-info",
                reason="exposure unknown",
                related_unknowns=[
                    UnknownRef(element_id="Process:Web-App", attribute="exposure")
                ],
            )
        )
        [snapped] = snap_rulings([ruling], ELEMENT_IDS)
        assert snapped.verdict.related_unknowns[0].element_id == "process:web-app"

    def test_a_ruling_carrying_no_unknowns_is_unchanged(self):
        ruling = sample_ruling()
        [snapped] = snap_rulings([ruling], ELEMENT_IDS)
        assert snapped == ruling

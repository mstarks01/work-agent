"""Tests for the mechanical checks around the critic: join and assemble."""

import pytest

from stride_service.critic import (
    CriticOutputError,
    DraftJoinError,
    assemble_threats,
    join_drafts,
)
from stride_service.report import Severity, UnknownRef, Verdict
from tests.factories import sample_draft, sample_ruling, valid_model


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
        assert [draft.id for draft in merged] == ["S-01", "T-01", "R-01"]

    def test_absent_categories_contribute_nothing(self, model):
        assert join_drafts({"spoofing": [sample_draft()]}, model) == [sample_draft()]

    def test_empty_analysis_is_legal(self, model):
        assert join_drafts({}, model) == []

    def test_unresolvable_element_reference_fails_closed(self, model):
        drafts = {
            "spoofing": [
                sample_draft(affected_element_ids=["process:does-not-exist"])
            ]
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

    def test_duplicate_threat_ids_fail_closed(self, model):
        drafts = {"spoofing": [sample_draft("S-01"), sample_draft("S-01")]}
        with pytest.raises(DraftJoinError, match="used by 2 drafts"):
            join_drafts(drafts, model)

    def test_draft_filed_under_the_wrong_category_fails_closed(self, model):
        drafts = {"spoofing": [sample_draft("T-01", "tampering")]}
        with pytest.raises(DraftJoinError, match="filed under 'spoofing'"):
            join_drafts(drafts, model)


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
        with pytest.raises(CriticOutputError, match="no analyst drafted"):
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

    def test_empty_analysis_assembles_to_empty_arrays(self, model):
        assert assemble_threats([], [], model) == ([], [])


class TestRulingsMergeOntoDrafts:
    """A ruling supplies judgement; every other field comes from the draft."""

    def test_the_analysts_own_fields_survive_the_critic_untouched(self, model):
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

    def test_a_ruling_without_severity_keeps_the_analysts_rating(self, model):
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

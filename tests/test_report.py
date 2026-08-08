"""Tests for the STRIDE report schema: severity matrix, verdict shapes,
report self-containment invariants, and JSON round-tripping."""

import pytest
from pydantic import ValidationError

from stride_service.report import (
    Ground,
    ProposedVerdict,
    Severity,
    StrideReport,
    ThreatRulings,
    UnknownRef,
    Verdict,
    build_summary,
    derive_severity_level,
)
from tests.factories import sample_report, sample_threat, valid_model


class TestSeverityMatrix:
    @pytest.mark.parametrize(
        ("likelihood", "impact", "expected"),
        [
            ("high", "high", "critical"),
            ("high", "medium", "high"),
            ("medium", "high", "high"),
            ("high", "low", "medium"),
            ("medium", "medium", "medium"),
            ("low", "high", "medium"),
            ("medium", "low", "low"),
            ("low", "medium", "low"),
            ("low", "low", "low"),
        ],
    )
    def test_matrix_covers_every_combination(self, likelihood, impact, expected):
        assert derive_severity_level(likelihood, impact) == expected

    def test_level_is_derived_when_omitted(self):
        severity = Severity(likelihood="high", impact="high", justification="x")
        assert severity.level == "critical"

    def test_matching_asserted_level_is_accepted(self):
        severity = Severity(
            likelihood="low", impact="high", level="medium", justification="x"
        )
        assert severity.level == "medium"

    def test_contradicting_asserted_level_is_rejected(self):
        with pytest.raises(ValidationError, match="contradicts the matrix"):
            Severity(
                likelihood="low", impact="low", level="critical", justification="x"
            )

    def test_the_band_is_absent_from_the_schema_but_present_in_the_payload(self):
        """Derived, never asked for — and the two halves stay independent.

        ``level`` is kept off the JSON schema so a schema-constrained model is
        never handed the field, while the value itself still rides the report.
        The three assertions above keep working because ``SkipJsonSchema``
        touches schema generation only, never validation.
        """
        assert "level" not in Severity.model_json_schema()["properties"]

        severity = Severity(likelihood="high", impact="high", justification="x")

        assert severity.model_dump()["level"] == "critical"


class TestVerdictShapes:
    def test_confirmed_needs_no_reason_or_unknowns(self):
        verdict = Verdict(status="confirmed")
        assert verdict.reason == ""
        assert verdict.related_unknowns == []

    def test_needs_info_requires_related_unknowns(self):
        with pytest.raises(ValidationError, match="at least one unknown"):
            Verdict(status="needs-info", reason="encryption unknown")

    def test_needs_info_with_unknown_ref_is_accepted(self):
        verdict = Verdict(
            status="needs-info",
            reason="encryption at rest unknown",
            related_unknowns=[
                UnknownRef(element_id="store:orders-db", attribute="encryption_at_rest")
            ],
        )
        assert verdict.related_unknowns[0].attribute == "encryption_at_rest"

    def test_an_unknown_ref_takes_a_pointer_spelled_attribute_bare(self):
        """The critic's ``related_unknowns`` reads field names the same way."""
        ref = UnknownRef(element_id="store:orders-db", attribute="/encryption_at_rest")
        assert ref.attribute == "encryption_at_rest"

    def test_rejected_requires_a_reason(self):
        with pytest.raises(ValidationError, match="must state a reason"):
            Verdict(status="rejected")

    def test_unknowns_on_non_needs_info_verdict_are_rejected(self):
        with pytest.raises(ValidationError, match="only meaningful for needs-info"):
            Verdict(
                status="confirmed",
                related_unknowns=[
                    UnknownRef(element_id="store:orders-db", attribute="x")
                ],
            )


class TestProposedVerdictCarriesNoRuleBetweenFields:
    """What the critic emits accepts everything the report's shape refuses.

    The whole of the fix: each of the three combinations below used to raise
    inside ``ThreatRulings`` on the way into session state, which kills the
    critic node — one pass over every draft in the job — and with it the run.
    They are faults, and they are reported by ``review_issues`` and fixed by
    the bounded re-ask, which is the machinery that already existed for exactly
    this and that a raise here made unreachable.
    """

    MIS_SHAPED = (
        {"status": "needs-info", "reason": "encryption unknown"},
        {
            "status": "confirmed",
            "related_unknowns": [{"element_id": "store:orders-db", "attribute": "x"}],
        },
        {"status": "rejected"},
    )

    @pytest.mark.parametrize("verdict", MIS_SHAPED)
    def test_the_report_shape_still_refuses_it(self, verdict):
        with pytest.raises(ValidationError):
            Verdict.model_validate(verdict)

    @pytest.mark.parametrize("verdict", MIS_SHAPED)
    def test_the_critic_facing_shape_accepts_it(self, verdict):
        assert ProposedVerdict.model_validate(verdict).status == verdict["status"]

    @pytest.mark.parametrize("verdict", MIS_SHAPED)
    def test_it_survives_the_nodes_own_output_schema(self, verdict):
        """The depth that matters. ADK validates ``output_schema`` on the way
        into state, so anything raising here is a dead job rather than a
        re-ask."""
        rulings = ThreatRulings.model_validate(
            {"threats": [{"id": "S-01", "confidence": "high", "verdict": verdict}]}
        )

        assert rulings.threats[0].verdict.status == verdict["status"]

    def test_the_field_level_constraints_are_untouched(self):
        """Only the rules *between* fields moved. A closed vocabulary is
        something a provider schema can carry, so it stays where it was."""
        with pytest.raises(ValidationError):
            ProposedVerdict.model_validate({"status": "maybe"})


class TestGround:
    """The flat model's ``_check_shape``, which stands in for a union.

    The union would forbid a nonsense combination in the schema itself; the
    flat object is the portable shape across six independently-vendored
    ``strong``-tier agents, and this validator is what buys back the guarantee
    — on arrival rather than at generation time.
    """

    @pytest.mark.parametrize(
        "fields",
        [
            {"kind": "quote", "text": "they never added MFA", "source_label": "Doc"},
            {
                "kind": "unknown-attribute",
                "element_id": "store:orders-db",
                "attribute": "encryption_at_rest",
            },
            {"kind": "derived-fact", "flow_id": "flow:a-to-b:x"},
        ],
    )
    def test_each_branch_accepts_its_own_fields(self, fields):
        assert Ground(**fields).kind == fields["kind"]

    @pytest.mark.parametrize(
        "fields",
        [
            {"kind": "quote", "text": "they never added MFA"},  # no label
            {"kind": "quote", "source_label": "Doc"},  # no text
            {"kind": "unknown-attribute", "element_id": "store:orders-db"},
            {"kind": "unknown-attribute", "attribute": "exposure"},
            {"kind": "derived-fact"},
        ],
    )
    def test_a_branch_missing_its_own_fields_is_rejected(self, fields):
        with pytest.raises(ValidationError, match="must carry"):
            Ground(**fields)

    def test_another_branchs_fields_are_forbidden_not_ignored(self):
        """A quote carrying an element_id is a shape error, not a tolerated extra."""
        with pytest.raises(ValidationError, match="must not carry"):
            Ground(
                kind="quote",
                text="they never added MFA",
                source_label="Doc",
                element_id="store:orders-db",
            )

    def test_unknown_fields_are_forbidden(self):
        with pytest.raises(ValidationError):
            Ground(kind="derived-fact", flow_id="flow:a-to-b:x", fact="invented")

    def test_a_pointer_spelled_attribute_arrives_bare(self):
        """``/exposure`` names the field ``exposure``, and is stored as it."""
        ground = Ground(
            kind="unknown-attribute",
            element_id="process:web-api",
            attribute="/exposure",
        )
        assert ground.attribute == "exposure"

    def test_stripping_the_pointer_does_not_invent_a_field(self):
        """The prefix is cut; whether the name resolves is still asked later."""
        ground = Ground(
            kind="unknown-attribute",
            element_id="process:web-api",
            attribute="/invented",
        )
        assert ground.attribute == "invented"

    def test_an_attribute_that_is_only_a_pointer_prefix_is_rejected(self):
        with pytest.raises(ValidationError, match="must carry"):
            Ground(
                kind="unknown-attribute", element_id="process:web-api", attribute="/"
            )


class TestThreat:
    def test_at_least_one_ground_is_required(self):
        """A finding with no machine-checkable justification is what this forbids."""
        with pytest.raises(ValidationError):
            sample_threat(grounds=[])

    def test_id_must_carry_the_category_letter(self):
        with pytest.raises(ValidationError, match="category letter"):
            sample_threat(threat_id="S-01", category="tampering")

    def test_id_pattern_is_enforced(self):
        with pytest.raises(ValidationError):
            sample_threat(threat_id="SPOOF-1", category="spoofing")

    def test_at_least_one_affected_element_is_required(self):
        with pytest.raises(ValidationError):
            sample_threat(affected_element_ids=[])


class TestReportInvariants:
    def test_sample_report_is_valid(self):
        report = sample_report()
        assert report.summary.threat_count == len(report.threats)

    def test_rejected_verdict_may_not_sit_in_threats(self):
        rejected = sample_threat(
            threat_id="T-01",
            category="tampering",
            verdict=Verdict(status="rejected", reason="ungrounded"),
        )
        with pytest.raises(ValidationError, match="belongs in rejected_threats"):
            sample_report(threats=[rejected])

    def test_rejected_threats_must_hold_rejected_verdicts(self):
        confirmed = sample_threat(threat_id="T-01", category="tampering")
        with pytest.raises(ValidationError, match="sits in rejected_threats"):
            sample_report(rejected_threats=[confirmed])

    def test_dangling_element_reference_is_rejected(self):
        threat = sample_threat(affected_element_ids=["process:ghost"])
        with pytest.raises(ValidationError, match="not in the embedded system model"):
            sample_report(threats=[threat])

    def test_dangling_unknown_ref_is_rejected(self):
        threat = sample_threat(
            verdict=Verdict(
                status="needs-info",
                reason="unclear",
                related_unknowns=[
                    UnknownRef(element_id="store:ghost", attribute="encryption_at_rest")
                ],
            ),
        )
        with pytest.raises(ValidationError, match="not in the embedded system model"):
            sample_report(threats=[threat])

    def test_duplicate_threat_ids_are_rejected(self):
        duplicate = sample_threat(threat_id="S-01")
        with pytest.raises(ValidationError, match="more than once"):
            sample_report(threats=[sample_threat(threat_id="S-01"), duplicate])

    def test_mismatched_boundary_crossings_are_rejected(self):
        report = sample_report()
        payload = report.model_dump()
        payload["boundary_crossings"] = []
        with pytest.raises(ValidationError, match="boundary_crossings"):
            StrideReport.model_validate(payload)

    def test_mismatched_summary_is_rejected(self):
        report = sample_report()
        payload = report.model_dump()
        payload["summary"]["threat_count"] += 1
        with pytest.raises(ValidationError, match="summary does not match"):
            StrideReport.model_validate(payload)


class TestSummary:
    def test_counts_derive_from_report_contents(self):
        model = valid_model()
        threats = [
            sample_threat(threat_id="S-01"),
            sample_threat(
                threat_id="I-01",
                category="information-disclosure",
                verdict=Verdict(
                    status="needs-info",
                    reason="encryption at rest unknown",
                    related_unknowns=[
                        UnknownRef(
                            element_id="store:orders-db",
                            attribute="encryption_at_rest",
                        )
                    ],
                ),
            ),
        ]
        rejected = [
            sample_threat(
                threat_id="E-01",
                category="elevation-of-privilege",
                verdict=Verdict(status="rejected", reason="ungrounded"),
            )
        ]
        summary = build_summary(threats, rejected, model)
        assert summary.threat_count == 2
        assert summary.by_category == {"spoofing": 1, "information-disclosure": 1}
        assert summary.needs_info_count == 1
        assert summary.rejected_count == 1
        assert summary.elements_analyzed == len(model.elements())


class TestSerialization:
    def test_report_roundtrips_through_json(self):
        report = sample_report()
        assert StrideReport.model_validate_json(report.model_dump_json()) == report

    def test_extra_fields_are_rejected(self):
        payload = sample_report().model_dump()
        payload["extra"] = True
        with pytest.raises(ValidationError):
            StrideReport.model_validate(payload)

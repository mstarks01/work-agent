"""Tests for the STRIDE report schema: severity matrix, verdict shapes,
report self-containment invariants, and JSON round-tripping."""

import pytest
from pydantic import ValidationError

from stride_service.report import (
    Severity,
    StrideReport,
    Threat,
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
            Severity(likelihood="low", impact="low", level="critical", justification="x")


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


class TestThreat:
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

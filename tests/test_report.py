"""Tests for the STRIDE report schema: severity matrix, verdict shapes,
report self-containment invariants, and JSON round-tripping."""

from typing import get_args

import pytest
from pydantic import ValidationError

from analysis_service.frameworks.stride.record import (
    ThreatRulings,
    build_stride_summary,
)
from analysis_service.report import (
    AnalysisMarks,
    ClaimMark,
    Ground,
    LaneCoverage,
    MissingMitigation,
    ProposedVerdict,
    RepairedQuote,
    Report,
    Severity,
    UnknownRef,
    UnresolvedEvidence,
    UnresolvedMention,
    UnresolvedReference,
    Verdict,
    derive_severity_level,
)
from analysis_service.sampling import TierSampling, sampling_fingerprint
from tests.factories import (
    sample_report,
    sample_threat,
    valid_model,
)


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
            {"claims": [{"id": "S-01", "confidence": "high", "verdict": verdict}]}
        )

        assert rulings.claims[0].verdict.status == verdict["status"]

    def test_a_malformed_threat_id_survives_the_node_too(self):
        """The critic copies IDs off a roster; a mistyped one is not a shape
        error worth a dead run.

        ``review_issues`` requires the ruled set to equal the drafted set, which
        an ill-formed ID fails on both sides at once — so a pattern here could
        only fire on an ID the reconciliation was about to reject anyway, and it
        fired earlier and fatally.
        """
        rulings = ThreatRulings.model_validate(
            {
                "claims": [
                    {
                        "id": "S-1",
                        "confidence": "high",
                        "verdict": {"status": "confirmed"},
                    }
                ]
            }
        )

        assert rulings.claims[0].id == "S-1"

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
            {
                "kind": "absent-attribute",
                "element_id": "flow:a-to-b:x",
                "attribute": "authentication",
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
            {"kind": "absent-attribute", "element_id": "flow:a-to-b:x"},
            {"kind": "absent-attribute", "attribute": "authentication"},
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

    def test_the_two_attribute_branches_are_told_apart_by_kind_alone(self):
        """They require the same two fields and forbid the same others, so the
        shape check cannot separate them and is not asked to: what an attribute
        *states* is the branch, and a record where the two disagreed would be
        the thing this validator exists to make unreachable."""
        fields = {"element_id": "flow:a-to-b:x", "attribute": "authentication"}

        unknown = Ground(kind="unknown-attribute", **fields)
        absent = Ground(kind="absent-attribute", **fields)

        assert unknown != absent
        assert unknown.model_dump(exclude={"kind"}) == absent.model_dump(
            exclude={"kind"}
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

    def test_the_record_no_longer_re_validates_an_id_it_did_not_compose(self):
        """``^[STRIDE]-\\d{2}$`` and the category-letter check are gone.

        Both asked a record to re-check a string the *service* built: the ID is
        composed by the package's own ``IdRule`` from the lane the resolver was
        called for, and the lane is stamped from the same call — so the letter
        and the category could not disagree unless the composition itself were
        wrong, which is a defect a schema pattern would hide rather than catch.
        The shape is a fact about STRIDE's ``id_format`` and nothing a second
        framework's requirement numbering would satisfy.
        """
        assert sample_threat(threat_id="S-01", category="tampering").id == "S-01"
        assert sample_threat(threat_id="SPOOF-1").id == "SPOOF-1"

    def test_at_least_one_affected_element_is_required(self):
        with pytest.raises(ValidationError):
            sample_threat(affected_element_ids=[])


class TestReportInvariants:
    def test_sample_report_is_valid(self):
        report = sample_report()
        (block,) = report.analyses
        assert block.summary.claim_count == len(block.claims)
        assert report.elements_analyzed == len(report.system_model.elements())

    def test_rejected_verdict_may_not_sit_in_threats(self):
        rejected = sample_threat(
            threat_id="T-01",
            category="tampering",
            verdict=Verdict(status="rejected", reason="ungrounded"),
        )
        with pytest.raises(ValidationError, match="belongs in rejected_claims"):
            sample_report(threats=[rejected])

    def test_rejected_threats_must_hold_rejected_verdicts(self):
        confirmed = sample_threat(threat_id="T-01", category="tampering")
        with pytest.raises(ValidationError, match="sits in rejected_claims"):
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
            Report.model_validate(payload)

    def test_mismatched_summary_is_rejected(self):
        """Recounted per block, since a report carries one summary per framework."""
        report = sample_report()
        payload = report.model_dump()
        payload["analyses"][0]["summary"]["claim_count"] += 1
        with pytest.raises(ValidationError, match="does not match the stride analysis"):
            Report.model_validate(payload)


class TestEveryClaimMarkPointsAtAThreat:
    """One rule over every mark, so a new mark type cannot arrive uncovered.

    A mark naming a threat this report does not carry annotates nothing, while
    the threat that really earned it renders as though it had checked out.
    Parametrized over the whole of ``ClaimMark`` rather than written once per
    type: the point is that the set is closed.
    """

    @pytest.mark.parametrize(
        ("field", "mark", "what"),
        [
            (
                "unresolved_references",
                UnresolvedReference(claim_id="S-09", element_id="process:ghost"),
                "unresolved reference",
            ),
            (
                "unresolved_mentions",
                UnresolvedMention(claim_id="S-09", mention="process:ghost"),
                "unresolved mention",
            ),
            (
                "unresolved_evidence",
                UnresolvedEvidence(
                    claim_id="S-09", reference="unknown:process:ghost:exposure"
                ),
                "unresolved evidence",
            ),
            (
                "missing_mitigations",
                MissingMitigation(claim_id="S-09"),
                "missing mitigation",
            ),
        ],
    )
    def test_a_mark_on_an_absent_threat_is_rejected(self, field, mark, what):
        with pytest.raises(ValidationError, match=f"{what} names claim 'S-09'"):
            sample_report(**{field: [mark]})

    def test_every_mark_type_is_covered_by_the_check(self):
        """The parametrization above is the whole of ``ClaimMark``."""
        covered = {
            UnresolvedReference,
            UnresolvedMention,
            UnresolvedEvidence,
            MissingMitigation,
        }
        assert set(get_args(ClaimMark)) == covered


class TestAnalysisMarks:
    """The five service-owned marks as one value.

    The fan-in collects them from more than one producer, so merging is the
    operation this type exists for.
    """

    def test_an_empty_set_is_the_common_case(self):
        marks = AnalysisMarks()

        assert all(getattr(marks, name) == [] for name in AnalysisMarks.model_fields)

    def test_merging_concatenates_every_list_in_order(self):
        first = AnalysisMarks(
            unresolved_mentions=[
                UnresolvedMention(claim_id="S-01", mention="process:ghost")
            ]
        )
        second = AnalysisMarks(
            unresolved_mentions=[
                UnresolvedMention(claim_id="T-02", mention="store:ghost")
            ],
            missing_mitigations=[MissingMitigation(claim_id="R-03")],
        )

        merged = first.merged_with(second)

        assert [mark.claim_id for mark in merged.unresolved_mentions] == [
            "S-01",
            "T-02",
        ]
        assert [mark.claim_id for mark in merged.missing_mitigations] == ["R-03"]
        assert merged.unverified_grounds == []

    def test_merging_covers_every_declared_mark(self):
        """A sixth mark joins by being declared, not by editing ``merged_with``.

        The method walks ``model_fields``, so this holds the whole set rather
        than the four names that happen to exist today.
        """
        empty = AnalysisMarks()

        merged = empty.merged_with(empty)

        assert set(merged.model_dump()) == set(AnalysisMarks.model_fields)


class TestCoverageRatios:
    def test_a_lane_cannot_cite_more_than_it_was_offered(self):
        with pytest.raises(
            ValidationError, match="elements_cited=3 exceeds elements=2"
        ):
            LaneCoverage(
                lane="spoofing",
                drafts=1,
                rules=2,
                rules_fired=1,
                candidates=0,
                candidates_cited=0,
                elements=2,
                elements_cited=3,
                boundary_crossings=0,
                boundary_crossings_cited=0,
                unknown_controls=0,
                unknown_controls_cited=0,
            )


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
        summary = build_stride_summary(threats, rejected)
        assert summary.claim_count == 2
        assert summary.by_category == {"spoofing": 1, "information-disclosure": 1}
        assert summary.needs_info_count == 1
        assert summary.rejected_count == 1
        # ``elements_analyzed`` is a fact about the shared model, so it left the
        # summary for the envelope: N blocks would be N chances to disagree
        # about one number.
        assert "elements_analyzed" not in summary.model_fields
        assert sample_report().elements_analyzed == len(model.elements())


class TestTheSamplingClearBlock:
    """The block records a resolved ``TierSampling``, so it has to be able to."""

    def fully_set(self) -> TierSampling:
        """Every param set, and none of them at its default.

        The shipped file leaves most of them unset, so a fixture built from it
        would exercise whichever types this repository happens to ship today —
        which is how a param's type reaches the report untested.
        """
        return TierSampling(
            temperature=0.2,
            top_p=0.9,
            seed=7,
            max_output_tokens=1024,
            candidate_count=1,
            presence_penalty=0.1,
            frequency_penalty=0.1,
            thinking="low",
            constrain_output=False,
        )

    def test_every_resolved_param_survives_a_round_trip_through_the_report(self):
        """The drift guard, and it is about a failure that costs a whole run.

        ``report.py`` cannot import ``sampling`` — that import cycles back
        through skills — so the block's value types are written out by hand and
        nothing but this ties them to the model they record. A param whose type
        the block cannot carry does not fail at config load or at the
        build-time gate: it fails at assembly, after every node has been paid
        for. ``thinking`` did exactly that.
        """
        resolved = self.fully_set()
        report = sample_report().model_copy(update={"sampling": {"base": {}}})

        stored = Report.model_validate(
            {**report.model_dump(), "sampling": {"base": resolved.model_dump()}}
        ).sampling["base"]

        assert TierSampling(**stored) == resolved

    def test_the_stored_block_recomputes_the_same_fingerprint(self):
        """Round-tripping is the requirement; the fingerprint is why.

        A value stored in a type that merely *validates* — a bool flattened to
        a number, an enum to something else — would round-trip through
        ``TierSampling`` and still hash differently, leaving every fingerprint
        in the report unverifiable by the reader it was recorded for.
        """
        resolved = self.fully_set()
        stored = Report.model_validate(
            {
                **sample_report().model_dump(),
                "sampling": {"base": resolved.model_dump()},
            }
        ).sampling["base"]

        served = "openai/gpt-4.1-mini-2025-04-14"
        assert sampling_fingerprint(served, TierSampling(**stored)) == (
            sampling_fingerprint(served, resolved)
        )


class TestSerialization:
    def test_report_roundtrips_through_json(self):
        report = sample_report()
        assert Report.model_validate_json(report.model_dump_json()) == report

    def test_extra_fields_are_rejected(self):
        payload = sample_report().model_dump()
        payload["extra"] = True
        with pytest.raises(ValidationError):
            Report.model_validate(payload)


class TestRepairedQuoteMarks:
    def test_a_mark_on_an_entry_the_block_carries_is_accepted(self):
        report = sample_report(
            [sample_threat("S-01")],
            repaired_quotes=[
                RepairedQuote(claim_id="S-01", index=0, written="w", similarity=0.95)
            ],
        )
        assert report.analyses[0].repaired_quotes[0].written == "w"

    @pytest.mark.parametrize(
        ("claim_id", "index", "message"),
        [
            ("S-99", 0, "repaired quote names claim 'S-99'"),
            ("S-01", 9, "repaired quote names index 9"),
        ],
    )
    def test_a_mark_on_nothing_is_refused(self, claim_id, index, message):
        """The agent's words would otherwise be shown beside the wrong entry."""
        with pytest.raises(ValidationError, match=message):
            sample_report(
                [sample_threat("S-01")],
                repaired_quotes=[
                    RepairedQuote(
                        claim_id=claim_id, index=index, written="w", similarity=0.95
                    )
                ],
            )

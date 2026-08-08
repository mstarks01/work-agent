"""What the sweep can say about grounds, and what it must not silently absorb.

Three measurements ride on this module and none of the rules behind them is
mechanically enforced anywhere else, so the arithmetic is pinned here — along
with the two classifications that decide which measured rate a dead case lands
in.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from evals.harness.grounds import (
    aggregate_grounds,
    classify_failure,
    measure_grounds,
)
from stride_service.critic import DraftJoinError, GroundsUnverifiedError
from stride_service.evidence import EvidenceResolutionError
from stride_service.report import DraftThreat, Ground, UnverifiedGround
from tests.eval_factories import draft_threat

LABEL = "design-doc"


class ThreatsHolder(BaseModel):
    """A draft one level down, the depth session state hands one back at."""

    threats: list[DraftThreat]


def grounded(sequence: int, *grounds: Ground, category="spoofing") -> DraftThreat:
    """One draft carrying exactly the grounds a test cares about."""
    return draft_threat(sequence, category, "A title.").model_copy(
        update={"grounds": list(grounds)}
    )


def quote(text: str = "a quoted span") -> Ground:
    return Ground(kind="quote", text=text, source_label=LABEL)


def unknown(attribute: str = "authentication") -> Ground:
    return Ground(
        kind="unknown-attribute", element_id="entity:shopper", attribute=attribute
    )


def derived(flow_id: str = "flow:a-to-b:login") -> Ground:
    return Ground(kind="derived-fact", flow_id=flow_id)


def draft_payload(*, grounds: list[dict]) -> dict:
    """One agent's draft as JSON, the way ``merge_drafts`` receives it."""
    return {
        "id": "S-01",
        "category": "spoofing",
        "title": "A title.",
        "description": "A description.",
        "affected_element_ids": ["entity:shopper"],
        "grounds": grounds,
        "severity": {"likelihood": "high", "impact": "high", "justification": "j"},
    }


class TestMeasureGrounds:
    def test_counts_grounds_per_threat_and_the_branch_mix(self):
        measurement = measure_grounds(
            "case-a",
            [grounded(1, quote(), unknown()), grounded(2, derived())],
            [],
        )

        assert measurement.threat_count == 2
        assert measurement.ground_count == 3
        assert measurement.grounds_per_threat == 1.5
        assert measurement.kind_counts == {
            "quote": 1,
            "unknown-attribute": 1,
            "derived-fact": 1,
        }

    def test_a_quoteless_threat_is_counted_not_faulted(self):
        """The branch rule predicts these: an unknown or a crossing was the
        trigger, so there was never a span to quote."""
        measurement = measure_grounds(
            "case-a", [grounded(1, unknown()), grounded(2, quote())], []
        )

        assert measurement.quoteless_count == 1
        assert measurement.quoteless_rate == 0.5

    def test_the_unverified_rate_is_denominated_in_quotes(self):
        """Not in grounds — a rate that fell whenever the agents cited more
        unknowns would be measuring the branch mix, not the discipline."""
        measurement = measure_grounds(
            "case-a",
            [grounded(1, quote("bad"), unknown()), grounded(2, quote())],
            [UnverifiedGround(threat_id="S-01", index=0, reason="not found")],
        )

        assert measurement.quote_count == 2
        assert measurement.unverified_count == 1
        assert measurement.unverified_rate == 0.5

    def test_marks_are_attributed_to_the_threat_that_carries_them(self):
        measurement = measure_grounds(
            "case-a",
            [grounded(1, quote("bad"), quote("worse")), grounded(2, quote())],
            [
                UnverifiedGround(threat_id="S-01", index=0, reason="not found"),
                UnverifiedGround(threat_id="S-01", index=1, reason="not found"),
            ],
        )

        by_id = {entry.threat_id: entry for entry in measurement.threats}
        assert by_id["S-01"].unverified == (0, 1)
        assert by_id["S-02"].unverified == ()

    def test_an_empty_case_reports_zeros_rather_than_dividing_by_none(self):
        measurement = measure_grounds("case-a", [], [])

        assert measurement.grounds_per_threat == 0.0
        assert measurement.unverified_rate == 0.0
        assert measurement.to_json()["counts"]["threats"] == 0


class TestClassifyFailure:
    def test_a_threat_that_lost_every_ground_is_fail_closed(self):
        error = GroundsUnverifiedError(
            "threat 'S-01' has no ground that verifies",
            threat_ids=["S-01"],
            draft_count=4,
        )

        failure = classify_failure("case-a", error)

        assert failure.kind == "fail-closed"
        assert failure.threat_ids == ("S-01",)
        assert failure.fail_closed_rate == 0.25

    def test_a_mis_shaped_ground_is_told_apart_by_its_error_location(self):
        """``Ground._check_shape`` raising sits at ``("grounds", i)`` — the
        signature the harness matches on, rather than the message prose.

        Validated from a raw dict, which is the shape ``merge_drafts`` really
        hands :meth:`DraftThreat.model_validate` when an agent emits one.
        """
        with pytest.raises(ValidationError) as excinfo:
            DraftThreat.model_validate(
                draft_payload(
                    grounds=[
                        {
                            "kind": "quote",
                            "text": "t",
                            "source_label": LABEL,
                            "flow_id": "f",
                        }
                    ]
                )
            )

        assert classify_failure("case-a", excinfo.value).kind == "mis-shape"

    def test_a_mis_shape_is_recognised_at_any_depth_a_draft_is_revalidated_at(self):
        """Matched on the ``loc`` tail, so the depth does not have to be known.

        A draft is revalidated wherever it is read back out of session state,
        which nests the same fault under a different path. Matching a fixed
        path would report this tripwire as zero from every site but one."""
        with pytest.raises(ValidationError) as excinfo:
            ThreatsHolder.model_validate(
                {
                    "threats": [
                        draft_payload(
                            grounds=[
                                {"kind": "derived-fact", "flow_id": "f", "text": "t"}
                            ]
                        )
                    ]
                }
            )

        assert excinfo.value.errors()[0]["loc"] == ("threats", 0, "grounds", 0)
        assert classify_failure("case-a", excinfo.value).kind == "mis-shape"

    def test_an_invented_evidence_reference_is_its_own_kind(self):
        """The one way an agent's evidence selection can fail.

        It is a ``DraftJoinError`` subclass, so it would otherwise pool into
        ``other`` beside dangling element IDs and duplicate threat IDs — and
        the number worth watching after the cutover is exactly this one."""
        failure = classify_failure(
            "case-a",
            EvidenceResolutionError(
                "threat 'S-01' cites evidence 'crossing:flow:not-real'"
            ),
        )

        assert failure.kind == "unresolved-evidence"
        assert "crossing:flow:not-real" in failure.detail

    def test_a_ground_missing_its_branch_fields_is_also_a_mis_shape(self):
        """The other half of ``_check_shape``: a branch that carries none of
        what it requires."""
        with pytest.raises(ValidationError) as excinfo:
            DraftThreat.model_validate(draft_payload(grounds=[{"kind": "quote"}]))

        assert classify_failure("case-a", excinfo.value).kind == "mis-shape"

    def test_an_empty_grounds_list_is_not_a_mis_shape(self):
        """A different defect, and one the flat-``Ground`` decision did not
        cause: the agent wrote no grounds at all rather than a nonsense
        combination. Its error sits on ``grounds`` itself, not on an entry."""
        with pytest.raises(ValidationError) as excinfo:
            DraftThreat.model_validate(draft_payload(grounds=[]))

        assert classify_failure("case-a", excinfo.value).kind == "other"

    def test_an_unrelated_join_failure_stays_out_of_both_rates(self):
        failure = classify_failure(
            "case-a", DraftJoinError("draft 'S-01' cites 'process:invented'")
        )

        assert failure.kind == "other"
        assert failure.threat_ids == ()


class TestAggregate:
    def test_pools_rather_than_averaging_over_cases(self):
        """A case that drafted one threat must not outweigh one that drafted
        three, which is what a mean of per-case rates would do."""
        small = measure_grounds("small", [grounded(1, quote("bad"))], [])
        large = measure_grounds(
            "large",
            [grounded(1, quote()), grounded(2, quote()), grounded(3, quote())],
            [],
        )
        marked = measure_grounds(
            "small",
            [grounded(1, quote("bad"))],
            [UnverifiedGround(threat_id="S-01", index=0, reason="not found")],
        )

        totals = aggregate_grounds([marked, large], [])

        assert totals["threats"] == 4
        assert totals["quote"] == 4
        assert totals["unverified_rate"] == 0.25
        assert small.unverified_rate == 0.0

    def test_failed_cases_are_counted_by_kind_never_folded_into_a_rate(self):
        """A dead case contributed no denominator, so pooling it into a
        per-threat rate would divide by a population the run does not have."""
        failures = [
            classify_failure(
                "case-a",
                GroundsUnverifiedError("x", threat_ids=["S-01", "T-02"], draft_count=9),
            ),
            classify_failure("case-b", DraftJoinError("unrelated")),
        ]

        totals = aggregate_grounds([], failures)

        assert totals["failed_cases"] == 2
        assert totals["fail_closed_cases"] == 1
        assert totals["other_failed_cases"] == 1
        assert totals["mis_shape_cases"] == 0
        assert totals["fail_closed_threats"] == 2
        assert totals["threats"] == 0

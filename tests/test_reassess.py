"""Recomputing a report's assertion layer after review (#926, option (iii)).

The reviewed guarantee: no reviewer-rejected fact reaches analysis. Each test
reviews a report built by the shared factory and reads what the recomputation
did to the model, the evidence and the findings.
"""

from __future__ import annotations

from typing import get_args

import pytest

from analysis_service.assertions import (
    ABSENT,
    PROJECTS_UNDER,
    Assertion,
    AssertionCatalog,
    AssertionRecord,
    Assessment,
    Subject,
    assertion_id,
)
from analysis_service.claims import Ground
from analysis_service.reassess import ReviewError, Verdict, reassess, reviewed
from tests.factories import sample_report, sample_threat

SHOPPERS = Subject(
    id="principal:shopper-accounts", type="principal", label="shopper accounts"
)
LOGIN = "flow:entity:customer>process:web-app>login"

ABSENCE = Assertion(
    subject=SHOPPERS.id,
    predicate="mfa-requirement",
    value=ABSENT,
    basis="inferred",
    explanation="the description names password login only",
)
MECHANISM = Assertion(
    subject=LOGIN,
    predicate="authentication-mechanism",
    value="a hardware token",
    basis="inferred",
    explanation="fixture",
)
REVIEWER = "human:reviewer-1"


def report(*, cite: bool = True):
    record = AssertionRecord(
        proposed=2,
        catalog=AssertionCatalog(
            subjects=[SHOPPERS, Subject(id=LOGIN, type="interaction", label="login")],
            entries=[ABSENCE, MECHANISM],
        ),
    )
    threats = (
        [
            sample_threat(
                grounds=[Ground(kind="assertion", assertion=assertion_id(ABSENCE))]
            )
        ]
        if cite
        else None
    )
    return sample_report(threats=threats, assertions=record)


def reject(row: Assertion, assessment: Assessment = "unsupported") -> dict:
    return {assertion_id(row): Verdict(assessment, REVIEWER)}


def test_a_rejected_row_leaves_the_evidence() -> None:
    result = reassess(report(), reject(ABSENCE))

    assert assertion_id(ABSENCE) not in result.evidence
    assert result.rejected == (assertion_id(ABSENCE),)


def test_a_finding_resting_on_a_rejected_row_is_withdrawn() -> None:
    result = reassess(report(), reject(ABSENCE))

    (withdrawn,) = result.withdrawn
    assert withdrawn.framework == "stride"
    assert withdrawn.reasons


@pytest.mark.parametrize("assessment", ["unsupported", "unresolved"])
def test_a_rejected_projection_reopens_its_lead(assessment) -> None:
    """The login's authentication is a stated control in the report; the
    rejected mechanism is set aside, and the attribute becomes a question the
    re-analysis has to look at."""
    result = reassess(report(cite=False), reject(MECHANISM, assessment))
    flow = next(flow for flow in result.model.data_flows if flow.id == LOGIN)

    assert flow.authentication.startswith("unknown; a reviewer set")
    assert MECHANISM.value not in flow.authentication
    assert assertion_id(MECHANISM) not in result.evidence
    assert f"{LOGIN}.authentication" in result.reopened


def test_a_supported_row_may_close_a_lead() -> None:
    """What a review is for: a checked mechanism becomes the control."""
    verdicts = {assertion_id(MECHANISM): Verdict("supported", REVIEWER)}
    result = reassess(report(cite=False), verdicts)
    flow = next(flow for flow in result.model.data_flows if flow.id == LOGIN)

    assert flow.authentication == "a hardware token"
    assert result.withdrawn == ()


def test_a_finding_on_a_row_the_review_supports_stands() -> None:
    """The supported mechanism moves into the attribute and out of the
    evidence; the finding that cited it still rests on a fact."""
    threat = sample_threat(
        grounds=[Ground(kind="assertion", assertion=assertion_id(MECHANISM))]
    )
    cited = sample_report(threats=[threat], assertions=report().assertions)
    verdicts = {assertion_id(MECHANISM): Verdict("supported", REVIEWER)}
    result = reassess(cited, verdicts)

    assert assertion_id(MECHANISM) not in result.evidence
    assert result.withdrawn == ()


def test_a_review_that_rejects_nothing_withdraws_nothing() -> None:
    verdicts = {assertion_id(ABSENCE): Verdict("supported", REVIEWER)}
    result = reassess(report(), verdicts)

    assert result.withdrawn == () and result.rejected == ()


@pytest.mark.parametrize(
    ("verdicts", "match"),
    [
        ({"assertion:nothing": Verdict("unsupported", REVIEWER)}, "does not hold"),
        ({assertion_id(ABSENCE): Verdict("unchecked", REVIEWER)}, "not a verdict"),
        ({assertion_id(ABSENCE): Verdict("unsupported", " ")}, "names who"),
    ],
)
def test_a_verdict_that_cannot_be_applied_is_refused(verdicts, match) -> None:
    with pytest.raises(ReviewError, match=match):
        reviewed(report().assertions.catalog, verdicts)


def test_a_report_with_no_assertion_pass_has_nothing_to_review() -> None:
    with pytest.raises(ReviewError, match="no assertion pass"):
        reassess(sample_report(), {})


def test_the_report_itself_is_never_rewritten() -> None:
    original = report()
    before = original.model_dump(mode="json")
    reassess(original, reject(ABSENCE))

    assert original.model_dump(mode="json") == before


class TestTheReportDisclosesReview:
    """Option (ii)'s disclosure, kept beside the guarantees (never instead)."""

    def test_a_job_with_no_assertion_pass_says_what_it_always_said(self) -> None:
        from analysis_service.report import DEFAULT_DISCLAIMER, disclaimer_for

        assert disclaimer_for(None) == DEFAULT_DISCLAIMER

    @pytest.mark.parametrize("assessment", get_args(Assessment))
    def test_the_disclosure_rejects_what_the_projection_refuses(
        self, assessment
    ) -> None:
        """One rule: a row the projection refuses is a row the report calls
        rejected, read off ``PROJECTS_UNDER`` rather than a second list."""
        from analysis_service.report import disclaimer_for

        row = ABSENCE.model_copy(
            update={"assessment": assessment, "assessor": REVIEWER}
        )
        catalog = AssertionCatalog(subjects=[SHOPPERS], entries=[row])
        text = disclaimer_for(AssertionRecord(proposed=1, catalog=catalog))
        rejected = 0 if PROJECTS_UNDER[assessment] else 1

        assert f"{rejected} rejected" in text

    def test_the_disclosure_counts_each_review_state(self) -> None:
        from analysis_service.report import disclaimer_for

        catalog = reviewed(report().assertions.catalog, reject(ABSENCE) | {})
        text = disclaimer_for(AssertionRecord(proposed=2, catalog=catalog))

        assert "0 checked by a reviewer, 1 rejected" in text
        assert "1 unchecked" in text
        assert "never closes an open question" in text

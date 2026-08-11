"""Coverage accounting over the drafts the critic was handed."""

import pytest

from stride_service.candidates import generate_candidates
from stride_service.coverage import build_coverage, cited_element_ids, lane_scope
from stride_service.report import STRIDE_CATEGORIES
from tests.factories import sample_draft, valid_model


@pytest.fixture
def model():
    return valid_model()


@pytest.fixture
def candidates(model):
    return generate_candidates(model)


def coverage_for(category, rows):
    return next(row for row in rows if row.category == category)


def test_every_lane_gets_a_row_even_with_no_drafts(model, candidates):
    rows = build_coverage({}, candidates, model)
    assert [row.category for row in rows] == list(STRIDE_CATEGORIES)
    assert all(row.drafts == 0 for row in rows)
    assert all(row.elements == len(model.elements()) for row in rows)


def test_rules_counts_the_lane_not_the_firings(model, candidates):
    rows = build_coverage({}, candidates, model)
    for row in rows:
        assert row.rules >= row.rules_fired
        assert row.rules_fired <= row.candidates


def test_a_draft_citing_nothing_relevant_scores_zero_candidates(model, candidates):
    draft = sample_draft(category="spoofing", affected_element_ids=["entity:customer"])
    rows = build_coverage({"spoofing": [draft]}, candidates, model)
    row = coverage_for("spoofing", rows)
    assert row.drafts == 1
    assert row.candidates_cited == 0


def test_a_candidate_counts_as_cited_only_when_every_element_is(model, candidates):
    lane = "information-disclosure"
    lead = next(
        candidate
        for candidate in candidates[lane].candidates
        if len(candidate.element_ids) > 1
    )
    partial = sample_draft(
        "I-01", category=lane, affected_element_ids=[lead.element_ids[0]]
    )
    whole = sample_draft(
        "I-01", category=lane, affected_element_ids=list(lead.element_ids)
    )
    partial_row = coverage_for(
        lane, build_coverage({lane: [partial]}, candidates, model)
    )
    whole_row = coverage_for(lane, build_coverage({lane: [whole]}, candidates, model))
    assert partial_row.candidates_cited == 0
    assert whole_row.candidates_cited > 0


def test_prose_citations_count_as_much_as_the_structured_field():
    """An ID named only in the description was still examined."""
    draft = sample_draft(
        "T-01",
        category="tampering",
        affected_element_ids=["process:web-app"],
        description="reached through `store:orders-db` from `process:web-app`",
    )
    assert cited_element_ids([draft]) == {"process:web-app", "store:orders-db"}


def test_crossings_and_unknown_controls_are_counted_against_citations(
    model, candidates
):
    draft = sample_draft(
        "T-01",
        category="tampering",
        affected_element_ids=["flow:customer-to-web-app:login", "store:orders-db"],
    )
    row = coverage_for(
        "tampering", build_coverage({"tampering": [draft]}, candidates, model)
    )
    assert row.boundary_crossings == 1
    assert row.boundary_crossings_cited == 1
    assert row.unknown_controls_cited >= 1


def test_coverage_is_stable_across_calls(model, candidates):
    draft = sample_draft(category="spoofing")
    first = build_coverage({"spoofing": [draft]}, candidates, model)
    second = build_coverage({"spoofing": [draft]}, candidates, model)
    assert [row.model_dump() for row in first] == [row.model_dump() for row in second]


class TestLaneScope:
    """The denominators an agent is told before it starts.

    Coverage answers "did the lane look at everything" for a *reader*. This
    answers it for the agent, which is the half that can still change an
    outcome: a lane that never learns the system has seventeen elements cannot
    distinguish having cleared them from having missed them.
    """

    def test_it_reports_the_models_denominators(self, model, candidates):
        scope = lane_scope("spoofing", model, candidates["spoofing"])

        assert f"{len(model.elements())} elements" in scope
        assert f"{len(model.boundary_crossings())} boundary crossings" in scope

    def test_it_agrees_with_the_coverage_row_the_report_publishes(
        self, model, candidates
    ):
        """One derivation, so the instruction and the report cannot disagree.

        Two computations of "how many elements" would be two claims rather than
        one fact, and the one the agent read is the one nobody could check.
        """
        scope = lane_scope("spoofing", model, candidates["spoofing"])
        row = coverage_for("spoofing", build_coverage({}, candidates, model))

        assert f"{row.elements} elements" in scope
        assert f"{row.boundary_crossings} boundary crossings" in scope
        assert f"{row.unknown_controls} unstated controls" in scope
        assert f"{row.rules} spoofing rules" in scope
        assert f"{row.rules_fired} fired" in scope
        assert f"{row.candidates} candidates" in scope

    def test_a_lane_that_fired_nothing_still_gets_its_denominators(self, model):
        """The case the line exists for.

        A lane with no leads is exactly the one at risk of filing nothing
        because it never looked, so it is the last lane that should be told
        nothing about the system's size.
        """
        scope = lane_scope("repudiation", model, None)

        assert f"{len(model.elements())} elements" in scope
        assert "0 fired" in scope

    def test_it_names_the_lanes_own_category(self, model, candidates):
        assert "denial-of-service rules" in lane_scope(
            "denial-of-service", model, candidates["denial-of-service"]
        )

    def test_it_is_stable_across_calls(self, model, candidates):
        """Two runs over one model must send byte-identical instructions."""
        first = lane_scope("tampering", model, candidates["tampering"])
        second = lane_scope("tampering", model, candidates["tampering"])

        assert first == second

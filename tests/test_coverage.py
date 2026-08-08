"""Coverage accounting over the drafts the critic was handed."""

import pytest

from stride_service.candidates import generate_candidates
from stride_service.coverage import build_coverage, cited_element_ids
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

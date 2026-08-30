"""The reading view behind one applicability disagreement (#447).

A pairing answers nothing. These pin the two properties that make it safe to
read: it reports the score's own cells rather than a second opinion, and it
refuses to write ASVS text into this Apache-2.0 tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from analysis_service.frameworks.asvs.catalog import requirements_for
from evals import verify_corpus
from evals.harness.applicability import declared_level, score_applicability
from evals.harness.pairing import (
    ATTRIBUTION,
    REPO_ROOT,
    PairingError,
    pair_case,
    refuse_path_inside_repo,
    render_html,
)
from evals.harness.reference import load_corpus
from tests.test_evals_applicability import CASE_ID, Block, Scoped, ruling


@pytest.fixture(scope="module")
def case():
    corpus = load_corpus(verify_corpus.CORPUS_DIR)
    return next(entry for entry in corpus if entry.id == CASE_ID)


@pytest.fixture
def expected(case):
    return [reference.requirement for reference in case.references["asvs"]]


def test_it_reports_the_score_s_own_cells(case, expected):
    """A pairing that disagreed with the number would be worse than none."""
    universe = {entry.id for entry in requirements_for(declared_level(case))}
    extra = sorted(universe - set(expected))[:3]
    block = Block(ruling(r) for r in [*expected, *extra])

    score = score_applicability(case, block)
    pairing = pair_case(case, block, score)

    assert {pair.requirement for pair in pairing.over_applied} == set(
        score.over_applied
    )
    assert {pair.requirement for pair in pairing.agreed_pairs} == set(score.matched)
    assert pairing.expected == len(score.expected)


def test_a_requirement_answered_only_by_a_deferral_is_kept_apart(case, expected):
    """It counts as applied (#456) and it is not a ruling, which is the question."""
    deferred, ruled = expected[0], expected[1:]
    block = Block((ruling(r) for r in ruled), scope=[Scoped(deferred)])

    pairing = pair_case(case, block)

    assert [pair.requirement for pair in pairing.deferred_pairs] == [deferred]
    assert deferred not in {pair.requirement for pair in pairing.agreed_pairs}
    assert pairing.missed_pairs == ()


def test_a_missed_requirement_carries_the_case_s_own_expectation(case, expected):
    block = Block(ruling(r) for r in expected[1:])

    pairing = pair_case(case, block)

    (missed,) = [
        pair for pair in pairing.missed_pairs if pair.requirement == expected[0]
    ]
    assert missed.expectation
    assert missed.deferral == ""


def test_the_page_carries_the_upstream_licence(case, expected):
    """The page reproduces ASVS sentences, so it carries ASVS's terms."""
    page = render_html(pair_case(case, Block(ruling(r) for r in expected)), "artifact")

    assert ATTRIBUTION in page
    assert "CC BY-SA 4.0" in page


def test_a_claim_s_prose_reaches_the_page_escaped(case, expected):
    """Model output is untrusted input, and this page renders it (OWASP LLM05)."""
    block = Block([ruling(expected[0], description="<script>alert(1)</script>")])

    page = render_html(pair_case(case, block), "artifact")

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_writing_the_page_into_this_repository_is_refused():
    """ASVS text is CC BY-SA and this tree is Apache-2.0."""
    with pytest.raises(PairingError, match="CC BY-SA"):
        refuse_path_inside_repo(REPO_ROOT / "evals" / "pairing.html")


def test_writing_it_outside_is_allowed(tmp_path):
    assert (
        refuse_path_inside_repo(tmp_path / "pairing.html")
        == (tmp_path / "pairing.html").resolve()
    )


def test_the_source_artifact_is_named_on_the_page(case, expected):
    """A reader has to be able to tell which run they are ruling on."""
    page = render_html(
        pair_case(case, Block(ruling(r) for r in expected)), "runs/some-sweep.json"
    )

    assert "runs/some-sweep.json" in page


def test_it_lives_outside_the_repo_root_check_only_by_resolution(tmp_path):
    """A relative path that climbs back into the tree is still refused."""
    inside = Path(REPO_ROOT / "evals" / ".." / "evals" / "x.html")
    with pytest.raises(PairingError):
        refuse_path_inside_repo(inside)

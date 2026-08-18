"""ASVS's scorer, and the sweep wiring that finally reaches it.

Before #200 the corpus held 63 ASVS reference records and nothing read them:
``EVAL_FRAMEWORKS`` built every sweep's graph for STRIDE alone, so ASVS's lanes
never ran and its block never existed. These tests hold the two halves of the
fix — the confusion matrix itself, and the wiring that decides which frameworks
a case's graph is built for.

Everything here is free of provider calls, because the scorer is a set
comparison: ASVS matches by requirement ID, so #167 removed the judge from this
half of the contract entirely.
"""

from __future__ import annotations

import pytest

from evals import verify_corpus
from evals.harness.applicability import (
    APPLIES,
    ApplicabilityError,
    applied_requirements,
    declared_level,
    pooled,
    score_applicability,
)
from evals.harness.modes import case_frameworks
from evals.harness.reference import load_corpus
from stride_service.frameworks.asvs.catalog import requirements_for
from stride_service.frameworks.asvs.record import RequirementRuling
from stride_service.report import Ground, UnknownRef, Verdict, VerdictStatus

CASE_ID = "01-payments-checkout"


@pytest.fixture(scope="module")
def corpus():
    return load_corpus(verify_corpus.CORPUS_DIR)


@pytest.fixture(scope="module")
def case(corpus):
    return next(entry for entry in corpus if entry.id == CASE_ID)


def ruling(requirement: str, status: VerdictStatus = "confirmed") -> RequirementRuling:
    """One ruled claim carrying the standard's identifier, as the graph builds it.

    ``requirement`` is spelled the way the report does — ``V1.2.4`` becomes the
    composed ID ``v5.0.0-1.2.4`` — so the scorer is exercised through
    ``requirement_of`` rather than against a shape nothing produces.
    """
    return RequirementRuling(
        id=f"v5.0.0-{requirement[1:]}",
        framework="asvs",
        framework_version="5.0.0",
        chapter="authentication",
        title="t",
        description="d",
        affected_element_ids=[],
        grounds=[Ground(kind="unknown-attribute", element_id="x", attribute="y")],
        verdict=Verdict(
            status=status,
            reason="" if status == "confirmed" else "the input does not settle it",
            # The review seam binds the fields to the status: needs-info must
            # name the unknown it is waiting on, exactly as production builds it.
            related_unknowns=(
                [
                    UnknownRef(
                        element_id="process:storefront-api", attribute="authentication"
                    )
                ]
                if status == "needs-info"
                else []
            ),
        ),
    )


class Block:
    """The one field the scorer reads off a block."""

    def __init__(self, claims):
        self.claims = list(claims)


def test_a_perfect_run_matches_every_expected_requirement(case):
    expected = [reference.requirement for reference in case.references["asvs"]]

    score = score_applicability(case, Block(ruling(r) for r in expected))

    assert set(score.matched) == set(expected)
    assert score.missed == ()
    assert score.over_applied == ()
    assert score.off_catalog == ()
    assert score.recall == 1.0
    assert score.precision == 1.0


def test_the_four_cells_partition_the_level_s_universe(case):
    """Every requirement the level rules on lands in exactly one cell."""
    expected = [reference.requirement for reference in case.references["asvs"]]
    universe = {entry.id for entry in requirements_for(declared_level(case))}
    extra = min(universe - set(expected))

    score = score_applicability(case, Block(ruling(r) for r in [*expected[:2], extra]))

    counted = (
        len(score.matched)
        + len(score.missed)
        + len(score.over_applied)
        + score.excluded
    )
    assert counted == score.universe == len(universe)


def test_a_rejected_ruling_is_not_an_applied_one(case):
    """``rejected`` is the critic saying the requirement does not apply.

    It is the negative answer rather than a missing one, so it neither matches a
    reference nor counts against precision — and it is reported, because a run
    that rejected everything is a different fact from one that produced nothing.
    """
    expected = [reference.requirement for reference in case.references["asvs"]]

    score = score_applicability(
        case, Block(ruling(r, status="rejected") for r in expected)
    )

    assert score.matched == ()
    assert set(score.missed) == set(expected)
    assert set(score.rejected) == set(expected)
    assert score.precision == 0.0


def test_needs_info_asserts_the_requirement_applies():
    """The verdict split this scorer turns on, pinned.

    ASVS never reports a pass, so ``confirmed`` and ``needs-info`` both say the
    requirement applies and only ``rejected`` says it does not. A scorer that
    read ``confirmed`` alone would count every open question as a miss.
    """
    applied, rejected = applied_requirements(
        [ruling("V1.2.4", "confirmed"), ruling("V2.1.1", "needs-info")]
    )

    assert applied == {"V1.2.4", "V2.1.1"}
    assert not rejected
    assert APPLIES == {"confirmed", "needs-info"}


def test_a_requirement_outside_the_level_is_its_own_cell(case):
    """Off-catalog is a package bug; over-applied is a judgement to argue with.

    Folding them together would hide the first behind the second — a composed
    identifier the catalog does not hold at this level is the package spelling
    something wrong, not an opinion this corpus disagrees with.
    """
    level = declared_level(case)
    beyond = next(
        entry.id
        for entry in requirements_for(3)
        if entry.id not in {inside.id for inside in requirements_for(level)}
    )

    score = score_applicability(case, Block([ruling(beyond)]))

    assert score.off_catalog == (beyond,)
    assert score.over_applied == ()
    # It still costs precision: a reader has to read the claim either way.
    assert score.precision == 0.0


def test_a_malformed_claim_id_is_not_charged_as_a_miss():
    """The block's own checks report a malformed ID; this metric is not about it."""
    claim = ruling("V1.2.4")
    applied, rejected = applied_requirements([claim.model_copy(update={"id": "junk"})])

    assert not applied and not rejected


def test_a_case_declaring_no_level_cannot_be_scored(corpus):
    stride_only = next(
        entry
        for entry in corpus
        if "asvs" not in {d.name for d in entry.meta.frameworks}
    )

    with pytest.raises(ApplicabilityError, match="does not declare asvs"):
        declared_level(stride_only)


def test_pooling_weights_by_record_and_not_by_case(case):
    """A small case's miss must not vanish behind a large case's success."""
    expected = [reference.requirement for reference in case.references["asvs"]]
    perfect = score_applicability(case, Block(ruling(r) for r in expected))
    nothing = score_applicability(case, Block([]))

    totals = pooled([perfect, nothing])

    assert totals["expected"] == 2 * len(expected)
    assert totals["matched"] == len(expected)
    assert totals["recall"] == 0.5


def test_a_case_s_graph_is_built_for_the_frameworks_it_declares(corpus):
    """The wiring #200 turned on: the sweep runs each case's own declaration.

    Seven cases declare ASVS and six do not. Building one graph for the sweep is
    what left 63 records unread; building the declaration is also what stops a
    STRIDE-only case paying for ASVS's 17 ``strong``-tier lanes.
    """
    built = {entry.id: case_frameworks(entry) for entry in corpus}

    assert sorted(built[CASE_ID]) == ["asvs", "stride"]
    with_asvs = [case_id for case_id, names in built.items() if "asvs" in names]
    assert len(with_asvs) == 7
    assert all("stride" in names for names in built.values())

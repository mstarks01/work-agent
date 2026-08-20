"""ASVS's scorer, and the sweep wiring that finally reaches it.

Before #200 the corpus held 63 ASVS reference records and nothing read them:
``EVAL_FRAMEWORKS`` built every sweep's graph for STRIDE alone, so ASVS's lanes
never ran and its block never existed. These tests hold the two halves of the
fix — the confusion matrix itself, and the wiring that decides which frameworks
a case's graph is built for.

Everything here is free of provider calls, because the scorer is a set
comparison: ASVS matches by requirement ID, so #167 removed claim equivalence from this
half of the contract entirely.
"""

from __future__ import annotations

from pathlib import Path

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
from stride_service.frameworks import PACKAGES
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
    assert len(with_asvs) == 11
    assert all("stride" in names for names in built.values())


def test_grounds_reads_a_lane_without_naming_any_framework_s_field():
    """The lane comes from the package's declaration, not from a field name.

    STRIDE stamps its lane into ``category`` and ASVS into ``chapter``. A fold
    that spelled either would be a second definition to drift, so
    :func:`~evals.harness.grounds.lane_of` reads ``IdRule.lane_field`` — which is
    the same declaration the neutral resolver stamps through.
    """
    from evals.harness.grounds import lane_of

    claim = ruling("V6.2.1")

    assert lane_of("asvs", claim) == "authentication"
    assert PACKAGES["asvs"].id_rule.lane_field == "chapter"
    assert PACKAGES["stride"].id_rule.lane_field == "category"


def test_coverage_reports_every_lane_of_every_framework_that_ran():
    """The row count a two-framework sweep owes, against a one-framework sweep.

    Before #213 the sweep collected ``stride_block(report).coverage`` and ASVS's
    17 lanes were computed, carried on the report and dropped. The framework list
    is what was *built*, so a package whose every lane went silent still gets its
    rows — which is the finding the table exists to show.
    """
    from evals.harness.coverage import aggregate_coverage

    both = aggregate_coverage([], ["stride", "asvs"])

    assert len(both) == len(PACKAGES["stride"].lanes) + len(PACKAGES["asvs"].lanes)
    assert len([lane for lane in both if lane.framework == "asvs"]) == 17
    assert all(lane.cases == 0 for lane in both)


def test_the_exemplar_delta_reads_this_package_s_own_proximity(case, corpus):
    """`exemplar_proximity` sits on the `(case, framework)` pair, not the case.

    Exemplars live at `frameworks/<name>/lanes/<lane>/exemplars.md`, so a case
    near STRIDE's payments exemplar is near nothing of ASVS's. A delta that read
    one declaration for both packages would answer the wrong question for one of
    them.
    """
    from evals.harness.applicability import exemplar_delta

    expected = [reference.requirement for reference in case.references["asvs"]]
    near = score_applicability(case, Block(ruling(r) for r in expected))
    far = score_applicability(case, Block([]))
    delta = exemplar_delta(
        [near, far.__class__(**{**far.__dict__, "exemplar_proximity": "far"})]
    )

    assert delta["near_recall"] == 1.0
    assert delta["far_recall"] == 0.0
    assert delta["delta"] == 1.0


def test_over_applied_requirements_are_surfaced_for_the_next_reading(case):
    """The corpus feedback loop, by catalog arithmetic.

    STRIDE needs one to tell a grounded unlisted threat from noise. Here the
    list is set arithmetic, and `off_catalog` has already taken out the entry
    that is a package bug rather than a judgement — so it never appears.
    """
    from evals.harness.applicability import over_applied_for_promotion

    universe = {entry.id for entry in requirements_for(declared_level(case))}
    unexpected = min(
        universe - {reference.requirement for reference in case.references["asvs"]}
    )
    beyond = next(entry.id for entry in requirements_for(3) if entry.id not in universe)

    score = score_applicability(case, Block([ruling(unexpected), ruling(beyond)]))
    surfaced = over_applied_for_promotion([score])

    assert [entry["requirement"] for entry in surfaced] == [unexpected]
    assert score.off_catalog == (beyond,)


def test_stability_reads_the_applicability_block_too():
    """ASVS run-to-run spread was unmeasured; #200's body said otherwise.

    ASVS matches by requirement ID with no model call, so a sweep of
    ASVS-only cases still carries a comparable half where STRIDE's is absent.
    """
    from evals.harness.artifact import DECLARED_KEYS, EvalArtifact
    from evals.harness.provenance import RunProvenance
    from evals.harness.stability import read_run

    artifact = EvalArtifact(
        path=Path("sweep.json"),
        mode="analysis",
        cases=("01-payments-checkout",),
        trusted=True,
        structural_failures=(),
        provenance=RunProvenance(
            sampling_config_version=1,
            tiers_config_version=1,
            sampling={},
            node_runs={},
        ),
        raw={
            # Every declared key, as a sweep writes them. An instrument that
            # measured nothing writes an empty block rather than dropping one.
            **dict.fromkeys(DECLARED_KEYS),
            "models": {},
            # Empty, as a sweep of ASVS-only cases leaves it.
            "scores": [],
            "applicability": [
                {
                    "case": "01-payments-checkout",
                    "matched": ["V1.2.4", "V6.2.1"],
                    "expected": 4,
                    "recall": 0.5,
                }
            ],
        },
    )

    run = read_run(artifact)

    assert run.cases == {("asvs", "01-payments-checkout")}
    assert run.matched[("asvs", "01-payments-checkout")] == {"V1.2.4", "V6.2.1"}
    assert run.recall[("asvs", "01-payments-checkout")] == 0.5


class RuledBlock:
    """A block with both sides of a critic decision, as the graph builds one."""

    def __init__(self, claims, rejected_claims=()):
        self.claims = list(claims)
        self.rejected_claims = list(rejected_claims)


def test_the_critic_pair_reads_both_sides(case):
    """A rejection count alone reads as the critic working or breaking things.

    So the instrument reports `earned` and `destroyed` together, the way
    `critic_yield` reports `unsupported_killed` beside `matched_killed`.
    """
    from evals.harness.applicability import score_yield

    expected = [reference.requirement for reference in case.references["asvs"]]
    universe = {entry.id for entry in requirements_for(declared_level(case))}
    unexpected = min(universe - set(expected))

    critic = score_yield(
        case,
        RuledBlock(
            claims=[ruling(expected[0])],
            # One the corpus expects — destroyed — and one it does not — earned.
            rejected_claims=[
                ruling(expected[1], "rejected"),
                ruling(unexpected, "rejected"),
            ],
        ),
        drafts=[object(), object(), object()],
    )

    assert critic.destroyed == (expected[1],)
    assert critic.earned == (unexpected,)
    assert critic.confirmed == (expected[0],)
    assert critic.rejection_rate == pytest.approx(2 / 3)
    assert critic.destroyed_rate == 0.5


def test_the_split_comes_from_the_list_a_claim_sits_in(case):
    """Not from its verdict, which would be a second definition of one split.

    The two lists *are* the critic's decision. Re-deriving it from the verdict
    would let a block whose lists and verdicts disagree read as consistent.
    """
    from evals.harness.applicability import score_yield

    expected = [reference.requirement for reference in case.references["asvs"]]
    critic = score_yield(
        case,
        # A confirmed verdict sitting in the rejected list: malformed, and the
        # block's own checks are what report that. This instrument follows the
        # list, so the claim counts as rejected.
        RuledBlock(claims=[], rejected_claims=[ruling(expected[0], "confirmed")]),
        drafts=[object()],
    )

    assert critic.rejected == (expected[0],)
    assert critic.confirmed == ()


def test_a_critic_that_rejected_nothing_reports_zero_rather_than_dividing(case):
    from evals.harness.applicability import aggregate_yield, score_yield

    expected = [reference.requirement for reference in case.references["asvs"]]
    critic = score_yield(
        case, RuledBlock(claims=[ruling(r) for r in expected]), drafts=[object()]
    )

    assert critic.rejected == ()
    assert critic.destroyed_rate == 0.0
    assert aggregate_yield([critic])["destroyed_rate"] == 0.0


def test_pooling_the_yield_weights_by_draft_and_not_by_case(case):
    """A case drafting three must not outweigh one drafting seventeen."""
    from evals.harness.applicability import aggregate_yield, score_yield

    expected = [reference.requirement for reference in case.references["asvs"]]
    small = score_yield(
        case,
        RuledBlock(claims=[], rejected_claims=[ruling(expected[0], "rejected")]),
        drafts=[object()],
    )
    large = score_yield(
        case, RuledBlock(claims=[ruling(r) for r in expected]), drafts=[object()] * 9
    )

    totals = aggregate_yield([small, large])

    assert totals["drafts"] == 10
    assert totals["rejected"] == 1
    assert totals["rejection_rate"] == 0.1

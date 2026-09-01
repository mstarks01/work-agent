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

from dataclasses import replace
from pathlib import Path

import pytest

from analysis_service.frameworks import PACKAGES
from analysis_service.frameworks.asvs.catalog import requirements_for
from analysis_service.frameworks.asvs.record import RequirementRuling
from analysis_service.report import Ground, UnknownRef, Verdict, VerdictStatus
from evals import verify_corpus
from evals.harness.applicability import (
    APPLIES,
    ApplicabilityError,
    Observation,
    applied_requirements,
    declared_level,
    observe,
    pooled,
    pooled_dispositions,
    satisfies,
    score_applicability,
    score_dispositions,
)
from evals.harness.modes import case_frameworks
from evals.harness.reference import load_corpus

CASE_ID = "01-payments-checkout"


@pytest.fixture(scope="module")
def corpus():
    return load_corpus(verify_corpus.CORPUS_DIR)


@pytest.fixture(scope="module")
def case(corpus):
    return next(entry for entry in corpus if entry.id == CASE_ID)


@pytest.fixture(scope="module")
def read_case(case):
    """``case`` with its ASVS reference set declared exhaustive.

    Precision is defined only there, so a test about the figure needs a case
    somebody read. No corpus case declares it yet — every one is ``sampled``
    until a **Case Sitting** clears it — which is exactly why the fixture builds
    the declaration rather than reaching for a case that carries it.
    """
    frameworks = [
        entry.model_copy(update={"reference_set": "exhaustive"})
        if entry.name == "asvs"
        else entry
        for entry in case.meta.frameworks
    ]
    return replace(case, meta=case.meta.model_copy(update={"frameworks": frameworks}))


def ruling(
    requirement: str,
    status: VerdictStatus = "confirmed",
    description: str = "d",
) -> RequirementRuling:
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
        description=description,
        affected_element_ids=[],
        grounds=[Ground(kind="unknown-attribute", element_id="x", attribute="y")],
        verdict=Verdict(
            status=status,
            reason="" if status == "confirmed" else "the input does not settle it",
            # A rejection names the check that ended it, exactly as production
            # builds it: these stand for a requirement ruled not applicable,
            # which is the critic's first step.
            rejected_because="evidence" if status == "rejected" else None,
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
    """The fields the scorer reads off a block, in production's own arrangement.

    ``scope`` matters because a missed requirement has two causes and they are
    not the same finding: a lane never raised it, or a lane raised it and the
    service withheld it for want of the right kind of evidence.

    ``rejected_claims`` is a second array rather than a status inside ``claims``
    because that is the only shape production can build: ``_verdict_placement_issues``
    fails a block that puts a rejected verdict in ``claims``. A fake that allowed
    it would let the scorer pass against a report nothing can emit.
    """

    def __init__(self, claims, scope=(), rejected_claims=()):
        self.claims = list(claims)
        self.scope = list(scope)
        self.rejected_claims = list(rejected_claims)

    def all_claims(self):
        return (*self.claims, *self.rejected_claims)


class Scoped:
    """One scope entry, as the scorer and the pairing read it.

    ``reason`` is what the service said it was waiting for. The scorer counts
    the entry and never reads the sentence; a pairing shows it, because that
    sentence is what a reader is being asked to agree or disagree with.
    """

    def __init__(
        self,
        unit,
        state="needs-other-evidence",
        reason="needs source code",
        needs="",
    ):
        self.unit = unit
        self.state = state
        self.reason = reason
        # Production binds these: a `needs-other-evidence` entry names its kind
        # and no other state may. A fake that let them drift would let the
        # routing scorer pass against an entry the report validator refuses.
        self.needs = needs


def test_a_perfect_run_matches_every_expected_requirement(read_case):
    expected = [reference.requirement for reference in read_case.references["asvs"]]

    score = score_applicability(read_case, Block(ruling(r) for r in expected))

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


def test_a_rejected_ruling_is_not_an_applied_one(read_case):
    """``rejected`` is the critic saying the requirement does not apply.

    It is the negative answer rather than a missing one, so it neither matches a
    reference nor counts against precision — and it is reported, because a run
    that rejected everything is a different fact from one that produced nothing.
    """
    expected = [reference.requirement for reference in read_case.references["asvs"]]

    score = score_applicability(
        read_case,
        Block([], rejected_claims=[ruling(r, status="rejected") for r in expected]),
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


def test_a_requirement_outside_the_level_is_its_own_cell(read_case):
    """Off-catalog is a package bug; over-applied is a judgement to argue with.

    Folding them together would hide the first behind the second — a composed
    identifier the catalog does not hold at this level is the package spelling
    something wrong, not an opinion this corpus disagrees with.
    """
    level = declared_level(read_case)
    beyond = next(
        entry.id
        for entry in requirements_for(3)
        if entry.id not in {inside.id for inside in requirements_for(level)}
    )

    score = score_applicability(read_case, Block([ruling(beyond)]))

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


class TestPrecisionNeedsAReferenceSetSomebodyRead:
    """The complement of a sample is not a set of negatives.

    ``over_applied`` divides the precision figure, and reading an unlisted
    requirement as wrong is only sound where a **Case Sitting** read the set as
    complete. ``evals/harness/scorer.py`` refuses the same inference for STRIDE:
    scoring it punishes finding real things and pushes every tuning cycle toward
    under-reporting. See #474.
    """

    @staticmethod
    def unexpected(case) -> str:
        """One requirement the level rules on that this case does not list."""
        expected = {reference.requirement for reference in case.references["asvs"]}
        universe = {entry.id for entry in requirements_for(declared_level(case))}
        return min(universe - expected)

    def test_a_sampled_set_reports_no_precision(self, case):
        score = score_applicability(case, Block([ruling(self.unexpected(case))]))

        assert score.reference_set == "sampled"
        assert score.over_applied != ()
        assert score.precision is None
        assert score.to_json()["precision"] is None

    def test_the_over_applied_list_survives_either_way(self, case, read_case):
        """The list is a reading, and the rate is a score. Only the rate goes."""
        extra = self.unexpected(case)
        sampled = score_applicability(case, Block([ruling(extra)]))
        read = score_applicability(read_case, Block([ruling(extra)]))

        assert sampled.over_applied == read.over_applied == (extra,)
        assert read.precision == 0.0

    def test_every_corpus_case_is_sampled_until_somebody_reads_one(self, corpus):
        """No case declares ``exhaustive``, so no ASVS precision is published."""
        declarations = [
            entry.reference_set
            for golden in corpus
            for entry in golden.meta.frameworks
            if entry.name == "asvs"
        ]

        assert declarations and set(declarations) == {"sampled"}

    def test_pooling_reads_only_the_cases_somebody_read(self, case, read_case):
        expected = [reference.requirement for reference in case.references["asvs"]]
        sampled = score_applicability(case, Block(ruling(r) for r in expected))
        read = score_applicability(read_case, Block([ruling(self.unexpected(case))]))

        assert pooled([sampled])["precision"] is None
        assert pooled([sampled])["precision_cases"] == 0
        # The sampled case is perfect and the read one is not. Pooling both
        # gives the read case's figure alone, never an average of the two.
        totals = pooled([sampled, read])
        assert totals["precision_cases"] == 1
        assert totals["precision"] == read.precision


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
    from evals.harness.artifact import DECLARED_KEYS, EvalArtifact, RepoCommit
    from evals.harness.provenance import RunProvenance
    from evals.harness.stability import read_run

    artifact = EvalArtifact(
        path=Path("sweep.json"),
        mode="analysis",
        cases=("01-payments-checkout",),
        trusted=True,
        structural_failures=(),
        provenance=RunProvenance(
            build={},
            sampling_config_version=1,
            tiers_config_version=1,
            sampling={},
            node_runs={},
        ),
        commit=RepoCommit(commit="0" * 40, clean=True),
        corpus_digest="0" * 64,
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


class TestADeferredRequirementApplies:
    """A ``needs-other-evidence`` scope entry says the requirement applies (#454).

    A lane raised it and the service withheld the claim for want of the right
    kind of evidence. That is a match on applicability, and a policy cost
    `CARRIED_EVIDENCE_KINDS` sets on the report; a requirement nobody raised is
    the miss. The first sweep carrying deferral withheld 45 of 57 expected
    requirements, and the scorer read all 45 as misses.
    """

    def test_a_deferred_expectation_is_matched_and_named_as_such(self, case):
        expected = [reference.requirement for reference in case.references["asvs"]]
        withheld = expected[0]

        score = score_applicability(
            case,
            Block((ruling(r) for r in expected[1:]), scope=[Scoped(withheld)]),
        )

        assert withheld in score.matched
        assert withheld not in score.missed
        assert score.matched_by_deferral == (withheld,)
        assert score.recall == 1.0

    def test_a_deferred_unit_nobody_expected_is_over_applied(self, case):
        expected = [reference.requirement for reference in case.references["asvs"]]
        universe = {
            r.id for r in requirements_for(case.declaration("asvs").options["level"])
        }
        unexpected = min(universe - set(expected))

        score = score_applicability(
            case, Block((ruling(r) for r in expected), scope=[Scoped(unexpected)])
        )

        assert unexpected in score.over_applied

    def test_a_requirement_nobody_raised_is_the_miss(self, case):
        expected = [reference.requirement for reference in case.references["asvs"]]

        score = score_applicability(case, Block(ruling(r) for r in expected[1:]))

        assert expected[0] in score.missed
        assert score.matched_by_deferral == ()

    def test_a_scope_entry_of_another_state_does_not_count(self, case):
        """Only `needs-other-evidence` is a withholding. `not-applicable` is an
        answer, and `applicable` is *considered and nothing raised*."""
        expected = [reference.requirement for reference in case.references["asvs"]]

        score = score_applicability(
            case,
            Block(
                (ruling(r) for r in expected[1:]),
                scope=[Scoped(expected[0], state="applicable")],
            ),
        )

        assert expected[0] in score.missed
        assert score.matched_by_deferral == ()


class DispositionBlock:
    """A block carrying all three surfaces the routing scorer reads.

    Both claim arrays and the scope list, because a disposition is spread across
    them: a ruling arrives as a verdict, a refusal as a rejected claim or a
    ``not-applicable`` entry, and a deferral as a ``needs-other-evidence`` entry
    naming its kind.
    """

    def __init__(self, claims=(), rejected_claims=(), scope=()):
        self.claims = list(claims)
        self.rejected_claims = list(rejected_claims)
        self.scope = list(scope)

    def all_claims(self):
        return (*self.claims, *self.rejected_claims)


def deferred(unit, needs="code"):
    """One ``needs-other-evidence`` scope entry, as ``scope_entries`` builds it."""
    return Scoped(
        unit, state="needs-other-evidence", reason=f"needs {needs}", needs=needs
    )


class TestObservingOneRequirement:
    """What the report says about a requirement, read off the payload."""

    def test_a_confirmed_claim_is_a_gap_the_prose_settled(self):
        block = DispositionBlock(claims=[ruling("V1.2.4", "confirmed")])

        observed = observe("V1.2.4", *_surfaces(block))

        assert observed == Observation("confirmed")

    def test_a_needs_info_claim_asks_for_more_of_what_the_job_carries(self):
        block = DispositionBlock(claims=[ruling("V1.2.4", "needs-info")])

        assert observe("V1.2.4", *_surfaces(block)).kind == "needs-info"

    def test_a_rejection_is_read_from_the_array_it_actually_lives_in(self):
        """The bug #472 fixed, pinned on this scorer too."""
        block = DispositionBlock(rejected_claims=[ruling("V1.2.4", "rejected")])

        assert observe("V1.2.4", *_surfaces(block)).kind == "rejected"

    def test_a_deferral_carries_the_kind_of_evidence_it_needs(self):
        entry = deferred("V1.2.4", "code")
        block = DispositionBlock(scope=[entry])

        assert observe("V1.2.4", *_surfaces(block)) == Observation("deferred", "code")

    def test_an_applicable_scope_entry_reads_as_silence(self):
        """The decision #471 left open, made here.

        ``applicable`` is *considered and nothing raised*, which for a listed
        requirement is the miss recall already charges. Reading it as a seventh
        disposition would put one failure in two metrics.
        """
        entry = Scoped("V1.2.4", state="applicable", reason="")
        block = DispositionBlock(scope=[entry])

        assert observe("V1.2.4", *_surfaces(block)).kind == "silent"

    def test_a_requirement_nothing_mentions_is_silent(self):
        assert observe("V1.2.4", *_surfaces(DispositionBlock())).kind == "silent"


def _surfaces(block):
    """The three lookups :func:`observe` takes, built as the scorer builds them."""
    from analysis_service.frameworks.asvs.record import requirement_of

    return (
        {requirement_of(c.id): c for c in block.claims if requirement_of(c.id)},
        {
            requirement_of(c.id): c
            for c in block.rejected_claims
            if requirement_of(c.id)
        },
        {entry.unit: entry for entry in block.scope},
    )


class TestTheCarriedPolicyDecidesWhatSatisfies:
    """``CARRIED_EVIDENCE_KINDS`` is read, never assumed."""

    def test_an_uncarried_kind_must_arrive_as_a_deferral_naming_it(self):
        assert satisfies("needs-code", Observation("deferred", "code"), ("prose",))
        assert not satisfies(
            "needs-code", Observation("deferred", "config"), ("prose",)
        )

    def test_asking_for_more_prose_does_not_satisfy_a_need_for_code(self):
        """The false prose request, at the level of one comparison."""
        assert not satisfies("needs-code", Observation("needs-info"), ("prose",))

    def test_a_carried_kind_is_satisfied_by_the_claim_path_instead(self):
        """A job that carried source would rule a ``needs-code`` proposal.

        The scorer must follow the policy rather than the kind's name, or it
        fails a run for doing the newly correct thing the day the tuple grows.
        """
        carried = ("prose", "code")

        assert satisfies("needs-code", Observation("needs-info"), carried)
        assert not satisfies("needs-code", Observation("deferred", "code"), carried)

    def test_more_prose_is_the_claim_path_because_prose_is_carried(self):
        assert satisfies("needs-more-prose", Observation("needs-info"), ("prose",))

    def test_a_rejection_and_a_not_applicable_entry_both_rule_it_out(self):
        assert satisfies("not-applicable", Observation("rejected"), ("prose",))
        assert satisfies("not-applicable", Observation("not-applicable"), ("prose",))
        assert not satisfies("not-applicable", Observation("confirmed"), ("prose",))


class TestTheRoutingFailures:
    """The two rates the instrument exists to expose."""

    def _scored(self, case, block):
        return score_dispositions(case, block, carried=("prose",))

    def test_asking_for_prose_where_only_code_answers_is_named_as_such(self, case):
        needs_code = _requirement_expecting(case, "needs-code")
        block = DispositionBlock(claims=[ruling(needs_code, "needs-info")])

        score = self._scored(case, block)

        assert [entry.requirement for entry in score.false_prose_requests] == [
            needs_code
        ]
        assert score.false_confirmed == ()

    def test_ruling_from_prose_what_only_code_settles_is_named_apart(self, case):
        needs_code = _requirement_expecting(case, "needs-code")
        block = DispositionBlock(claims=[ruling(needs_code, "confirmed")])

        score = self._scored(case, block)

        assert [entry.requirement for entry in score.false_confirmed] == [needs_code]
        assert score.false_prose_requests == ()

    def test_ruling_out_a_requirement_the_case_says_applies(self, case):
        needs_code = _requirement_expecting(case, "needs-code")
        block = DispositionBlock(rejected_claims=[ruling(needs_code, "rejected")])

        score = self._scored(case, block)

        assert [entry.requirement for entry in score.false_not_applicable] == [
            needs_code
        ]

    def test_the_right_deferral_scores_correct(self, case):
        needs_code = _requirement_expecting(case, "needs-code")
        entry = deferred(needs_code, "code")

        score = self._scored(case, DispositionBlock(scope=[entry]))

        assert [entry.requirement for entry in score.correct] == [needs_code]
        assert score.accuracy == 1.0
        assert score.wrong == ()

    def test_the_wrong_kind_is_a_routing_error_rather_than_a_refusal_error(self, case):
        """Right to refuse, wrong about what would answer."""
        needs_code = _requirement_expecting(case, "needs-code")
        entry = deferred(needs_code, "people")

        score = self._scored(case, DispositionBlock(scope=[entry]))

        assert [entry.requirement for entry in score.wrong_kind] == [needs_code]
        assert score.false_prose_requests == ()
        assert score.false_confirmed == ()


class TestWhatTheRoutingScoreExcludes:
    """Two metrics, and one miss must not move both."""

    def test_a_requirement_the_run_never_mentioned_is_unreached(self, case):
        """Recall already charges it, so accuracy does not see it."""
        score = score_dispositions(case, DispositionBlock(), carried=("prose",))

        assert score.judged == ()
        assert score.accuracy == 0.0
        assert len(score.unreached) == len(case.references["asvs"])

    def test_an_unjudged_record_is_counted_rather_than_scored(self, case):
        """The corpus's own gap reads as a gap, never as a wrong answer."""
        stripped = [
            reference.model_copy(update={"disposition": None})
            for reference in case.references["asvs"]
        ]
        thin = replace_references(case, stripped)

        score = score_dispositions(thin, DispositionBlock(), carried=("prose",))

        assert score.unjudged == len(stripped)
        assert score.judged == ()
        assert score.unreached == ()


def replace_references(case, references):
    """The same case carrying a different ASVS reference set."""
    import dataclasses

    return dataclasses.replace(
        case, references={**case.references, "asvs": tuple(references)}
    )


def _requirement_expecting(case, disposition):
    """One requirement this case expects the given disposition for."""
    return next(
        reference.requirement
        for reference in case.references["asvs"]
        if reference.disposition == disposition
    )


def test_the_pooled_rates_carry_their_own_denominators(case):
    """A rate over an empty denominator reads as a clean run, so it is reported."""
    needs_code = _requirement_expecting(case, "needs-code")
    scores = [
        score_dispositions(
            case,
            DispositionBlock(claims=[ruling(needs_code, "needs-info")]),
            carried=("prose",),
        )
    ]

    totals = pooled_dispositions(scores)

    assert totals["needs_other_evidence"] == 1
    assert totals["false_prose_requests"] == 1
    assert totals["false_prose_request_rate"] == 1.0
    assert totals["accuracy"] == 0.0


def test_evidence_kind_accuracy_counts_what_was_right_and_not_what_was_named(case):
    """A wrong answer that is none of the three named failures is still wrong.

    The rate counted the reachable records left after subtracting the three
    failures it names. A run that *rejected* a requirement needing source code
    falls into none of them, so it landed in the numerator and the routing rate
    read 100% on a record the accuracy figure beside it scored 0%.
    """
    needs_code = _requirement_expecting(case, "needs-code")
    scores = [
        score_dispositions(
            case,
            DispositionBlock(rejected_claims=[ruling(needs_code, "rejected")]),
            carried=("prose",),
        )
    ]

    totals = pooled_dispositions(scores)

    assert totals["needs_other_evidence"] == 1
    assert totals["false_prose_requests"] == 0
    assert totals["false_confirmed"] == 0
    assert totals["wrong_kind"] == 0
    assert totals["false_not_applicable"] == 1
    assert totals["accuracy"] == 0.0
    assert totals["evidence_kind_accuracy"] == 0.0

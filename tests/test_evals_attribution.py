"""The loss-attribution instrument: which stage lost each expected requirement.

The matrix and the routing scorer say what was lost; this says where. Every
scenario below builds the block shape production emits — a rejection in its
own array, a scope entry per unit — and reads the stage off the closed token
the report carries, so the tests are the same set comparisons the scorer is.
"""

from __future__ import annotations

from typing import get_args, get_type_hints

import pytest

from analysis_service.claims import ScopeEntry
from analysis_service.sources import CARRIED_EVIDENCE_KINDS
from evals import verify_corpus
from evals.harness.applicability import (
    EVIDENCE_FOR_DISPOSITION,
    Observation,
    score_applicability,
    score_dispositions,
)
from evals.harness.attribution import (
    STAGE_FOR_OBSERVED,
    STAGE_FOR_SCOPE,
    STAGES,
    artifact,
    attribute_case,
    pooled,
)
from evals.harness.reference import load_corpus
from tests.test_evals_applicability import Block, Scoped, deferred, ruling

CASE_ID = "01-payments-checkout"


@pytest.fixture(scope="module")
def case():
    corpus = load_corpus(verify_corpus.CORPUS_DIR)
    return next(entry for entry in corpus if entry.id == CASE_ID)


def attribute(case, block):
    return attribute_case(
        case, block, score_applicability(case, block), score_dispositions(case, block)
    )


def perfect(case, *, without=(), claims=(), scope=(), rejected_claims=()):
    """The block a run that answers every record right would produce.

    Each record's own satisfying answer, read off the disposition table rather
    than assumed: a gap the prose settles is a ``confirmed`` claim, a need for
    more prose is a ``needs-info`` claim because prose is the carried kind, and
    a need for code, config or a person is a scope entry deferring for it. The
    requirements in ``without`` are left out for the scenario to replace with
    the ``claims``, ``scope`` and ``rejected_claims`` it hands in.
    """
    right_claims, right_scope = [], []
    for reference in case.references["asvs"]:
        requirement = reference.requirement
        if requirement in without:
            continue
        wanted = EVIDENCE_FOR_DISPOSITION.get(reference.disposition)
        if reference.disposition == "gap-from-prose":
            right_claims.append(ruling(requirement, "confirmed"))
        elif wanted in CARRIED_EVIDENCE_KINDS:
            right_claims.append(ruling(requirement, "needs-info"))
        else:
            right_scope.append(deferred(requirement, needs=wanted))
    return Block(
        [*right_claims, *claims],
        scope=[*right_scope, *scope],
        rejected_claims=rejected_claims,
    )


def not_raised(requirement):
    return Scoped(requirement, "not-raised", "")


class TestTheTablesCoverTheirVocabularies:
    """A table keyed by a closed vocabulary must answer for every word in it."""

    def test_every_scope_state_is_charged_to_a_stage(self):
        states = set(get_args(ScopeEntry.model_fields["state"].annotation))
        assert set(STAGE_FOR_SCOPE) == states

    def test_every_observation_of_an_applied_requirement_is_charged_once(self):
        """The kinds this table omits are the ones the matrix already charges."""
        kinds = set(get_args(get_type_hints(Observation)["kind"]))
        charged_by_the_matrix = {"rejected", "not-applicable", "silent"}
        assert set(STAGE_FOR_OBSERVED) == kinds - charged_by_the_matrix

    def test_every_stage_a_table_names_is_a_stage(self):
        named = set(STAGE_FOR_SCOPE.values()) | set(STAGE_FOR_OBSERVED.values())
        assert named <= set(STAGES)


def test_a_perfect_run_charges_nothing(case):
    row = attribute(case, perfect(case))

    assert row.losses == ()
    assert all(row.by_stage[stage] == 0 for stage in STAGES)


class TestAMissIsChargedToTheStageThatLostIt:
    def test_a_not_raised_entry_is_generation(self, case):
        block = perfect(case, without={"V1.2.4"}, scope=[not_raised("V1.2.4")])

        (loss,) = attribute(case, block).losses

        assert (loss.requirement, loss.stage, loss.cause) == (
            "V1.2.4",
            "generation",
            "not-raised",
        )
        assert loss.must_find is True
        assert loss.expected == "needs-code"

    @pytest.mark.parametrize("state", ["undecidable", "not-applicable"])
    def test_a_refused_framework_is_the_precondition(self, case, state):
        scope = [Scoped("V1.2.4", state, "the input never said")]
        block = perfect(case, without={"V1.2.4"}, scope=scope)

        (loss,) = attribute(case, block).losses

        assert (loss.stage, loss.cause, loss.reason) == (
            "precondition",
            state,
            "the input never said",
        )

    def test_a_ruling_rejection_is_the_critic_and_charged_once(self, case):
        """Rejected for evidence, the requirement is both missed and misrouted.

        The matrix counts it missed and the routing scorer observes ``rejected``
        for a record expecting code, which is also wrong. One loss, one row.
        """
        rejected = [ruling("V1.2.4", "rejected", rejected_because="evidence")]
        block = perfect(case, without={"V1.2.4"}, rejected_claims=rejected)

        (loss,) = attribute(case, block).losses

        assert (loss.stage, loss.cause) == ("critic", "evidence")
        assert loss.reason == "the input does not settle it"

    @pytest.mark.parametrize("step", ["reasoning", "lane", "duplicate"])
    def test_a_non_ruling_rejection_is_still_the_critic(self, case, step):
        """The scope lists it ``not-raised``, but a lane raised it (#659)."""
        rejected = [ruling("V1.2.4", "rejected", rejected_because=step)]
        block = perfect(
            case,
            without={"V1.2.4"},
            scope=[not_raised("V1.2.4")],
            rejected_claims=rejected,
        )

        (loss,) = attribute(case, block).losses

        assert (loss.stage, loss.cause) == ("critic", step)

    def test_a_rejection_recorded_before_the_cause_was_a_field_reads_as_evidence(
        self, case
    ):
        rejected = [ruling("V1.2.4", "rejected")]
        rejected[0].verdict.rejected_because = None
        block = perfect(case, without={"V1.2.4"}, rejected_claims=rejected)

        (loss,) = attribute(case, block).losses

        assert (loss.stage, loss.cause) == ("critic", "evidence")

    def test_a_requirement_with_neither_claim_nor_entry_is_named_unlisted(self, case):
        block = perfect(case, without={"V1.2.4"})

        (loss,) = attribute(case, block).losses

        assert (loss.stage, loss.cause, loss.reason) == (
            "generation",
            "unlisted",
            "",
        )


class TestAMisroutedRequirementIsChargedToTheStageThatRoutedIt:
    def test_a_needs_info_claim_where_code_answers_is_the_verdict(self, case):
        block = perfect(
            case, without={"V1.2.4"}, claims=[ruling("V1.2.4", "needs-info")]
        )

        (loss,) = attribute(case, block).losses

        assert (loss.stage, loss.cause) == ("verdict", "needs-info")
        assert loss.reason == "the input does not settle it"
        assert loss.expected == "needs-code"

    def test_a_confirmed_claim_where_config_answers_is_the_verdict(self, case):
        block = perfect(
            case, without={"V6.2.1"}, claims=[ruling("V6.2.1", "confirmed")]
        )

        (loss,) = attribute(case, block).losses

        assert (loss.stage, loss.cause) == ("verdict", "confirmed")

    def test_a_deferral_of_a_gap_the_prose_settles_is_the_deferral(self, case):
        block = perfect(case, without={"V13.2.2"}, scope=[deferred("V13.2.2")])

        (loss,) = attribute(case, block).losses

        assert (loss.stage, loss.cause, loss.reason) == (
            "deferral",
            "deferred",
            "needs code",
        )
        assert loss.expected == "gap-from-prose"

    def test_a_deferral_for_the_wrong_kind_is_the_deferral(self, case):
        block = perfect(
            case, without={"V1.2.4"}, scope=[deferred("V1.2.4", needs="config")]
        )

        (loss,) = attribute(case, block).losses

        assert (loss.stage, loss.cause) == ("deferral", "deferred")

    def test_a_route_the_record_also_accepts_is_no_loss(self, case):
        """V3.3.1 expects config and also accepts code (#673)."""
        block = perfect(case, without={"V3.3.1"}, scope=[deferred("V3.3.1")])

        assert attribute(case, block).losses == ()


def test_pooling_counts_losses_and_the_must_find_ones_apart(case):
    generation = perfect(
        case,
        without={"V1.2.4", "V4.1.1"},
        scope=[not_raised("V1.2.4"), not_raised("V4.1.1")],
    )
    verdict = perfect(case, without={"V6.2.1"}, claims=[ruling("V6.2.1", "needs-info")])
    rows = [attribute(case, generation), attribute(case, verdict)]

    totals = pooled(rows)

    assert totals["cases"] == 2
    assert totals["losses"] == 3
    assert totals["by_stage"] == {
        "precondition": 0,
        "generation": 2,
        "critic": 0,
        "verdict": 1,
        "deferral": 0,
    }
    # V1.2.4 and V6.2.1 are must-find; V4.1.1 is not.
    assert totals["must_find_by_stage"]["generation"] == 1
    assert totals["must_find_by_stage"]["verdict"] == 1


def test_the_artifact_carries_the_rows_and_their_fold(case):
    row = attribute(
        case, perfect(case, without={"V1.2.4"}, scope=[not_raised("V1.2.4")])
    )

    blocks = artifact([row])

    assert set(blocks) == {"attribution", "attribution_aggregate"}
    assert blocks["attribution"][0]["by_stage"]["generation"] == 1
    assert blocks["attribution"][0]["losses"][0]["requirement"] == "V1.2.4"
    assert blocks["attribution_aggregate"]["losses"] == 1
    assert artifact([]) == {"attribution": [], "attribution_aggregate": None}

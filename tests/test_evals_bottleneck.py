"""The ten shapes of #1033, and where each archived miss is charged.

Three groups. The first drives every fixture through the real resolver, the
real gate and the real projection, and holds each one to the survival it was
observed to take — so a schema change, a resolver change or a registry change
that moves one of them fails here rather than quietly rewriting the record the
research file cites.

The second holds the charge's own contract: the stage vocabulary is complete,
a refusal is charged to the code that refused it, and a subject the run's graph
never held is charged apart from one the reading put elsewhere.

The third is the evaluator qualification #1033 asks for that nothing else
covers: the matcher's answer does not depend on the order the reference lists
its rows. Every other item of that list — one-to-one matching, semantic
equivalence under a signed alias, scope, polarity, certainty, exclusivity, an
unreviewed row against an adjudicated one, and a case nobody signed — is driven
by ``tests/test_evals_replay.py``, which is the one reader of those rules.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import get_args

import pytest

from analysis_service.assertions import (
    REGISTRY,
    UNPROJECTED,
    Assertion,
    AssertionCatalog,
    CatalogIssue,
    assertion_id,
)
from analysis_service.factbundle import resolve_bundle
from evals.harness import bottleneck, replay
from evals.harness.alignment import Alignment
from evals.harness.bottleneck import (
    FIXTURES,
    MISS_STAGES,
    Fixture,
    MissStage,
    charge_misses,
    diagnose,
    pooled,
)
from evals.harness.modes import AssertionResult
from evals.harness.reference import load_corpus

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT / "evals" / "corpus"
ARMS = REPO_ROOT / "evals" / "emissions" / "20260917T-arms-luna-pro"

#: What each fixture was observed to do, as the research record states it.
#: ``cause`` is which of the three readings the loss belongs to, and ``met``
#: says whether every wanted fact reached a graph attribute a rule reads.
OBSERVED: dict[str, tuple[str, bool]] = {
    "unknown-placement": ("adapter", False),
    "conflicting-placement": ("adapter", False),
    "store-and-process": ("adapter", False),
    "distinct-subjects": ("consumer", False),
    "same-name": ("schema", False),
    "parallel-interfaces": ("none", True),
    "scope-and-polarity": ("consumer", False),
    "hedge-and-conflict": ("consumer", False),
    "operations-and-classification": ("none", True),
    "failed-correction": ("none", True),
}


def fixture_of(key: str) -> Fixture:
    return next(one for one in FIXTURES if one.key == key)


def signed(case) -> replay.SignedReference:
    """The case's signed reference, refused by name where nobody signed one."""
    reference = replay.signed_reference(CORPUS, case)
    assert reference is not None, f"{case.id} carries no signed reference"
    return reference


class TestEveryFixtureIsReadThreeWays:
    """Each shape, through the schema, the adapter and the consumer."""

    def test_the_table_covers_every_shape_the_ticket_names(self) -> None:
        assert {one.key for one in FIXTURES} == set(OBSERVED)

    @pytest.mark.parametrize("key", sorted(OBSERVED), ids=sorted(OBSERVED))
    def test_each_fixture_keeps_what_it_was_observed_to_keep(self, key: str) -> None:
        found = diagnose(fixture_of(key))
        cause, met = OBSERVED[key]

        assert (found.cause, found.met) == (cause, met)

    def test_every_want_names_a_registered_predicate(self) -> None:
        """A want outside the registry would be a fact no route may assert."""
        for one in FIXTURES:
            for want in one.wants:
                assert want.predicate in REGISTRY

    def test_a_want_with_no_graph_field_names_an_unprojected_predicate(self) -> None:
        """The two spellings of "the graph has no home for this" agree."""
        for one in FIXTURES:
            for want in one.wants:
                assert bool(want.attribute) == (want.predicate not in UNPROJECTED)


class TestTheShapesTheSchemaCannotHold:
    """The two findings that are properties of the System Model itself."""

    def test_two_things_the_sources_name_alike_cannot_both_be_elements(self) -> None:
        """An element ID is a function of the name, and the gate holds it to that."""
        found = diagnose(fixture_of("same-name"))

        assert found.cause == "schema"
        assert not found.direct_holds
        assert all("id-mismatch" in issue for issue in found.direct_issues)
        assert ("mention", "s2", "duplicate-element") in found.lost_rows

    def test_a_scoped_grant_reaches_no_graph_attribute(self) -> None:
        """Every want of this shape stops in the catalog, which is ADR 0036's seam."""
        found = diagnose(fixture_of("scope-and-polarity"))

        assert set(found.survivals.values()) == {"catalog"}
        assert found.cause == "consumer"


class TestTheShapesTheAdapterLoses:
    """The three the schema holds and the facts-first resolver does not."""

    def test_an_unplaced_endpoint_leaves_a_question_and_not_a_defect(self) -> None:
        """ADR 0038 rule 2 reaches the interaction and the fact beside it."""
        found = diagnose(fixture_of("unknown-placement"))
        codes = {code for _, _, code in found.lost_rows}

        assert codes == {"unplaced", "unplaced-endpoint", "unplaced-subject"}
        assert found.direct_holds

    def test_two_sources_placing_one_thing_twice_settle_nothing(self) -> None:
        found = diagnose(fixture_of("conflicting-placement"))

        assert ("mention", "led", "competing-placement") in found.lost_rows
        assert found.cause == "adapter"

    def test_one_named_thing_with_two_roles_is_not_settled_by_code(self) -> None:
        found = diagnose(fixture_of("store-and-process"))

        assert ("mention", "wb", "competing-roles") in found.lost_rows
        assert found.direct_holds, "two elements hold it, under a coined name"


class TestTheShapesThatSurvive:
    """The four that reach a reader, and the one whose correction fails safely."""

    def test_two_interfaces_between_one_pair_stay_separate(self) -> None:
        found = diagnose(fixture_of("parallel-interfaces"))

        assert set(found.survivals.values()) == {"structural"}

    def test_a_disagreement_is_kept_and_derived(self) -> None:
        found = diagnose(fixture_of("hedge-and-conflict"))

        assert found.conflicts == 1

    def test_a_failed_correction_leaves_the_original_row_standing(self) -> None:
        found = diagnose(fixture_of("failed-correction"))

        assert found.rolled_back is True
        assert set(found.survivals.values()) == {"structural"}

    def test_the_account_and_the_person_stay_two_subjects(self) -> None:
        fixture = fixture_of("distinct-subjects")
        resolution = resolve_bundle(fixture.bundle, fixture.sources)
        subjects = {entry.subject for entry in resolution.record.catalog.entries}

        assert "principal:batch-runner" in subjects
        assert "principal:priya" in subjects


class TestChargingAnArchivedMiss:
    """Where a missed reference row stopped, over emissions already paid for."""

    @pytest.fixture(scope="class")
    def corpus(self):
        return load_corpus(CORPUS)

    @pytest.fixture(scope="class")
    def charged(self, corpus):
        specs = [
            ("A", 0, ARMS / "arm-A.json"),
            ("B", 0, ARMS / "arm-B2.json"),
            ("E", 0, ARMS / "arm-E.json"),
        ]
        return charge_misses(specs, corpus, CORPUS)[0]

    def test_the_stage_vocabulary_is_the_literal(self) -> None:
        assert MISS_STAGES == get_args(MissStage)

    def test_every_charge_names_one_stage(self, charged) -> None:
        assert charged
        assert sum(pooled(charged).values()) == len(charged)

    def test_no_archived_miss_is_charged_to_a_refusal(self, charged) -> None:
        """The measurement #1033's second mechanism turns on, over three arms.

        A recorded observation rather than a rule: if a later archive charges a
        refusal, this fails and the research file's finding moves with it.
        """
        assert pooled(charged)["refused"] == 0

    def test_the_production_arm_loses_nothing_to_a_graph_that_held_nothing(
        self, charged
    ) -> None:
        unmodelled = [
            charge
            for charge in charged
            if charge.stage == "unmodelled" and charge.arm == "A"
        ]

        assert unmodelled == []

    def test_a_refused_row_is_charged_to_the_refusal(self, corpus) -> None:
        """The negative control: a stage nothing in the archive reaches."""
        case = next(one for one in corpus if one.id == "01-payments-checkout")
        reference = signed(case)
        wanted = next(
            entry
            for entry in reference.entries
            if entry.basis == "stated" and entry.value != "unknown"
        )
        result = _refused_result(wanted)
        fate = replay.ReferenceRowFate(assertion_id(wanted), "omitted")

        stage, why = bottleneck.charge_row(reference, result, fate, Alignment.empty())

        assert (stage, why) == ("refused", "unverifiable-span")

    def test_a_predicate_nobody_proposed_is_charged_to_the_reading(
        self, corpus
    ) -> None:
        case = next(one for one in corpus if one.id == "01-payments-checkout")
        reference = signed(case)
        wanted = next(
            entry
            for entry in reference.entries
            if entry.basis == "stated" and entry.value != "unknown"
        )
        result = _empty_result()
        fate = replay.ReferenceRowFate(assertion_id(wanted), "omitted")

        stage, _ = bottleneck.charge_row(reference, result, fate, Alignment.empty())

        assert stage == "unread"


class TestOneRelaxedConstraint:
    """The replay #1033 asks for: the same emissions, one constraint moved."""

    @pytest.fixture(scope="class")
    def corpus(self):
        return load_corpus(CORPUS)

    @pytest.fixture(scope="class")
    def relaxed(self, corpus):
        specs = [
            ("A", 0, ARMS / "arm-A.json"),
            ("B", 0, ARMS / "arm-B2.json"),
            ("E", 0, ARMS / "arm-E.json"),
        ]
        return bottleneck.relax(specs, corpus, CORPUS)

    def test_every_arm_is_scored_on_the_same_denominator(self, relaxed) -> None:
        assert {one.required for one in relaxed} == {45}

    def test_relaxing_the_name_recovers_rows_and_introduces_errors(
        self, relaxed
    ) -> None:
        """Both halves, because a gain reported alone is not a measurement."""
        production = next(one for one in relaxed if one.arm == "A")

        assert production.recovered > 0
        assert production.introduced > 0

    def test_a_signed_alias_still_wins(self, corpus) -> None:
        """The relaxation only adds a pairing; it never overrides a ruling."""
        case = next(one for one in corpus if one.id == "01-payments-checkout")
        reference = signed(case)
        alignment = bottleneck.align(case, case.model)
        moved = bottleneck.relaxed_reference(reference, alignment)

        for produced, ruled in reference.subject_aliases.items():
            assert moved.subject_aliases[produced] == ruled


class TestTheMatcherDoesNotReadTheReferenceInOrder:
    """The one qualification item nothing else drives: order invariance.

    The audit under #1003 found the strict matcher's score turning on the order
    the reference happened to list its rows, and
    :func:`~evals.harness.replay._assigned` was written to answer it. This is
    the property rather than the mechanism: shuffle the reference and the fates
    must not move.
    """

    @pytest.fixture(scope="class")
    def corpus(self):
        return load_corpus(CORPUS)

    @pytest.mark.parametrize("seed", (1, 7, 1033))
    def test_shuffling_the_reference_does_not_move_one_fate(
        self, corpus, seed: int
    ) -> None:
        from evals.harness.bundle import heads_from_reports

        case = next(one for one in corpus if one.id == "01-payments-checkout")
        reference = signed(case)
        result = heads_from_reports(ARMS / "arm-A.json", [case])[case.id]
        straight = replay.replay_assertions(case, reference, result)

        shuffled = list(reference.entries)
        random.Random(seed).shuffle(shuffled)
        moved = replay.SignedReference(
            catalog=AssertionCatalog(
                registry_version=reference.catalog.registry_version,
                subjects=list(reference.catalog.subjects),
                entries=shuffled,
            ),
            subject_aliases=reference.subject_aliases,
            qualifier_aliases=reference.qualifier_aliases,
        )
        again = replay.replay_assertions(case, reference=moved, result=result)

        assert dict(again.counts) == dict(straight.counts)
        assert {row.reference: row.fate for row in again.rows} == {
            row.reference: row.fate for row in straight.rows
        }


def _proposal(wanted: Assertion) -> dict[str, list[dict[str, str]]]:
    """A proposal holding one row on the wanted subject and predicate."""
    return {
        "assertions": [
            {
                "subject_type": "component",
                "subject": wanted.subject,
                "predicate": wanted.predicate,
                "value": wanted.value,
                "basis": "stated",
            }
        ]
    }


def _refused_result(wanted: Assertion) -> AssertionResult:
    """A run that proposed the wanted row and whose gate refused it."""
    return AssertionResult(
        case_id="synthetic",
        proposal=_proposal(wanted),
        catalog=AssertionCatalog(),
        issues=(
            CatalogIssue(
                code="unverifiable-span",
                message="the quote is in no source",
                row=0,
            ),
        ),
    )


def _empty_result() -> AssertionResult:
    """A run that proposed nothing at all."""
    return AssertionResult(
        case_id="synthetic",
        proposal={"assertions": []},
        catalog=AssertionCatalog(),
        issues=(),
    )

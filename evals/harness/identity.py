"""Claim identity decided by the fields a claim carries, with no model call.

[#201](https://github.com/mstarks01/work-agent/issues/201) proposes resolving a
**Claim** to the parts that decide its identity, so two spellings of one threat
compare equal without the judge. This is that rule, built to the ``Judge``
protocol so that :func:`~evals.harness.calibration.measure_agreement` scores it
against the same recorded labels, on the same 90% bar, as the pinned judge. One
scoreboard, two answers to one question.

**What it reads, and what it does not.** #201 names four parts. Three are on the
record — the lane, the affected **Element** IDs and the **Grounds** — and
``mechanism`` is the only new one. Of those three, the lane is already equal on
every pair a scorer or a calibration set builds, because both only ever pair
within a lane, and a reference claim carries no grounds to compare. So on this
data the rule is element-set equality, and saying so plainly is the point: a
number produced here measures how far element agreement alone goes, and it is
not a rehearsal of the model #201 sketches.

**Set equality, never overlap.** ``affected_element_ids`` is a list whose order
no rule reads, so the comparison sorts. Relaxing equality to a shared element is
the obvious way to buy back a paraphrase that named the flow where the reference
named the process, and ``tests/test_claim_identity.py`` records what it costs on
the blessed corpus: an order of magnitude more claims merge, every one of them a
pair the corpus records as a distinct claim.

**A Trust Boundary is dropped before the comparison.** It is an Element with an
Element ID like any other, and the reference sets cite one in 431 citations —
a zone is the context a claim sits in rather than the thing the claim is about.
Comparing on a citation that arbitrary is noise, and dropping it costs nothing
the corpus can show.

:func:`endpoint_form` is the looser comparison the frontier in
``tests/test_evals_identity.py`` is measured over. It is **not** what
:class:`MechanicalIdentity` answers with, because on its own it merges far more
than it recovers; it exists so the trade-off is a number rather than an opinion.

Nothing here is a judge replacement. The judge stays the decider until the
numbers say otherwise, which is #201's own third bullet.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from evals.harness.judge import BucketRuling, ClaimPair, ClaimRuling, UnmatchedThreat
from evals.harness.verbs import same_action
from stride_service.system_model import SystemModel

#: One case's **Data Flow**s as ``flow id -> (source id, destination id)``.
FlowMap = Mapping[str, tuple[str, str]]


class IdentityError(ValueError):
    """A pair carries no candidate elements, so the rule cannot answer it."""


def comparable_elements(element_ids: Iterable[str]) -> frozenset[str]:
    """The cited **Element**s the identity comparison reads: every one but a zone."""
    return frozenset(
        element_id
        for element_id in element_ids
        if not element_id.startswith("boundary:")
    )


def endpoint_form(element_ids: Iterable[str], flows: FlowMap) -> frozenset[str]:
    """Every cited **Data Flow** replaced by the two **Element**s it runs between.

    One place in the graph, spelled one way. The recorded labels carry the same
    finding cited as a flow by one writer and as the process at the end of that
    flow by another — ``evals/BLESSING.md`` step 5 calls that an element-agreement
    difference and labels the pair a match — and this is what makes those two
    citations equal without asking anybody.

    It reads the flow map rather than parsing an **Element ID**, because a flow's
    ID spells its endpoints by *name* and two elements of different types may
    legally carry one name inside a **System Model**.
    """
    resolved: set[str] = set()
    for element_id in comparable_elements(element_ids):
        endpoints = flows.get(element_id)
        if endpoints is None:
            resolved.add(element_id)
        else:
            resolved.update(endpoints)
    return frozenset(resolved)


def endpoint_subset(
    left_ids: Iterable[str], right_ids: Iterable[str], flows: FlowMap
) -> bool:
    """Does one claim's **Element** set contain the other's, once resolved?

    The loosest element rule that is still usable, and the frontier in
    ``tests/test_evals_identity.py`` is what says so: 14 false splits over the
    200 labelled pairs against ``equality``'s 89, at the price of 23 false
    merges over 287 reference pairs against ``equality``'s 1.

    Subset rather than equality because the two sides are written at different
    grain — one names the flow and the process it ends at, the other names only
    the process — and neither is wrong. Subset accepts that; equality calls it a
    different claim.

    On its own this over-merges, which is why nothing calls it alone:
    :class:`SubsetVerbIdentity` is the rule, and this is one half of it.
    """
    left = endpoint_form(left_ids, flows)
    right = endpoint_form(right_ids, flows)
    return left <= right or right <= left


class SubsetVerbIdentity:
    """Endpoint subset **and** one action: the rule #201 argues for.

    Each half fails alone and the pair does not. Elements alone cannot separate
    a read from a write against one store, and a verb alone cannot separate two
    reads of two different stores. Together they answer both, and the
    measurement says how far: over the 23 reference-claim pairs ``endpoint
    subset`` wrongly merges, the vocabulary separates 20, and
    :data:`~evals.harness.verbs.UNSEPARATED` names the three it does not with
    the reason for each.

    Built to the ``Judge`` protocol like :class:`MechanicalIdentity`, so
    :func:`~evals.harness.calibration.measure_agreement` scores it against the
    same recorded labels on the same bar. Two rules, one scoreboard.

    **It refuses a pair carrying no verb rather than guessing one.** Treating an
    absent verb as a wildcard would quietly grade the element half alone and
    report the number as this rule's.

    The **Data Flow** map is held per instance rather than passed per call, so
    the signature matches the ``Judge`` protocol and one comparison can score
    this rule beside the pinned judge. It is per case for the same reason
    :class:`~evals.harness.judge.MemoJudge` is scoped per case: two cases may
    spell one flow ID differently, and a shared map would resolve one case's
    citation against another's graph.
    """

    def __init__(self, flows_by_case: Mapping[str, FlowMap]) -> None:
        self._flows = flows_by_case

    def equivalent(self, pair: ClaimPair) -> ClaimRuling:
        if pair.candidate_element_ids is None:
            raise IdentityError(
                f"{pair.case}: no element IDs are assigned to candidate claim"
                f" {pair.candidate_claim!r}, so this rule has nothing to compare"
            )
        if pair.reference_verb is None or pair.candidate_verb is None:
            raise IdentityError(
                f"{pair.case}: one side carries no action verb, so the verb half"
                " of this rule cannot answer; assign both from"
                " evals.harness.verbs, or score MechanicalIdentity instead"
            )
        elements = endpoint_subset(
            pair.reference_element_ids,
            pair.candidate_element_ids,
            self._flows.get(pair.case, {}),
        )
        action = same_action(pair.reference_verb, pair.candidate_verb)
        return ClaimRuling(
            match=elements and action,
            rationale=(
                f"elements {'overlap' if elements else 'disjoint'};"
                f" {pair.reference_verb} vs {pair.candidate_verb}"
                f" {'is one action' if action else 'are two actions'}"
            ),
        )

    def adjudicate(
        self,
        threat: UnmatchedThreat,
        system_model: SystemModel,
        sibling_claims: tuple[str, ...],
    ) -> BucketRuling:
        """Refused, for the reason :class:`MechanicalIdentity` refuses it.

        Bucketing an unmatched threat asks whether the **System Model** supports
        a claim nobody wrote down. No comparison of fields answers that, and a
        made-up answer would put a meaningless number into a metric.
        """
        del threat, system_model, sibling_claims
        raise IdentityError(
            "this rule compares claims and cannot bucket an unmatched threat;"
            " that call needs the judge"
        )


class MechanicalIdentity:
    """The identity rule as a ``Judge``, so one comparison scores both.

    Answers ``equivalent`` and refuses ``adjudicate``. Bucketing an unmatched
    threat asks whether the **System Model** supports a claim nobody wrote down,
    which is judgement about prose and not a comparison of fields — there is no
    mechanical answer to give, and returning a made-up one would put a number
    into a metric that means nothing.
    """

    def equivalent(self, pair: ClaimPair) -> ClaimRuling:
        if pair.candidate_element_ids is None:
            raise IdentityError(
                f"{pair.case}: no element IDs are assigned to candidate claim"
                f" {pair.candidate_claim!r}, so mechanical identity has nothing"
                " to compare; assign them in build_pairs.py or exclude the pair"
            )
        reference = sorted(comparable_elements(pair.reference_element_ids))
        candidate = sorted(comparable_elements(pair.candidate_element_ids))
        return ClaimRuling(
            match=reference == candidate,
            rationale=f"reference elements {reference}; candidate {candidate}",
        )

    def adjudicate(
        self,
        threat: UnmatchedThreat,
        system_model: SystemModel,
        sibling_claims: tuple[str, ...],
    ) -> BucketRuling:
        del threat, system_model, sibling_claims
        raise IdentityError(
            "mechanical identity compares claims and cannot bucket an unmatched"
            " threat; that call needs the judge"
        )

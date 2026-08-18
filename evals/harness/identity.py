"""Claim identity decided by the fields a claim carries, with no model call.

[#201](https://github.com/mstarks01/work-agent/issues/201) proposes resolving a
**Claim** to the parts that decide its identity, so two spellings of one threat
compare equal without the judge. This is that rule, built to the ``Judge``
protocol so that :func:`~evals.harness.calibration.measure_agreement` scores it
against the same hand labels, on the same 90% bar, as the pinned judge. One
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
pair a reviewer ruled distinct.

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

    One place in the graph, spelled one way. The hand labels record the same
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
